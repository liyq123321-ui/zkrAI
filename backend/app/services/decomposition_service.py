"""Validated, atomic conversion of an approved Spec into child-Agent Specs.

The PM Agent proposes local keys only.  This boundary validates every proposal
before writes, assigns server IDs, and never starts a child Agent.
"""

import hashlib
import json
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable, Mapping
from uuid import uuid4

from sqlalchemy.orm import Session

from app.agents.gateway import AgentGateway
from app.database.models import AgentCall, AgentSession, AgentSpec, AuditEvent, Project, SpecVersion, WorkItem, WorkItemDependency
from app.domain.types import AgentSpecProposal, ProjectPhase, ProjectSpecPayload, ReviewVerdict, SpecStatus, WorkBreakdown, WorkItemKind


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
        self.agent_call_id = self.agent_call_ids[-1]
        super().__init__(message)


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
    reviewer_agent_call_id: str

    @property
    def agent_call_ids(self) -> list[str]:
        return [self.pm_agent_call_id, self.reviewer_agent_call_id]

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
        if set(proposal.dependency_keys) != set(by_key[key].dependency_keys):
            _error("DEPENDENCY_MISMATCH", key, "AgentSpec dependencies must match its task prerequisites")

    missing = set(by_key) - {item.local_key for item in breakdown.milestones} - set(specs_by_key)
    if missing:
        _error("MISSING_AGENT_SPEC", sorted(missing)[0], "each task needs exactly one AgentSpec")


def _normalize_text(value: str) -> str:
    return " ".join(value.strip().split())


def _normalized_agent_spec_content(proposal: AgentSpecProposal, *, work_item_id: str, dependency_ids: list[str], spec_id: str) -> dict[str, object]:
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
    return json.loads(json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False))


