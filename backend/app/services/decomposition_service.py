"""Validated, atomic conversion of an approved Spec into child-Agent Specs.

The PM Agent proposes local keys only.  This boundary validates every proposal
before writes, assigns server IDs, and never starts a child Agent.
"""

import asyncio
import hashlib
import json
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable, Mapping
from uuid import uuid4

from sqlalchemy.orm import Session

from app.agents.gateway import AgentGateway
from app.agents.progress import report_agent_progress
from app.database.models import AgentCall, AgentSession, AgentSpec, AuditEvent, Project, SdlcRouteDecision, SpecVersion, WorkItem, WorkItemDependency
from app.domain.implementation_plan import ImplementationPlan
from app.domain.types import AgentSpecProposal, ProjectPhase, ProjectSpecPayload, ReviewVerdict, SemanticReview, SpecStatus, WorkBreakdown, WorkItemKind
from app.services.task_plan_graph import (
    dependency_contracts,
    topological_plan_waves,
    transitive_dependent_keys,
)
from app.services.task_specifications import ImplementationPlanError, requirement_snapshots, validate_implementation_plan
from app.services.sdlc_rules import LifecycleError, planning_context, task_lifecycle_context, validate_lifecycle


_DECOMPOSITION_CONTRACT_VERSION = 6
_MAX_SEMANTIC_REVIEW_ATTEMPTS = 5


class BreakdownValidationError(ValueError):
    """A stable, model-attributable work-breakdown validation failure."""

    def __init__(self, code: str, local_key: str, message: str) -> None:
        self.code, self.local_key = code, local_key
        super().__init__(f"{code} [{local_key}]: {message}")


class DecompositionNotAllowed(RuntimeError):
    """The current project Spec is not eligible for decomposition."""


class DecompositionAgentFailure(RuntimeError):
    """An external PM result is unavailable, with its durable call evidence."""

    def __init__(self, message: str, agent_call_id: str) -> None:
        self.agent_call_id = agent_call_id
        self.agent_call_ids = [agent_call_id]
        super().__init__(message)


class BreakdownReviewerFailure(RuntimeError):
    """The semantic Reviewer failed after PM evidence was made durable."""

    def __init__(self, message: str, agent_call_ids: list[str]) -> None:
        self.agent_call_ids = list(agent_call_ids)
        self.agent_call_id = self.agent_call_ids[-1] if self.agent_call_ids else None
        super().__init__(message)


class DecompositionPlanningFailure(BreakdownReviewerFailure):
    """At least one independently persisted task-plan call failed."""


class PreparedBreakdownValidationError(BreakdownValidationError):
    """A stable proposal error associated with the exact PM call that produced it."""

    def __init__(self, error: BreakdownValidationError, agent_call_ids: list[str]) -> None:
        self.agent_call_ids = list(agent_call_ids)
        self.agent_call_id = self.agent_call_ids[-1]
        super().__init__(error.code, error.local_key, str(error).split("]: ", 1)[-1])


class SemanticReviewRejected(BreakdownValidationError):
    """A completed Reviewer response rejected the proposed breakdown."""

    def __init__(self, error: BreakdownValidationError, agent_call_ids: list[str]) -> None:
        self.agent_call_ids = list(agent_call_ids)
        self.agent_call_id = self.agent_call_ids[-1]
        super().__init__(error.code, error.local_key, str(error).split("]: ", 1)[-1])


@dataclass(frozen=True, slots=True)
class ApprovedSpecSnapshot:
    id: str
    content: Mapping[str, object]
    input_refs: tuple[str, ...]
    content_hash: str


@dataclass(frozen=True, slots=True)
class PreparedDecomposition:
    project_id: str
    approved_spec: ApprovedSpecSnapshot
    breakdown: WorkBreakdown
    pm_agent_call_id: str
    plan_agent_call_ids: tuple[str, ...]
    reviewer_agent_call_id: str

    @property
    def agent_call_ids(self) -> list[str]:
        return [
            self.pm_agent_call_id,
            *self.plan_agent_call_ids,
            self.reviewer_agent_call_id,
        ]

    @property
    def agent_call_id(self) -> str:
        """Compatibility alias for callers that only need the PM call."""
        return self.pm_agent_call_id


def _new_id() -> str:
    return str(uuid4())


def _now() -> datetime:
    return datetime.now(UTC)


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _review_fingerprint(review: SemanticReview) -> str:
    """Identify equivalent semantic feedback independent of finding order."""

    findings = sorted(
        (
            {
                "code": finding.code.strip(),
                "severity": finding.severity.strip().upper(),
                "spec_path": finding.spec_path.strip(),
                "message": finding.message.strip(),
                "suggested_resolution": finding.suggested_resolution.strip(),
                "blocks_progress": finding.blocks_progress,
            }
            for finding in review.findings
        ),
        key=lambda finding: (
            finding["code"],
            finding["spec_path"],
            finding["message"],
            finding["suggested_resolution"],
        ),
    )
    return _canonical_hash({"verdict": review.verdict.value, "findings": findings})