class DecompositionService:
    """Obtain, validate, and atomically persist a PM's child-Agent Spec plan."""

    def __init__(self, session_factory: Callable[[], Session], agent: AgentGateway) -> None:
        self._session_factory, self._agent = session_factory, agent

    async def convert(self, project_id: str) -> list[AgentSpec]:
        prepared = await self.prepare(project_id)
        try:
            return self._persist_direct(prepared)
        except Exception as error:
            self._record_failures(prepared.project_id, prepared.agent_call_ids, error)
            raise

    async def prepare(self, project_id: str, *, command_id: str | None = None, input_hash: str | None = None) -> PreparedDecomposition:
        """Perform PM work outside the final write transaction and retain its evidence."""
        with self._session_factory() as db:
            project = self._project(db, project_id)
            version = self._approved_version(db, project)
            pm = self._pm_session(db, project_id)
            payload = {
                "project_id": project.id,
                "spec_version_id": version.id,
                "spec_content_hash": version.content_hash,
                "approved_spec": version.content,
                "input_refs": list(version.input_refs),
            }
            if command_id is not None:
                payload["command_id"] = command_id
            if input_hash is not None:
                payload["input_hash"] = input_hash
            call = AgentCall(id=_new_id(), project_id=project_id, agent_session_id=pm.id, operation="decompose_spec", request=payload, status="PENDING")
            db.add(call)
            db.commit()
            call_id = call.id

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
        snapshot = ApprovedSpecSnapshot(version.id, version.content, tuple(version.input_refs), version.content_hash)
        try:
            validate_breakdown(breakdown, snapshot)
            with self._session_factory() as db:
                self._assert_new_local_keys(db, project_id, breakdown)
        except BreakdownValidationError as error:
            self._record_failure(project_id, call_id, error)
            raise PreparedBreakdownValidationError(error, [call_id]) from error
        except Exception as error:
            self._record_failure(project_id, call_id, error)
            raise
        reviewer_payload = self._reviewer_payload(project_id, snapshot, breakdown, command_id=command_id, input_hash=input_hash)
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
            raise BreakdownReviewerFailure(str(error), [call_id, reviewer_call_id]) from error
        with self._session_factory() as db:
            persisted_review_call = db.get(AgentCall, reviewer_call_id)
            if persisted_review_call is None:
                raise RuntimeError("breakdown reviewer Agent call disappeared")
            persisted_review_call.status = "RESULT_READY"
            persisted_review_call.response = review.model_dump(mode="json")
            persisted_review_call.completed_at = _now()
            db.commit()
        if self._review_blocks(review):
            implicated = self._review_implicated_path(review)
            error = BreakdownValidationError("SEMANTIC_REVIEW_BLOCKED", implicated, "Reviewer found a blocking semantic conflict")
            raise SemanticReviewRejected(error, [call_id, reviewer_call_id])
        return PreparedDecomposition(project_id, snapshot, breakdown, call_id, reviewer_call_id)

    def _persist_direct(self, prepared: PreparedDecomposition) -> list[AgentSpec]:
        with self._session_factory() as db:
            with db.begin():
                project = self._project(db, prepared.project_id)
                version = self._approved_version(db, project)
                if version.id != prepared.approved_spec.id:
                    raise DecompositionNotAllowed("approved Spec changed before conversion")
                self._assert_new_local_keys(db, project.id, prepared.breakdown)
                specs = self._persist(db.add, db.flush, project, prepared.approved_spec, prepared.breakdown)
                project.phase = ProjectPhase.AGENT_SPECS_READY.value
                project.state_version += 1
                self._mark_call_succeeded(db, prepared.pm_agent_call_id, project.id, "decompose_spec")
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
            "source_spec_version_id": snapshot.id,
            "source_spec_content_hash": snapshot.content_hash,
            "approved_spec_exclusions": list(snapshot.content.get("exclusions", [])),
            "approved_spec": json.loads(json.dumps(
                dict(snapshot.content), sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            )),
            "agent_spec_semantic_content": [
                {
                    "work_item_key": proposal.work_item_key,
                    "fixed_constraints": proposal.fixed_constraints,
                    "configurable_parts": proposal.configurable_parts,
                    "extension_points": proposal.extension_points,
                }
                for proposal in breakdown.agent_specs
            ],
            "canonical_breakdown": canonical_breakdown,
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
            content = _normalized_agent_spec_content(proposal, work_item_id=id_by_key[proposal.work_item_key], dependency_ids=dependencies, spec_id=approved_spec.id)
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
                    prepared = await service.prepare(context.project_id, command_id=context.request.command_id, input_hash=context.input_hash)
                except SemanticReviewRejected as error:
                    raise CommandHandlerRejected(
                        str(error), agent_call_ids=list(error.agent_call_ids),
                        audit_payload={"decomposition_error_code": error.code},
                    ) from error
                except (PreparedBreakdownValidationError, DecompositionAgentFailure, BreakdownReviewerFailure) as error:
                    call_ids = list(error.agent_call_ids)
                    raise CommandHandlerFailure(str(error), agent_call_ids=call_ids, audit_payload={"decomposition_error_code": getattr(error, "code", "AGENT_FAILURE")}) from error
                return PreparedCommand(payload={"approved_spec_id": prepared.approved_spec.id, "approved_spec": dict(prepared.approved_spec.content), "input_refs": list(prepared.approved_spec.input_refs), "spec_content_hash": prepared.approved_spec.content_hash, "breakdown": prepared.breakdown.model_dump(mode="json")}, agent_backed=True, agent_call_ids=prepared.agent_call_ids, audit_payload={"decomposition_agent_call_ids": prepared.agent_call_ids})

            def materialize(self, uow, context, prepared):
                if context.state.current_spec_version_id != prepared.payload["approved_spec_id"]:
                    raise DecompositionNotAllowed("approved Spec changed before conversion")
                snapshot = ApprovedSpecSnapshot(str(prepared.payload["approved_spec_id"]), prepared.payload["approved_spec"], tuple(prepared.payload["input_refs"]), str(prepared.payload["spec_content_hash"]))
                breakdown = WorkBreakdown.model_validate(prepared.payload["breakdown"])
                return service._materialize_command(uow, context, snapshot, breakdown, prepared.agent_call_ids)

        return _Handler()

    def _materialize_command(self, uow, context, approved_spec: ApprovedSpecSnapshot, breakdown: WorkBreakdown, agent_call_ids: list[str]):
        """Materialize using the narrow command UOW without mutating protected state."""
        from app.services.command_service import CommandHandlerResult
        validate_breakdown(breakdown, approved_spec)
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
            content = _normalized_agent_spec_content(proposal, work_item_id=id_by_key[proposal.work_item_key], dependency_ids=dependency_ids, spec_id=approved_spec.id)
            spec = AgentSpec(id=_new_id(), project_id=context.project_id, work_item_id=id_by_key[proposal.work_item_key], source_spec_version_id=approved_spec.id, dependency_work_item_ids=dependency_ids, content=content, content_hash=_canonical_hash(content))
            uow.add_agent_spec(spec)
            specs.append(spec)
        if len(agent_call_ids) != 2:
            raise ValueError("breakdown materialization requires PM and Reviewer Agent call evidence")
        uow.mark_decomposition_call_succeeded(agent_call_ids[0])
        uow.mark_breakdown_reviewer_call_succeeded(agent_call_ids[1])
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