def _trimmed(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _spec_content(spec: SpecVersion | ProjectSpecPayload | ApprovedSpecSnapshot | Mapping[str, object]) -> Mapping[str, object]:
    if isinstance(spec, SpecVersion):
        return spec.content
    if isinstance(spec, ProjectSpecPayload):
        return spec.model_dump(mode="json")
    if isinstance(spec, ApprovedSpecSnapshot):
        return spec.content
    return spec


def _spec_input_refs(spec: SpecVersion | ProjectSpecPayload | ApprovedSpecSnapshot | Mapping[str, object]) -> set[str]:
    if isinstance(spec, SpecVersion):
        return set(spec.input_refs)
    if isinstance(spec, ApprovedSpecSnapshot):
        return set(spec.input_refs)
    content = _spec_content(spec)
    return {item for item in content.get("source_refs", []) if isinstance(item, str)}


def _error(code: str, local_key: object, message: str) -> None:
    raise BreakdownValidationError(code, str(local_key), message)


def _validate_nonempty_text(value: object, code: str, local_key: str, label: str) -> None:
    if not _trimmed(value):
        _error(code, local_key, f"{label} must not be blank")


def _field(value: object, name: str) -> object:
    return value.get(name) if isinstance(value, Mapping) else getattr(value, name, None)


def _validate_questions(questions: list[Any], local_key: str) -> None:
    for question in questions:
        _validate_nonempty_text(question.question, "INVALID_OPEN_QUESTION", local_key, "question")
        if question.blocking:
            _error("BLOCKING_OPEN_QUESTION", local_key, "blocking open questions must be resolved")
        if not _trimmed(question.risk_owner) or not _trimmed(question.accepted_consequence):
            _error("UNACCEPTED_RISK", local_key, "non-blocking question requires risk_owner and accepted_consequence")


def _validate_acceptance(
    criteria: list[Any], local_key: str, allowed_requirement_ids: set[str]
) -> None:
    if not criteria:
        _error("MISSING_ACCEPTANCE", local_key, "an executable acceptance criterion is required")
    for criterion in criteria:
        for requirement_id in criterion.requirement_ids:
            _validate_nonempty_text(
                requirement_id, "INVALID_ACCEPTANCE", local_key, "requirement_id"
            )
            if requirement_id not in allowed_requirement_ids:
                _error(
                    "UNKNOWN_REQUIREMENT_ID",
                    local_key,
                    f"acceptance requirement {requirement_id!r} is not in the approved Spec",
                )
        _validate_nonempty_text(criterion.criterion, "INVALID_ACCEPTANCE", local_key, "criterion")
        _validate_nonempty_text(criterion.verification_method, "INVALID_ACCEPTANCE", local_key, "verification_method")
        _validate_nonempty_text(criterion.expected_result, "INVALID_ACCEPTANCE", local_key, "expected_result")


def _kahn_leftovers(edges: Mapping[str, set[str]]) -> set[str]:
    """Run iterative Kahn topological reduction and return only its unprocessed keys."""
    inbound = {key: len(dependencies) for key, dependencies in edges.items()}
    successors = {key: set() for key in edges}
    for item, dependencies in edges.items():
        for dependency in dependencies:
            successors[dependency].add(item)
    ready = deque(sorted(key for key, count in inbound.items() if count == 0))
    while ready:
        key = ready.popleft()
        for successor in sorted(successors[key]):
            inbound[successor] -= 1
            if inbound[successor] == 0:
                ready.append(successor)
    return {key for key, count in inbound.items() if count > 0}


def _cycle_participant_iterative(edges: Mapping[str, set[str]], candidates: set[str]) -> str:
    """Extract a real cycle member from Kahn leftovers without recursive traversal."""
    state = {key: 0 for key in edges}
    for start in sorted(candidates):
        if state[start] != 0:
            continue
        path: list[str] = [start]
        position = {start: 0}
        state[start] = 1
        frames: list[tuple[str, object]] = [(start, iter(sorted(edges[start])))]
        while frames:
            key, iterator = frames[-1]
            try:
                dependency = next(iterator)
            except StopIteration:
                state[key] = 2
                position.pop(key, None)
                path.pop()
                frames.pop()
                continue
            if dependency not in candidates:
                continue
            if state[dependency] == 1:
                return min(path[position[dependency]:])
            if state[dependency] == 0:
                state[dependency] = 1
                position[dependency] = len(path)
                path.append(dependency)
                frames.append((dependency, iter(sorted(edges[dependency]))))
    raise RuntimeError("Kahn reported a cycle but no cycle participant was found")


def validate_breakdown(
    breakdown: WorkBreakdown,
    approved_spec: SpecVersion | ProjectSpecPayload | ApprovedSpecSnapshot | Mapping[str, object],
    *, require_implementation_plan: bool = True,
    require_lifecycle: bool = False,
) -> None:
    """Reject unsafe model output before a database transaction is opened."""
    proposals = [*breakdown.milestones, *breakdown.tasks]
    spec_content = _spec_content(approved_spec)
    allowed_requirement_ids = {
        str(_field(requirement, "requirement_id"))
        for section in ("functional_requirements", "non_functional_requirements")
        for requirement in spec_content.get(section, [])
    }
    by_key: dict[str, Any] = {}
    for proposal in proposals:
        if not _trimmed(proposal.local_key) or proposal.local_key != proposal.local_key.strip():
            _error("INVALID_LOCAL_KEY", proposal.local_key, "local_key must be non-blank and must not have outer whitespace")
        if proposal.local_key in by_key:
            _error("DUPLICATE_LOCAL_KEY", proposal.local_key, "milestone and task keys must be globally unique")
        by_key[proposal.local_key] = proposal
        _validate_nonempty_text(proposal.title, "INVALID_WORK_ITEM", proposal.local_key, "title")
        _validate_nonempty_text(proposal.objective, "INVALID_WORK_ITEM", proposal.local_key, "objective")

    for proposal in breakdown.milestones:
        if proposal.kind is not WorkItemKind.MILESTONE:
            _error("INVALID_MILESTONE_KIND", proposal.local_key, "milestones must have kind MILESTONE")
        if proposal.parent_key is not None:
            _error("INVALID_PARENT", proposal.local_key, "milestones must be top-level")
    for proposal in breakdown.tasks:
        if proposal.kind is not WorkItemKind.TASK:
            _error("INVALID_TASK_KIND", proposal.local_key, "tasks must have kind TASK")
        if proposal.parent_key is None or proposal.parent_key not in by_key:
            _error("UNKNOWN_PARENT", proposal.local_key, "each task must name a proposed milestone parent")
        if by_key[proposal.parent_key].kind is not WorkItemKind.MILESTONE:
            _error("INVALID_PARENT", proposal.local_key, "tasks must be leaf children of milestones")

    for proposal in proposals:
        if proposal.parent_key is not None and proposal.parent_key not in by_key:
            _error("UNKNOWN_PARENT", proposal.local_key, "parent key is not in the proposed breakdown")
        if len(proposal.dependency_keys) != len(set(proposal.dependency_keys)):
            _error("DUPLICATE_DEPENDENCY", proposal.local_key, "dependency keys must be unique")
        for dependency_key in proposal.dependency_keys:
            if not _trimmed(dependency_key):
                _error("INVALID_DEPENDENCY", proposal.local_key, "dependency key must not be blank")
            if dependency_key not in by_key:
                _error("UNKNOWN_DEPENDENCY", proposal.local_key, f"dependency {dependency_key!r} is not proposed")
            if (
                proposal.kind is WorkItemKind.TASK
                and by_key[dependency_key].kind is not WorkItemKind.TASK
            ):
                _error(
                    "INVALID_DEPENDENCY_TARGET",
                    proposal.local_key,
                    f"dependency {dependency_key!r} must target an executable task",
                )

    dependency_edges = {proposal.local_key: set(proposal.dependency_keys) for proposal in proposals}
    leftovers = _kahn_leftovers(dependency_edges)
    if leftovers:
        cycle_key = _cycle_participant_iterative(dependency_edges, leftovers)
        _error("DEPENDENCY_CYCLE", cycle_key, "dependency graph must be acyclic")

    specs_by_key: dict[str, AgentSpecProposal] = {}
    allowed_refs = _spec_input_refs(approved_spec)
    for proposal in breakdown.agent_specs:
        key = proposal.work_item_key
        if key not in by_key:
            _error("UNKNOWN_WORK_ITEM", key, "AgentSpec must target a proposed task")
        if by_key[key].kind is not WorkItemKind.TASK:
            _error("MILESTONE_AGENT_SPEC", key, "milestones cannot receive AgentSpecs")
        if key in specs_by_key:
            _error("DUPLICATE_AGENT_SPEC", key, "a task may have exactly one AgentSpec")
        specs_by_key[key] = proposal
        _validate_nonempty_text(proposal.objective, "INVALID_AGENT_SPEC", key, "objective")
        for field_name in (
            "scope",
            "exclusions",
            "context_refs",
            "inputs",
            "fixed_constraints",
            "configurable_parts",
            "extension_points",
            "required_skills",
            "allowed_tools",
            "allowed_paths",
            "test_obligations",
            "risks",
        ):
            for value in getattr(proposal, field_name):
                _validate_nonempty_text(
                    value,
                    "INVALID_AGENT_SPEC_FIELD",
                    key,
                    f"{field_name} entry",
                )
        for field_name in ("responsible_role", "suggested_assignee"):
            _validate_nonempty_text(
                getattr(proposal, field_name),
                "INVALID_AGENT_SPEC_FIELD",
                key,
                field_name,
            )
        for output in proposal.outputs:
            _validate_nonempty_text(_field(output, "name"), "INVALID_OUTPUT", key, "output name")
            _validate_nonempty_text(_field(output, "format"), "INVALID_OUTPUT", key, "output format")
        if not proposal.outputs or not any(_field(output, "required") is True for output in proposal.outputs):
            _error("MISSING_OUTPUT", key, "each task must have at least one required output")
        _validate_acceptance(proposal.acceptance_criteria, key, allowed_requirement_ids)
        _validate_questions(proposal.open_questions, key)
        for reference in proposal.context_refs:
            # References are identifiers: validate exact membership rather than trimming them.
            if reference not in allowed_refs:
                _error("INVALID_SOURCE_REF", key, f"context reference {reference!r} is not an approved version input")
        if len(proposal.dependency_keys) != len(set(proposal.dependency_keys)):
            _error("DUPLICATE_DEPENDENCY", key, "dependency keys must be unique")
        for dependency_key in proposal.dependency_keys:
            if not _trimmed(dependency_key):
                _error("INVALID_DEPENDENCY", key, "dependency key must not be blank")
            if dependency_key not in by_key:
                _error("UNKNOWN_DEPENDENCY", key, f"dependency {dependency_key!r} is not proposed")
            if by_key[dependency_key].kind is not WorkItemKind.TASK:
                _error(
                    "INVALID_DEPENDENCY_TARGET",
                    key,
                    f"dependency {dependency_key!r} must target an executable task",
                )
        if set(proposal.dependency_keys) != set(by_key[key].dependency_keys):
            _error("DEPENDENCY_MISMATCH", key, "AgentSpec dependencies must match its task prerequisites")

    missing = set(by_key) - {item.local_key for item in breakdown.milestones} - set(specs_by_key)
    if missing:
        _error("MISSING_AGENT_SPEC", sorted(missing)[0], "each task needs exactly one AgentSpec")

    covered_ids = {
        requirement_id
        for proposal in breakdown.agent_specs
        for criterion in proposal.acceptance_criteria
        for requirement_id in criterion.requirement_ids
    }
    uncovered = allowed_requirement_ids - covered_ids
    if uncovered:
        _error("MISSING_ACCEPTANCE_COVERAGE", "agent_specs",
               f"requirements without task acceptance coverage: {', '.join(sorted(uncovered))}")

    try:
        validate_lifecycle(breakdown, dict(spec_content), required=require_lifecycle)
    except LifecycleError as error:
        _error(error.code, "lifecycle", str(error))

    for proposal in breakdown.agent_specs:
        if proposal.implementation_plan is None and not require_implementation_plan:
            continue
        try:
            validate_implementation_plan(proposal.implementation_plan, proposal, allowed_requirement_ids)
        except ImplementationPlanError as error:
            _error(error.code, proposal.work_item_key, str(error))


def _normalize_text(value: str) -> str:
    return " ".join(value.strip().split())


def _normalized_agent_spec_content(proposal: AgentSpecProposal, *, work_item_id: str, dependency_ids: list[str], spec_id: str, approved_spec: Mapping[str, object], lifecycle=None) -> dict[str, object]:
    """Canonicalize human-readable fields but retain identifiers as exact validated values."""
    content = proposal.model_dump(mode="json")
    content.pop("work_item_key", None)
    content.pop("dependency_keys", None)
    for field in ("objective", "responsible_role", "suggested_assignee"):
        content[field] = _normalize_text(content[field])
    for field in ("scope", "exclusions", "inputs", "fixed_constraints", "configurable_parts", "extension_points", "required_skills", "allowed_tools", "allowed_paths", "test_obligations", "risks"):
        content[field] = [_normalize_text(value) for value in content[field]]
    for output in content["outputs"]:
        output["name"] = _normalize_text(output["name"])
        output["format"] = _normalize_text(output["format"])
    for criterion in content["acceptance_criteria"]:
        for field in ("criterion", "verification_method", "expected_result"):
            criterion[field] = _normalize_text(criterion[field])
    for question in content["open_questions"]:
        for field in ("question", "risk_owner", "accepted_consequence"):
            if question[field] is not None:
                question[field] = _normalize_text(question[field])
    content["work_item_id"] = work_item_id
    content["dependency_work_item_ids"] = dependency_ids
    content["source_spec_version_id"] = spec_id
    content["requirements"] = requirement_snapshots(proposal, dict(approved_spec))
    if lifecycle is not None:
        content["sdlc"] = task_lifecycle_context(lifecycle, proposal.work_item_key)
        content["fixed_constraints"].insert(
            0, f'SDLC 生命周期：{content["sdlc"]["model_rules"]["name"]}；'
            '按选定路线的阶段、门禁、并行、缺陷回退与变更规则执行；计划不代表已获审批。'
        )
    return json.loads(json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False))


class DecompositionService:
    """Obtain, validate, and atomically persist a PM's child-Agent Spec plan."""

    def __init__(self, session_factory: Callable[[], Session], agent: AgentGateway) -> None:
        self._session_factory, self._agent = session_factory, agent
        self._planning_semaphore = asyncio.Semaphore(2)

    async def convert(self, project_id: str) -> list[AgentSpec]:
        prepared = await self.prepare(project_id)
        try:
            return self._persist_direct(prepared)
        except Exception as error:
            self._record_failures(prepared.project_id, prepared.agent_call_ids, error)
            raise

    async def prepare(
        self,
        project_id: str,
        *,
        command_id: str | None = None,
        input_hash: str | None = None,
        repair_context: Mapping[str, object] | None = None,
    ) -> PreparedDecomposition:
        """Revise against the approved snapshot, then independently review each result."""
        with self._session_factory() as db:
            project = self._project(db, project_id)
            version = self._approved_version(db, project)
            snapshot = ApprovedSpecSnapshot(
                version.id, json.loads(json.dumps(version.content)), tuple(version.input_refs), version.content_hash,
            )
            route_decision = db.query(SdlcRouteDecision).filter_by(project_id=project.id).one_or_none()
            if route_decision is None or route_decision.selected_model is None:
                raise DecompositionNotAllowed(
                    "a user-confirmed SDLC route is required before task decomposition"
                )
            selected_sdlc_model = route_decision.selected_model
            seed = self._latest_rejected_review(db, project_id, snapshot, command_id, input_hash)

        previous, first_round, prior_call_ids = seed or (None, 0, [])
        seen_review_fingerprints: set[str] = set()
        if previous and previous.get("review_feedback") is not None:
            seen_review_fingerprints.add(
                _review_fingerprint(
                    SemanticReview.model_validate(previous["review_feedback"])
                )
            )
        if first_round >= _MAX_SEMANTIC_REVIEW_ATTEMPTS:
            review = SemanticReview.model_validate(previous["review_feedback"])
            raise SemanticReviewRejected(BreakdownValidationError(
                "SEMANTIC_REVIEW_BLOCKED", self._review_implicated_path(review),
                "Semantic revision budget exhausted for this command",
            ), prior_call_ids)

        # Continue bounded repair while the reviewer reports meaningfully different
        # blockers. A persisted rejection already supplies the initial proposal.
        for repair_round in range(first_round, _MAX_SEMANTIC_REVIEW_ATTEMPTS):
            if repair_round == 0 and previous is None:
                report_agent_progress(
                    "base_decomposition",
                    "正在根据已批准 PRD 生成任务边界和 Agent Spec。",
                )
            else:
                report_agent_progress(
                    "repairing",
                    f"正在修复第 {repair_round} 轮拆分审核问题。",
                )
            payload = {
                "project_id": project_id,
                "spec_version_id": snapshot.id,
                "spec_content_hash": snapshot.content_hash,
                "source_spec_version_id": snapshot.id,
                "source_spec_content_hash": snapshot.content_hash,
                "approved_spec": json.loads(json.dumps(dict(snapshot.content))),
                "input_refs": list(snapshot.input_refs),
                "repair_round": repair_round,
                "decomposition_stage": "base",
                "decomposition_contract_version": _DECOMPOSITION_CONTRACT_VERSION,
                "sdlc_rules": planning_context(),
            }
            if isinstance(repair_context, Mapping):
                payload["automatic_repair_context"] = json.loads(
                    json.dumps(dict(repair_context))
                )
            payload["selected_sdlc_model"] = selected_sdlc_model
            if command_id is not None:
                payload["command_id"] = command_id
            if input_hash is not None:
                payload["input_hash"] = input_hash
            if previous:
                payload.update(previous)
            breakdown, review, call_id, plan_call_ids, reviewer_call_id = await self._prepare_round(
                project_id, snapshot, payload, command_id=command_id, input_hash=input_hash,
            )
            if not self._review_blocks(review):
                report_agent_progress(
                    "ready_to_materialize",
                    "拆分结果已通过校验，正在写入 WorkItem 和 Agent Spec。",
                )
                return PreparedDecomposition(
                    project_id,
                    snapshot,
                    breakdown,
                    call_id,
                    tuple(plan_call_ids),
                    reviewer_call_id,
                )

            review_fingerprint = _review_fingerprint(review)
            repeated_review = review_fingerprint in seen_review_fingerprints
            seen_review_fingerprints.add(review_fingerprint)
            will_repair = (
                repair_round + 1 < _MAX_SEMANTIC_REVIEW_ATTEMPTS
                and self._can_repair_review(review)
                and not repeated_review
            )
            with self._session_factory() as db:
                project = self._project(db, project_id)
                db.add(AuditEvent(
                    id=_new_id(), project_id=project_id, session_id=project.session_id,
                    event_type="DECOMPOSITION_REVIEW_REJECTED", actor_id=None,
                    payload={
                        "spec_version_id": snapshot.id, "command_id": command_id,
                        "agent_call_ids": [call_id, *plan_call_ids, reviewer_call_id], "repair_round": repair_round,
                        "will_repair": will_repair,
                        "stop_reason": "repeated_review" if repeated_review else None,
                        "review": review.model_dump(mode="json"),
                    },
                ))
                db.commit()
            if not will_repair:
                error = BreakdownValidationError(
                    "SEMANTIC_REVIEW_BLOCKED", self._review_implicated_path(review),
                    "Reviewer found a blocking semantic conflict",
                )
                raise SemanticReviewRejected(
                    error, [call_id, *plan_call_ids, reviewer_call_id]
                )
            if plan_call_ids and self._review_targets_plans(review):
                prepared = await self._repair_staged_plans(
                    project_id,
                    snapshot,
                    breakdown,
                    review,
                    base_call_id=call_id,
                    plan_call_ids=plan_call_ids,
                    reviewer_call_id=reviewer_call_id,
                    completed_round=repair_round,
                    seen_review_fingerprints=seen_review_fingerprints,
                    command_id=command_id,
                    input_hash=input_hash,
                )
                report_agent_progress(
                    "ready_to_materialize",
                    "修复后的拆分结果已通过校验，正在写入。",
                )
                return prepared
            previous = {
                "previous_breakdown": breakdown.model_dump(mode="json"),
                "review_feedback": review.model_dump(mode="json"),
                "previous_review_call_id": reviewer_call_id,
            }
        raise RuntimeError("semantic repair budget exhausted without a review outcome")

    @staticmethod
    def _can_repair_review(review: SemanticReview) -> bool:
        return review.verdict is not ReviewVerdict.NEED_INFO and bool(review.findings) and not any(
            finding.code in {"NEEDS_HUMAN_DECISION", "NEEDS_DECISION", "UNACCEPTED_RISK"}
            for finding in review.findings
        )

    def _latest_rejected_review(
        self, db: Session, project_id: str, snapshot: ApprovedSpecSnapshot,
        command_id: str | None, input_hash: str | None,
    ):
        """Reuse only durable feedback bound to this exact project and approved PRD."""
        calls = db.query(AgentCall).filter_by(project_id=project_id, operation="review_breakdown").order_by(
            AgentCall.started_at.desc(), AgentCall.id.desc(),
        ).all()
        # A newer command's feedback must not hide this command's consumed budget.
        # Prefer its highest recorded round, retaining chronological order for ties.
        def resume_priority(call: AgentCall) -> tuple[bool, int]:
            same = command_id is not None and call.request.get("command_id") == command_id and call.request.get("input_hash") == input_hash
            round_value = call.request.get("repair_round", 0)
            return same, round_value if same and type(round_value) is int else 0

        calls.sort(key=resume_priority, reverse=True)
        for call in calls:
            request = call.request
            if (request.get("project_id") != project_id
                    or request.get("sdlc_rules", {}).get("hash") != planning_context()["hash"]
                    or request.get("decomposition_contract_version") != _DECOMPOSITION_CONTRACT_VERSION
                    or request.get("source_spec_version_id") != snapshot.id
                    or request.get("source_spec_content_hash") != snapshot.content_hash
                    or tuple(request.get("input_refs", [])) != snapshot.input_refs
                    or request.get("approved_spec") != snapshot.content):
                continue
            if call.status == "FAILED":
                continue
            if call.status != "RESULT_READY":
                return None
            try:
                review = SemanticReview.model_validate(call.response)
                breakdown = WorkBreakdown.model_validate(request.get("canonical_breakdown"))
                validate_breakdown(breakdown, snapshot, require_implementation_plan=False, require_lifecycle=True)
            except ValueError:
                return None
            if not self._review_blocks(review) or not self._can_repair_review(review):
                return None
            same_attempt = command_id is not None and request.get("command_id") == command_id and request.get("input_hash") == input_hash
            if (
                not same_attempt
                and request.get("plan_agent_call_ids")
                and self._review_targets_plans(review)
            ):
                # A new explicit command gets a fresh semantic-repair budget and
                # retains the last review as planning context.  The base tree and
                # unaffected task plans may still come from exact checkpoints;
                # reviewer-implicated plans are regenerated with this feedback.
                return {
                    "inherited_breakdown": breakdown.model_dump(mode="json"),
                    "inherited_review_feedback": review.model_dump(mode="json"),
                    "previous_review_call_id": call.id,
                }, 0, [call.id]
            repeated_review = False
            if same_attempt:
                fingerprint = _review_fingerprint(review)
                for earlier in calls:
                    if earlier.id == call.id or earlier.status != "RESULT_READY":
                        continue
                    earlier_request = earlier.request
                    if (
                        earlier_request.get("command_id") != command_id
                        or earlier_request.get("input_hash") != input_hash
                        or earlier_request.get("source_spec_version_id") != snapshot.id
                        or earlier_request.get("source_spec_content_hash")
                        != snapshot.content_hash
                    ):
                        continue
                    try:
                        earlier_review = SemanticReview.model_validate(
                            earlier.response
                        )
                    except ValueError:
                        continue
                    if _review_fingerprint(earlier_review) == fingerprint:
                        repeated_review = True
                        break
            completed_round = request.get("repair_round", 0) if same_attempt else 0
            if (
                type(completed_round) is not int
                or not 0 <= completed_round < _MAX_SEMANTIC_REVIEW_ATTEMPTS
            ):
                raise DecompositionNotAllowed("invalid persisted semantic repair round")
            evidence_ids = [call.id]
            if same_attempt and request.get("pm_agent_call_id"):
                evidence_ids.insert(0, request["pm_agent_call_id"])
            next_round = (
                _MAX_SEMANTIC_REVIEW_ATTEMPTS
                if repeated_review
                else completed_round + 1
            )
            return {
                "previous_breakdown": breakdown.model_dump(mode="json"),
                "review_feedback": review.model_dump(mode="json"),
                "previous_review_call_id": call.id,
            }, next_round, evidence_ids
        return None

    def _assert_snapshot_current(self, db: Session, project_id: str, snapshot: ApprovedSpecSnapshot) -> None:
        version = self._approved_version(db, self._project(db, project_id))
        if (version.id != snapshot.id or version.content_hash != snapshot.content_hash
                or version.content != snapshot.content or tuple(version.input_refs) != snapshot.input_refs):
            raise DecompositionNotAllowed("approved Spec changed during decomposition")

    async def _prepare_round(
        self, project_id: str, snapshot: ApprovedSpecSnapshot, payload: dict[str, object], *,
        command_id: str | None, input_hash: str | None,
    ) -> tuple[WorkBreakdown, SemanticReview, str, list[str], str]:
        with self._session_factory() as db:
            self._assert_snapshot_current(db, project_id, snapshot)
            pm = self._pm_session(db, project_id)
            checkpoint = self._base_checkpoint(db, project_id, snapshot, payload)
            call_id = _new_id()
            if checkpoint is None:
                call = AgentCall(
                    id=call_id, project_id=project_id,
                    agent_session_id=pm.id, operation="decompose_spec",
                    request=payload, status="PENDING",
                )
                base_checkpoint_call_id = call_id
            else:
                breakdown, source_call_id = checkpoint
                adopted_request = json.loads(json.dumps(payload))
                adopted_request["checkpoint_source_call_id"] = source_call_id
                call = AgentCall(
                    id=call_id, project_id=project_id,
                    agent_session_id=pm.id, operation="decompose_spec",
                    request=adopted_request,
                    response=breakdown.model_dump(mode="json"),
                    status="RESULT_READY", completed_at=_now(),
                )
                base_checkpoint_call_id = source_call_id
            db.add(call)
            db.commit()

        if checkpoint is None:
            try:
                breakdown = await self._agent.decompose_spec(payload)
            except Exception as error:
                self._record_failure(project_id, call_id, error)
                raise DecompositionAgentFailure(str(error), call_id) from error
            with self._session_factory() as db:
                call = db.get(AgentCall, call_id)
                if call is None:
                    raise RuntimeError("decomposition Agent call disappeared")
                call.status, call.response, call.completed_at = "RESULT_READY", breakdown.model_dump(mode="json"), _now()
                db.commit()
        try:
            validate_breakdown(
                breakdown, snapshot, require_implementation_plan=False, require_lifecycle=True
            )
            with self._session_factory() as db:
                self._assert_snapshot_current(db, project_id, snapshot)
                self._assert_new_local_keys(db, project_id, breakdown)
        except BreakdownValidationError as error:
            self._record_failure(project_id, call_id, error)
            raise PreparedBreakdownValidationError(error, [call_id]) from error
        except Exception as error:
            self._record_failure(project_id, call_id, error)
            raise
        try:
            report_agent_progress(
                "task_planning",
                f"任务边界已生成，正在规划 {len(breakdown.agent_specs)} 个子任务。",
            )
            inherited_review = None
            inherited_plans = None
            inherited_target_keys = None
            if payload.get("inherited_review_feedback") is not None:
                inherited_review = SemanticReview.model_validate(
                    payload["inherited_review_feedback"]
                )
                inherited_breakdown = WorkBreakdown.model_validate(
                    payload["inherited_breakdown"]
                )
                inherited_plans = {
                    task.work_item_key: task.implementation_plan
                    for task in inherited_breakdown.agent_specs
                }
                inherited_target_keys = {
                    task.work_item_key
                    for task in self._plan_repair_targets(
                        inherited_breakdown, inherited_review
                    )
                }
            plan_call_ids = await self._plan_tasks(
                project_id,
                snapshot,
                breakdown,
                base_checkpoint_call_id=base_checkpoint_call_id,
                command_id=command_id,
                input_hash=input_hash,
                previous_plans=inherited_plans,
                review_feedback=inherited_review,
                previous_review_call_id=(
                    str(payload["previous_review_call_id"])
                    if inherited_review is not None
                    else None
                ),
                review_target_keys=inherited_target_keys,
            )
            validate_breakdown(breakdown, snapshot, require_lifecycle=True)
        except DecompositionPlanningFailure as error:
            error.agent_call_ids.insert(0, call_id)
            error.agent_call_id = error.agent_call_ids[-1]
            raise
        except BreakdownValidationError as error:
            self._record_failure(project_id, call_id, error)
            raise PreparedBreakdownValidationError(error, [call_id]) from error
        report_agent_progress(
            "semantic_review",
            "子任务计划已生成，正在进行一致性审核。",
        )
        review, reviewer_call_id = await self._run_breakdown_review(
            project_id,
            snapshot,
            breakdown,
            base_call_id=call_id,
            plan_call_ids=plan_call_ids,
            repair_round=int(payload["repair_round"]),
            command_id=command_id,
            input_hash=input_hash,
        )
        return breakdown, review, call_id, plan_call_ids, reviewer_call_id

    async def _run_breakdown_review(
        self,
        project_id: str,
        snapshot: ApprovedSpecSnapshot,
        breakdown: WorkBreakdown,
        *,
        base_call_id: str,
        plan_call_ids: list[str],
        repair_round: int,
        command_id: str | None,
        input_hash: str | None,
    ) -> tuple[SemanticReview, str]:
        reviewer_payload = self._reviewer_payload(
            project_id, snapshot, breakdown,
            command_id=command_id, input_hash=input_hash,
        )
        reviewer_payload.update(
            pm_agent_call_id=base_call_id,
            plan_agent_call_ids=plan_call_ids,
            repair_round=repair_round,
        )
        with self._session_factory() as db:
            reviewer = self._reviewer_session(db, project_id)
            reviewer_call = AgentCall(
                id=_new_id(), project_id=project_id, agent_session_id=reviewer.id,
                operation="review_breakdown", request=reviewer_payload, status="PENDING",
            )
            db.add(reviewer_call)
            db.commit()
            reviewer_call_id = reviewer_call.id

        try:
            review = await self._agent.review_breakdown(reviewer_payload)
        except Exception as error:
            self._record_failure(project_id, reviewer_call_id, error)
            raise BreakdownReviewerFailure(
                str(error), [base_call_id, *plan_call_ids, reviewer_call_id]
            ) from error
        with self._session_factory() as db:
            persisted_review_call = db.get(AgentCall, reviewer_call_id)
            if persisted_review_call is None:
                raise RuntimeError("breakdown reviewer Agent call disappeared")
            persisted_review_call.status = "RESULT_READY"
            persisted_review_call.response = review.model_dump(mode="json")
            persisted_review_call.completed_at = _now()
            db.commit()
        return review, reviewer_call_id

    async def _repair_staged_plans(
        self,
        project_id: str,
        snapshot: ApprovedSpecSnapshot,
        breakdown: WorkBreakdown,
        review: SemanticReview,
        *,
        base_call_id: str,
        plan_call_ids: list[str],
        reviewer_call_id: str,
        completed_round: int,
        seen_review_fingerprints: set[str],
        command_id: str | None,
        input_hash: str | None,
    ) -> PreparedDecomposition:
        """Repair only reviewer-implicated plans while retaining the base tree."""

        if len(plan_call_ids) != len(breakdown.agent_specs):
            raise RuntimeError("staged planning evidence does not match task count")
        plan_calls_by_key = {
            task.work_item_key: call_id
            for task, call_id in zip(
                breakdown.agent_specs, plan_call_ids, strict=True
            )
        }
        with self._session_factory() as db:
            base_call = db.get(AgentCall, base_call_id)
            if base_call is None:
                raise RuntimeError("base decomposition Agent call disappeared")
            base_checkpoint_call_id = str(
                base_call.request.get("checkpoint_source_call_id") or base_call.id
            )

        for repair_round in range(
            completed_round + 1, _MAX_SEMANTIC_REVIEW_ATTEMPTS
        ):
            targets = self._plan_repair_targets(breakdown, review)
            previous_plans = {
                task.work_item_key: task.implementation_plan
                for task in targets
            }
            for task in targets:
                task.implementation_plan = None
            try:
                repaired_call_ids = await self._plan_tasks(
                    project_id,
                    snapshot,
                    breakdown,
                    base_checkpoint_call_id=base_checkpoint_call_id,
                    command_id=command_id,
                    input_hash=input_hash,
                    previous_plans=previous_plans,
                    review_feedback=review,
                    previous_review_call_id=reviewer_call_id,
                )
                target_keys = {task.work_item_key for task in targets}
                with self._session_factory() as db:
                    repaired_by_key = {}
                    for call_id in repaired_call_ids:
                        call = db.get(AgentCall, call_id)
                        key = (
                            call.request.get("task_spec", {}).get("work_item_key")
                            if call is not None else None
                        )
                        if key not in target_keys or key in repaired_by_key:
                            raise RuntimeError(
                                "task-plan evidence does not match repair targets"
                            )
                        repaired_by_key[key] = call_id
                if set(repaired_by_key) != target_keys:
                    raise RuntimeError(
                        "task-plan evidence does not cover repair targets"
                    )
                plan_calls_by_key.update(repaired_by_key)
                validate_breakdown(breakdown, snapshot, require_lifecycle=True)
            except DecompositionPlanningFailure as error:
                error.agent_call_ids.insert(0, base_call_id)
                error.agent_call_id = error.agent_call_ids[-1]
                raise
            current_plan_ids = [
                plan_calls_by_key[task.work_item_key]
                for task in breakdown.agent_specs
            ]
            review, reviewer_call_id = await self._run_breakdown_review(
                project_id,
                snapshot,
                breakdown,
                base_call_id=base_call_id,
                plan_call_ids=current_plan_ids,
                repair_round=repair_round,
                command_id=command_id,
                input_hash=input_hash,
            )
            if not self._review_blocks(review):
                return PreparedDecomposition(
                    project_id,
                    snapshot,
                    breakdown,
                    base_call_id,
                    tuple(current_plan_ids),
                    reviewer_call_id,
                )
            review_fingerprint = _review_fingerprint(review)
            repeated_review = review_fingerprint in seen_review_fingerprints
            seen_review_fingerprints.add(review_fingerprint)
            will_repair = (
                repair_round + 1 < _MAX_SEMANTIC_REVIEW_ATTEMPTS
                and self._can_repair_review(review)
                and not repeated_review
            )
            with self._session_factory() as db:
                project = self._project(db, project_id)
                db.add(AuditEvent(
                    id=_new_id(), project_id=project_id,
                    session_id=project.session_id,
                    event_type="DECOMPOSITION_REVIEW_REJECTED", actor_id=None,
                    payload={
                        "spec_version_id": snapshot.id,
                        "command_id": command_id,
                        "agent_call_ids": [
                            base_call_id, *current_plan_ids, reviewer_call_id,
                        ],
                        "repair_round": repair_round,
                        "will_repair": will_repair,
                        "stop_reason": (
                            "repeated_review" if repeated_review else None
                        ),
                        "review": review.model_dump(mode="json"),
                    },
                ))
                db.commit()
            if not will_repair:
                error = BreakdownValidationError(
                    "SEMANTIC_REVIEW_BLOCKED",
                    self._review_implicated_path(review),
                    "Reviewer found a blocking semantic conflict",
                )
                raise SemanticReviewRejected(
                    error,
                    [base_call_id, *current_plan_ids, reviewer_call_id],
                )
        raise RuntimeError("semantic plan repair budget exhausted")

    @staticmethod
    def _plan_repair_targets(
        breakdown: WorkBreakdown, review: SemanticReview
    ) -> list[AgentSpecProposal]:
        by_key = {task.work_item_key: task for task in breakdown.agent_specs}
        keys: list[str] = []
        for finding in review.findings:
            if not (
                finding.blocks_progress
                or finding.severity.strip().upper() == "BLOCKER"
            ):
                continue
            path = _trimmed(finding.spec_path)
            if not path.startswith("agent_specs[") or "]" not in path:
                return list(breakdown.agent_specs)
            key = path.partition("[")[2].partition("]")[0]
            if key.isdigit():
                index = int(key)
                if index >= len(breakdown.agent_specs):
                    return list(breakdown.agent_specs)
                key = breakdown.agent_specs[index].work_item_key
            if key not in by_key:
                return list(breakdown.agent_specs)
            if key not in keys:
                keys.append(key)
        if not keys:
            return list(breakdown.agent_specs)
        target_keys = transitive_dependent_keys(breakdown.agent_specs, set(keys))
        return [
            task for task in breakdown.agent_specs
            if task.work_item_key in target_keys
        ]

    @staticmethod
    def _review_targets_plans(review: SemanticReview) -> bool:
        blocking = [
            finding for finding in review.findings
            if finding.blocks_progress
            or finding.severity.strip().upper() == "BLOCKER"
        ]
        return bool(blocking) and all(
            ".implementation_plan" in _trimmed(finding.spec_path)
            for finding in blocking
        )

    @staticmethod
    def _base_checkpoint(
        db: Session,
        project_id: str,
        snapshot: ApprovedSpecSnapshot,
        payload: Mapping[str, object],
    ) -> tuple[WorkBreakdown, str] | None:
        """Return a valid plan-free base result for this exact approved Spec."""

        if payload.get("previous_breakdown") or payload.get("review_feedback"):
            return None
        calls = (
            db.query(AgentCall)
            .filter_by(project_id=project_id, operation="decompose_spec")
            .order_by(AgentCall.started_at.desc(), AgentCall.id.desc())
            .all()
        )
        for call in calls:
            request = call.request
            if (
                call.status not in {"RESULT_READY", "SUCCEEDED"}
                or request.get("decomposition_stage") != "base"
                or request.get("sdlc_rules", {}).get("hash") != planning_context()["hash"]
                or request.get("decomposition_contract_version") != _DECOMPOSITION_CONTRACT_VERSION
                or request.get("source_spec_version_id", request.get("spec_version_id")) != snapshot.id
                or request.get("source_spec_content_hash", request.get("spec_content_hash")) != snapshot.content_hash
                or request.get("approved_spec") != snapshot.content
                or tuple(request.get("input_refs", [])) != snapshot.input_refs
                or request.get("previous_breakdown")
                or request.get("review_feedback")
            ):
                continue
            try:
                breakdown = WorkBreakdown.model_validate(call.response)
                validate_breakdown(
                    breakdown, snapshot, require_implementation_plan=False, require_lifecycle=True
                )
            except ValueError:
                continue
            source_call_id = str(
                request.get("checkpoint_source_call_id") or call.id
            )
            return breakdown.model_copy(deep=True), source_call_id
        return None

    async def _plan_tasks(
        self,
        project_id: str,
        snapshot: ApprovedSpecSnapshot,
        breakdown: WorkBreakdown,
        *,
        base_checkpoint_call_id: str,
        command_id: str | None,
        input_hash: str | None,
        previous_plans: Mapping[str, ImplementationPlan | None] | None = None,
        review_feedback: SemanticReview | None = None,
        previous_review_call_id: str | None = None,
        review_target_keys: set[str] | None = None,
    ) -> list[str]:
        """Fill only missing task plans and settle every started Agent call."""

        targets = [
            task for task in breakdown.agent_specs if task.implementation_plan is None
        ]
        if not targets:
            return []
        call_ids_by_key: dict[str, str] = {}
        target_keys = {task.work_item_key for task in targets}
        for wave in topological_plan_waves(
            breakdown.agent_specs, selected_keys=target_keys
        ):
            results = await asyncio.gather(
                *(
                    self._plan_task(
                        project_id,
                        snapshot,
                        breakdown,
                        task,
                        base_checkpoint_call_id=base_checkpoint_call_id,
                        command_id=command_id,
                        input_hash=input_hash,
                        previous_plan=(previous_plans or {}).get(task.work_item_key),
                        review_feedback=(
                            review_feedback
                            if review_target_keys is None
                            or task.work_item_key in review_target_keys
                            else None
                        ),
                        previous_review_call_id=(
                            previous_review_call_id
                            if review_target_keys is None
                            or task.work_item_key in review_target_keys
                            else None
                        ),
                    )
                    for task in wave
                ),
                return_exceptions=True,
            )
            failure: Exception | None = None
            cancellation: BaseException | None = None
            for task, result in zip(wave, results, strict=True):
                if isinstance(result, BaseException):
                    if isinstance(result, DecompositionPlanningFailure):
                        call_ids_by_key[task.work_item_key] = result.agent_call_id
                    if failure is None and isinstance(result, Exception):
                        failure = result
                    elif cancellation is None and not isinstance(result, Exception):
                        cancellation = result
                    continue
                plan, call_id = result
                task.implementation_plan = plan
                call_ids_by_key[task.work_item_key] = call_id
            ordered_call_ids = [
                call_ids_by_key[task.work_item_key]
                for task in breakdown.agent_specs
                if task.work_item_key in call_ids_by_key
            ]
            if failure is not None:
                raise DecompositionPlanningFailure(
                    str(failure), ordered_call_ids
                ) from failure
            if cancellation is not None:
                raise cancellation
        return [
            call_ids_by_key[task.work_item_key]
            for task in breakdown.agent_specs
            if task.work_item_key in call_ids_by_key
        ]

    async def _plan_task(
        self,
        project_id: str,
        snapshot: ApprovedSpecSnapshot,
        breakdown: WorkBreakdown,
        task: AgentSpecProposal,
        *,
        base_checkpoint_call_id: str,
        command_id: str | None,
        input_hash: str | None,
        previous_plan: ImplementationPlan | None = None,
        review_feedback: SemanticReview | None = None,
        previous_review_call_id: str | None = None,
    ) -> tuple[ImplementationPlan, str]:
        async with self._planning_semaphore:
            payload: dict[str, object] = {
                "project_id": project_id,
                "source_spec_version_id": snapshot.id,
                "source_spec_content_hash": snapshot.content_hash,
                "input_refs": list(snapshot.input_refs),
                "decomposition_stage": "task_plan",
                "sdlc_rules": planning_context(),
                "lifecycle": breakdown.lifecycle.model_dump(mode="json") if breakdown.lifecycle else None,
                "task_lifecycle": task_lifecycle_context(breakdown.lifecycle, task.work_item_key),
                "decomposition_contract_version": _DECOMPOSITION_CONTRACT_VERSION,
                "base_checkpoint_call_id": base_checkpoint_call_id,
                "task_spec": task.model_dump(mode="json"),
                "task_spec_hash": _canonical_hash(
                    task.model_dump(mode="json")
                ),
                "approved_spec": json.loads(json.dumps(dict(snapshot.content))),
                "related_tasks": [
                    related.model_dump(
                        mode="json", exclude={"implementation_plan"}
                    )
                    for related in breakdown.agent_specs
                    if related.work_item_key != task.work_item_key
                ],
            }
            if command_id is not None:
                payload["command_id"] = command_id
            if input_hash is not None:
                payload["input_hash"] = input_hash
            if review_feedback is not None:
                payload["previous_plan"] = (
                    previous_plan.model_dump(mode="json")
                    if previous_plan is not None else None
                )
                payload["review_feedback"] = review_feedback.model_dump(
                    mode="json"
                )
                payload["previous_review_call_id"] = previous_review_call_id
            with self._session_factory() as db:
                self._assert_snapshot_current(db, project_id, snapshot)
                session = self._pm_session(db, project_id)
                contracts = dependency_contracts(
                    task,
                    {item.work_item_key: item for item in breakdown.agent_specs},
                )
                payload["dependency_contracts"] = contracts
                payload["dependency_contract_hashes"] = {
                    str(contract["work_item_key"]): str(contract["contract_hash"])
                    for contract in contracts
                }
                prior_failure = self._task_plan_failure_context(
                    db, project_id, snapshot, payload
                )
                if prior_failure is not None:
                    payload["prior_validation_failure"] = prior_failure
                call_id = _new_id()
                checkpoint = self._task_plan_checkpoint(
                    db, project_id, snapshot, payload, task
                )
                if checkpoint is None:
                    call = AgentCall(
                        id=call_id, project_id=project_id,
                        agent_session_id=session.id, operation="plan_task",
                        request=payload, status="PENDING",
                    )
                else:
                    plan, source_call_id = checkpoint
                    adopted_request = json.loads(json.dumps(payload))
                    adopted_request["checkpoint_source_call_id"] = source_call_id
                    call = AgentCall(
                        id=call_id, project_id=project_id,
                        agent_session_id=session.id, operation="plan_task",
                        request=adopted_request,
                        response=plan.model_dump(mode="json"),
                        status="RESULT_READY", completed_at=_now(),
                    )
                db.add(call)
                db.commit()
            if checkpoint is not None:
                return plan, call_id
            response = None
            try:
                plan = await self._agent.plan_task(json.loads(json.dumps(payload)))
                response = plan.model_dump(mode="json")
                plan = ImplementationPlan.model_validate(response)
                approved_ids = {
                    item["requirement_id"]
                    for section in ("functional_requirements", "non_functional_requirements")
                    for item in snapshot.content.get(section, [])
                }
                validate_implementation_plan(plan, task, approved_ids)
            except BaseException as error:
                message = str(error) or type(error).__name__
                with self._session_factory() as db:
                    call = db.get(AgentCall, call_id)
                    if call is not None:
                        call.status = "FAILED"
                        call.response = response
                        call.error = message
                        call.completed_at = _now()
                    db.commit()
                if not isinstance(error, Exception):
                    raise
                raise DecompositionPlanningFailure(message, [call_id]) from error
            with self._session_factory() as db:
                call = db.get(AgentCall, call_id)
                if call is None:
                    raise RuntimeError("task planning Agent call disappeared")
                call.status = "RESULT_READY"
                call.response = response
                call.completed_at = _now()
                db.commit()
            return plan, call_id

    @staticmethod
    def _task_plan_checkpoint(
        db: Session,
        project_id: str,
        snapshot: ApprovedSpecSnapshot,
        payload: Mapping[str, object],
        task: AgentSpecProposal,
    ) -> tuple[ImplementationPlan, str] | None:
        """Return a validated task plan bound to the base and task hashes."""

        if payload.get("review_feedback") is not None:
            return None
        calls = (
            db.query(AgentCall)
            .filter_by(project_id=project_id, operation="plan_task")
            .order_by(AgentCall.started_at.desc(), AgentCall.id.desc())
            .all()
        )
        approved_ids = {
            str(item["requirement_id"])
            for section in ("functional_requirements", "non_functional_requirements")
            for item in snapshot.content.get(section, [])
        }
        for call in calls:
            request = call.request
            if (
                call.status not in {"RESULT_READY", "SUCCEEDED"}
                or request.get("decomposition_stage") != "task_plan"
                or request.get("sdlc_rules", {}).get("hash") != planning_context()["hash"]
                or request.get("decomposition_contract_version") != _DECOMPOSITION_CONTRACT_VERSION
                or request.get("source_spec_version_id") != snapshot.id
                or request.get("source_spec_content_hash") != snapshot.content_hash
                or tuple(request.get("input_refs", [])) != snapshot.input_refs
                or request.get("base_checkpoint_call_id") != payload.get("base_checkpoint_call_id")
                or request.get("task_spec_hash") != payload.get("task_spec_hash")
                or request.get("task_spec") != payload.get("task_spec")
                or request.get("dependency_contract_hashes") != payload.get("dependency_contract_hashes")
                or request.get("dependency_contracts") != payload.get("dependency_contracts")
                or request.get("task_lifecycle") != payload.get("task_lifecycle")
            ):
                continue
            try:
                plan = ImplementationPlan.model_validate(call.response)
                validate_implementation_plan(plan, task, approved_ids)
            except (ValueError, ImplementationPlanError):
                continue
            source_call_id = str(
                request.get("checkpoint_source_call_id") or call.id
            )
            return plan.model_copy(deep=True), source_call_id
        return None

    @staticmethod
    def _task_plan_failure_context(
        db: Session,
        project_id: str,
        snapshot: ApprovedSpecSnapshot,
        payload: Mapping[str, object],
    ) -> dict[str, object] | None:
        """Return the latest exact-task validation failure for a later command."""

        calls = (
            db.query(AgentCall)
            .filter_by(
                project_id=project_id,
                operation="plan_task",
                status="FAILED",
            )
            .order_by(AgentCall.started_at.desc(), AgentCall.id.desc())
            .all()
        )
        for call in calls:
            request = call.request
            if (
                request.get("decomposition_stage") != "task_plan"
                or request.get("sdlc_rules", {}).get("hash")
                != planning_context()["hash"]
                or request.get("decomposition_contract_version")
                != _DECOMPOSITION_CONTRACT_VERSION
                or request.get("source_spec_version_id") != snapshot.id
                or request.get("source_spec_content_hash")
                != snapshot.content_hash
                or tuple(request.get("input_refs", [])) != snapshot.input_refs
                or request.get("base_checkpoint_call_id")
                != payload.get("base_checkpoint_call_id")
                or request.get("task_spec_hash") != payload.get("task_spec_hash")
                or request.get("task_spec") != payload.get("task_spec")
            ):
                continue
            return {
                "agent_call_id": call.id,
                "error": call.error or "The previous task plan failed validation.",
                "instruction": (
                    "Correct this prior validation failure; do not repeat the "
                    "invalid requirement assignment or contract shape."
                ),
            }
        return None

    def _persist_direct(self, prepared: PreparedDecomposition) -> list[AgentSpec]:
        with self._session_factory() as db:
            with db.begin():
                validate_breakdown(prepared.breakdown, prepared.approved_spec, require_lifecycle=True)
                project = self._project(db, prepared.project_id)
                self._assert_snapshot_current(db, project.id, prepared.approved_spec)
                version = self._approved_version(db, project)
                self._assert_new_local_keys(db, project.id, prepared.breakdown)
                specs = self._persist(db.add, db.flush, project, prepared.approved_spec, prepared.breakdown)
                project.phase = ProjectPhase.AGENT_SPECS_READY.value
                project.state_version += 1
                self._mark_call_succeeded(db, prepared.pm_agent_call_id, project.id, "decompose_spec")
                for call_id in prepared.plan_agent_call_ids:
                    self._mark_call_succeeded(db, call_id, project.id, "plan_task")
                self._mark_call_succeeded(db, prepared.reviewer_agent_call_id, project.id, "review_breakdown")
                db.add(AuditEvent(id=_new_id(), project_id=project.id, session_id=project.session_id, event_type="AGENT_SPECS_CREATED", actor_id=None, payload={"spec_version_id": version.id, "agent_call_ids": prepared.agent_call_ids, "agent_spec_ids": [item.id for item in specs]}))
            return specs

    @staticmethod
    def _reviewer_payload(
        project_id: str, snapshot: ApprovedSpecSnapshot, breakdown: WorkBreakdown, *,
        command_id: str | None, input_hash: str | None,
    ) -> dict[str, object]:
        canonical_breakdown = json.loads(json.dumps(
            breakdown.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        ))
        payload: dict[str, object] = {
            "project_id": project_id,
            "decomposition_contract_version": _DECOMPOSITION_CONTRACT_VERSION,
            "source_spec_version_id": snapshot.id,
            "source_spec_content_hash": snapshot.content_hash,
            "input_refs": list(snapshot.input_refs),
            "approved_spec_exclusions": list(snapshot.content.get("exclusions", [])),
            "approved_spec": json.loads(json.dumps(
                dict(snapshot.content), sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            )),
            "agent_spec_semantic_content": [
                {
                    "work_item_key": proposal.work_item_key,
                    "scope": proposal.scope,
                    "exclusions": proposal.exclusions,
                    "fixed_constraints": proposal.fixed_constraints,
                    "configurable_parts": proposal.configurable_parts,
                    "extension_points": proposal.extension_points,
                }
                for proposal in breakdown.agent_specs
            ],
            "canonical_breakdown": canonical_breakdown,
            "sdlc_rules": planning_context(),
        }
        if command_id is not None:
            payload["command_id"] = command_id
        if input_hash is not None:
            payload["input_hash"] = input_hash
        return payload

    @staticmethod
    def _review_blocks(review: object) -> bool:
        if getattr(review, "verdict", None) is not ReviewVerdict.PASS:
            return True
        return any(
            bool(getattr(finding, "blocks_progress", False))
            or str(getattr(finding, "severity", "")).strip().upper() == "BLOCKER"
            for finding in getattr(review, "findings", [])
        )

    @staticmethod
    def _review_implicated_path(review: object) -> str:
        findings = getattr(review, "findings", [])
        if not findings:
            return "review"
        path = _trimmed(getattr(findings[0], "spec_path", ""))
        if path.startswith("agent_specs[") and "]" in path:
            return path.partition("[")[2].partition("]")[0]
        return path or "review"

    @staticmethod
    def _mark_call_succeeded(db: Session, call_id: str, project_id: str, operation: str) -> None:
        call = db.get(AgentCall, call_id)
        if call is None or call.project_id != project_id or call.operation != operation or call.status != "RESULT_READY":
            raise RuntimeError(f"{operation} Agent call is not ready to complete")
        call.status, call.completed_at = "SUCCEEDED", _now()

    def _persist(self, add: Callable[[object], None], flush: Callable[[], None], project: Project, approved_spec: ApprovedSpecSnapshot, breakdown: WorkBreakdown) -> list[AgentSpec]:
        items = [*breakdown.milestones, *breakdown.tasks]
        id_by_key = {proposal.local_key: _new_id() for proposal in items}
        agent_spec_by_key = {proposal.work_item_key: proposal for proposal in breakdown.agent_specs}
        for proposal in items:
            child = agent_spec_by_key.get(proposal.local_key)
            add(self._work_item(project.session_id, project.id, id_by_key, proposal, child))
        for proposal in items:
            for dependency_key in proposal.dependency_keys:
                add(WorkItemDependency(id=_new_id(), project_id=project.id, from_work_item_id=id_by_key[proposal.local_key], to_work_item_id=id_by_key[dependency_key]))
        specs: list[AgentSpec] = []
        for proposal in breakdown.agent_specs:
            dependencies = [id_by_key[key] for key in proposal.dependency_keys]
            content = _normalized_agent_spec_content(proposal, work_item_id=id_by_key[proposal.work_item_key], dependency_ids=dependencies, spec_id=approved_spec.id, approved_spec=approved_spec.content, lifecycle=breakdown.lifecycle)
            spec = AgentSpec(id=_new_id(), project_id=project.id, work_item_id=id_by_key[proposal.work_item_key], source_spec_version_id=approved_spec.id, dependency_work_item_ids=dependencies, content=content, content_hash=_canonical_hash(content))
            add(spec)
            specs.append(spec)
        flush()
        return specs

    def as_command_handler(self):
        """Return a two-phase adapter consumed by ``CommandService`` in Task 10."""
        from app.services.command_service import CommandHandlerFailure, CommandHandlerRejected, PreparedCommand

        service = self

        class _Handler:
            async def prepare(self, context):
                try:
                    repair_context = context.request.payload.get("auto_repair")
                    prepared = await service.prepare(
                        context.project_id,
                        command_id=context.request.command_id,
                        input_hash=context.input_hash,
                        repair_context=(
                            repair_context
                            if isinstance(repair_context, Mapping)
                            else None
                        ),
                    )
                except SemanticReviewRejected as error:
                    raise CommandHandlerRejected(
                        str(error), agent_call_ids=list(error.agent_call_ids),
                        audit_payload={"decomposition_error_code": error.code},
                    ) from error
                except (PreparedBreakdownValidationError, DecompositionAgentFailure, BreakdownReviewerFailure) as error:
                    call_ids = list(error.agent_call_ids)
                    if getattr(error, "code", None) == "SDLC_NEEDS_CLARIFICATION":
                        raise CommandHandlerRejected(str(error), agent_call_ids=call_ids,
                            audit_payload={"decomposition_error_code": error.code}) from error
                    raise CommandHandlerFailure(str(error), agent_call_ids=call_ids, audit_payload={"decomposition_error_code": getattr(error, "code", "AGENT_FAILURE")}) from error
                return PreparedCommand(payload={"approved_spec_id": prepared.approved_spec.id, "approved_spec": dict(prepared.approved_spec.content), "input_refs": list(prepared.approved_spec.input_refs), "spec_content_hash": prepared.approved_spec.content_hash, "breakdown": prepared.breakdown.model_dump(mode="json")}, agent_backed=True, agent_call_ids=prepared.agent_call_ids, audit_payload={"decomposition_agent_call_ids": prepared.agent_call_ids})

            def materialize(self, uow, context, prepared):
                if context.state.current_spec_version_id != prepared.payload["approved_spec_id"]:
                    raise DecompositionNotAllowed("approved Spec changed before conversion")
                snapshot = ApprovedSpecSnapshot(str(prepared.payload["approved_spec_id"]), prepared.payload["approved_spec"], tuple(prepared.payload["input_refs"]), str(prepared.payload["spec_content_hash"]))
                uow.assert_decomposition_spec_snapshot(
                    snapshot.id, snapshot.content_hash, snapshot.content, snapshot.input_refs,
                )
                breakdown = WorkBreakdown.model_validate(prepared.payload["breakdown"])
                return service._materialize_command(uow, context, snapshot, breakdown, prepared.agent_call_ids)

        return _Handler()

    def _materialize_command(self, uow, context, approved_spec: ApprovedSpecSnapshot, breakdown: WorkBreakdown, agent_call_ids: list[str]):
        """Materialize using the narrow command UOW without mutating protected state."""
        from app.services.command_service import CommandHandlerResult
        validate_breakdown(breakdown, approved_spec, require_lifecycle=True)
        items = [*breakdown.milestones, *breakdown.tasks]
        collisions = uow.existing_work_item_keys({item.local_key for item in items})
        if collisions:
            _error("LOCAL_KEY_EXISTS", sorted(collisions)[0], "local key already exists for this project")
        id_by_key = {proposal.local_key: _new_id() for proposal in items}
        agent_spec_by_key = {proposal.work_item_key: proposal for proposal in breakdown.agent_specs}
        for proposal in items:
            uow.add_work_item(
                self._work_item(
                    context.session_id,
                    context.project_id,
                    id_by_key,
                    proposal,
                    agent_spec_by_key.get(proposal.local_key),
                )
            )
        for proposal in items:
            for dependency_key in proposal.dependency_keys:
                uow.add_work_item_dependency(WorkItemDependency(id=_new_id(), project_id=context.project_id, from_work_item_id=id_by_key[proposal.local_key], to_work_item_id=id_by_key[dependency_key]))
        specs = []
        for proposal in breakdown.agent_specs:
            dependency_ids = [id_by_key[key] for key in proposal.dependency_keys]
            content = _normalized_agent_spec_content(proposal, work_item_id=id_by_key[proposal.work_item_key], dependency_ids=dependency_ids, spec_id=approved_spec.id, approved_spec=approved_spec.content, lifecycle=breakdown.lifecycle)
            spec = AgentSpec(id=_new_id(), project_id=context.project_id, work_item_id=id_by_key[proposal.work_item_key], source_spec_version_id=approved_spec.id, dependency_work_item_ids=dependency_ids, content=content, content_hash=_canonical_hash(content))
            uow.add_agent_spec(spec)
            specs.append(spec)
        if len(agent_call_ids) < 2:
            raise ValueError("breakdown materialization requires PM and Reviewer Agent call evidence")
        uow.mark_decomposition_call_succeeded(agent_call_ids[0])
        for call_id in agent_call_ids[1:-1]:
            uow.mark_task_plan_call_succeeded(call_id)
        uow.mark_breakdown_reviewer_call_succeeded(agent_call_ids[-1])
        return CommandHandlerResult(phase=ProjectPhase.AGENT_SPECS_READY, created_resource_ids=[item.id for item in specs], audit_payload={"spec_version_id": approved_spec.id, "agent_spec_ids": [item.id for item in specs]})

    @staticmethod
    def _work_item(
        session_id: str,
        project_id: str,
        id_by_key: Mapping[str, str],
        proposal: Any,
        child: AgentSpecProposal | None,
    ) -> WorkItem:
        fields: dict[str, object] = {}
        if child is not None:
            fields = {
                "scope": [_normalize_text(value) for value in child.scope],
                "exclusions": [_normalize_text(value) for value in child.exclusions],
                "inputs": [_normalize_text(value) for value in child.inputs],
                "outputs": [item.model_dump(mode="json") for item in child.outputs],
                "acceptance_criteria": [
                    item.model_dump(mode="json") for item in child.acceptance_criteria
                ],
                "required_skills": [
                    _normalize_text(value) for value in child.required_skills
                ],
                "responsible_role": _normalize_text(child.responsible_role),
                "suggested_assignee": _normalize_text(child.suggested_assignee),
            }
        return WorkItem(
            id=id_by_key[proposal.local_key],
            session_id=session_id,
            project_id=project_id,
            local_key=proposal.local_key,
            parent_id=id_by_key.get(proposal.parent_key),
            kind=proposal.kind.value,
            executable=proposal.kind is WorkItemKind.TASK,
            title=_normalize_text(proposal.title),
            objective=_normalize_text(proposal.objective),
            status="todo",
            **fields,
        )

    def _record_failure(self, project_id: str, call_id: str, error: Exception) -> None:
        with self._session_factory() as db:
            project = db.get(Project, project_id)
            call = db.get(AgentCall, call_id)
            if call is not None:
                call.status, call.error, call.completed_at = "FAILED", str(error), _now()
            if project is not None:
                db.add(AuditEvent(id=_new_id(), project_id=project.id, session_id=project.session_id, event_type="DECOMPOSITION_FAILED", actor_id=None, payload={"agent_call_id": call_id, "error_code": "DECOMPOSITION_FAILED", "message": "Decomposition failed; retry after correcting the workflow input."}))
            db.commit()

    def _record_failures(self, project_id: str, call_ids: list[str], error: Exception) -> None:
        with self._session_factory() as db:
            project = db.get(Project, project_id)
            for call_id in call_ids:
                call = db.get(AgentCall, call_id)
                if call is not None:
                    call.status, call.error, call.completed_at = "FAILED", str(error), _now()
            if project is not None:
                db.add(AuditEvent(
                    id=_new_id(), project_id=project.id, session_id=project.session_id,
                    event_type="DECOMPOSITION_FAILED", actor_id=None,
                    payload={"agent_call_ids": list(call_ids), "error_code": "DECOMPOSITION_FAILED", "message": "Decomposition failed; retry after correcting the workflow input."},
                ))
            db.commit()

    @staticmethod
    def _project(db: Session, project_id: str) -> Project:
        project = db.get(Project, project_id)
        if project is None:
            raise KeyError(f"project not found: {project_id}")
        return project

    @staticmethod
    def _approved_version(db: Session, project: Project) -> SpecVersion:
        version = db.get(SpecVersion, project.current_spec_version_id) if project.current_spec_version_id else None
        if version is None or version.project_id != project.id or version.status != SpecStatus.APPROVED.value or project.phase != ProjectPhase.REVIEW.value:
            raise DecompositionNotAllowed("current Spec is not approved and ready for decomposition")
        return version

    @staticmethod
    def _pm_session(db: Session, project_id: str) -> AgentSession:
        session = db.query(AgentSession).filter_by(project_id=project_id, role="PM").first()
        if session is None:
            session = AgentSession(id=_new_id(), project_id=project_id, role="PM", purpose="Decompose approved Project Spec into child-Agent Specs", metadata_json={})
            db.add(session)
            db.flush()
        return session

    @staticmethod
    def _reviewer_session(db: Session, project_id: str) -> AgentSession:
        session = db.query(AgentSession).filter_by(project_id=project_id, role="REVIEWER").first()
        if session is None:
            session = AgentSession(id=_new_id(), project_id=project_id, role="REVIEWER", purpose="Review child-Agent Spec breakdown semantic consistency", metadata_json={})
            db.add(session)
            db.flush()
        return session

    @staticmethod
    def _assert_new_local_keys(db: Session, project_id: str, breakdown: WorkBreakdown) -> None:
        proposed = {item.local_key for item in [*breakdown.milestones, *breakdown.tasks]}
        existing = {item.local_key for item in db.query(WorkItem.local_key).filter_by(project_id=project_id).all()}
        collision = sorted(proposed & existing)
        if collision:
            _error("LOCAL_KEY_EXISTS", collision[0], "local key already exists for this project")


__all__ = ["ApprovedSpecSnapshot", "BreakdownValidationError", "DecompositionNotAllowed", "DecompositionService", "PreparedDecomposition", "validate_breakdown"]
