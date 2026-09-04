"""Enrich existing task plans against a frozen approved PRD, with atomic adoption."""

import asyncio
import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Callable

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.agents.gateway import AgentGateway
from app.database.models import AgentCall, AgentSpec, AuditEvent, Project, SpecVersion, WorkItem, WorkItemDependency
from app.domain.implementation_plan import ImplementationPlan
from app.domain.types import AgentSpecProposal, ReviewVerdict, SemanticReview, WorkBreakdown, WorkItemProposal
from app.services.command_service import assert_reviewer
from app.services.decomposition_service import ApprovedSpecSnapshot, DecompositionService, _canonical_hash, _new_id, _now, validate_breakdown
from app.services.task_plan_graph import dependency_contracts, topological_plan_waves, transitive_dependent_keys
from app.services.task_specifications import requirement_snapshots, validate_implementation_plan


class AgentSpecDetailsError(RuntimeError):
    code = "AGENT_SPEC_DETAILS_FAILED"

    def __init__(self, message: str, agent_call_ids=()):
        self.agent_call_ids = list(agent_call_ids)
        self.agent_call_id = self.agent_call_ids[-1] if self.agent_call_ids else None
        super().__init__(message)


class AgentSpecDetailsNotAllowed(AgentSpecDetailsError):
    code = "AGENT_SPEC_DETAILS_NOT_ALLOWED"


class AgentSpecDetailsSnapshotChanged(AgentSpecDetailsError):
    code = "AGENT_SPEC_DETAILS_SNAPSHOT_CHANGED"


class AgentSpecDetailsAgentFailure(AgentSpecDetailsError):
    code = "AGENT_SPEC_DETAILS_AGENT_FAILED"


class AgentSpecDetailsReviewRejected(AgentSpecDetailsError):
    code = "AGENT_SPEC_DETAILS_REVIEW_REJECTED"


@dataclass
class _Snapshot:
    rows: dict
    approved: ApprovedSpecSnapshot
    breakdown: WorkBreakdown
    specs: list[AgentSpec]


def _row(row):
    return deepcopy({column.name: getattr(row, column.name) for column in row.__table__.columns})


class AgentSpecDetailService:
    def __init__(self, session_factory: Callable[[], Session], agent: AgentGateway):
        self._session_factory, self._agent = session_factory, agent
        self._decomposition = DecompositionService(session_factory, agent)
        self._semaphore = asyncio.Semaphore(2)

    def _snapshot(self, db, project_id, actor_id):
        project = db.get(Project, project_id)
        if project is None:
            raise AgentSpecDetailsNotAllowed("Project does not exist; reload the project before enriching")
        assert_reviewer(project, actor_id)
        version = db.get(SpecVersion, project.current_spec_version_id) if project.current_spec_version_id else None
        if (project.phase != "AGENT_SPECS_READY" or version is None
                or version.project_id != project_id or version.status != "APPROVED"):
            raise AgentSpecDetailsNotAllowed("Enrichment requires AGENT_SPECS_READY and the current APPROVED PRD")
        items = db.query(WorkItem).filter_by(project_id=project_id).order_by(WorkItem.id).all()
        edges = db.query(WorkItemDependency).filter_by(project_id=project_id).order_by(WorkItemDependency.id).all()
        specs = db.query(AgentSpec).filter_by(project_id=project_id).order_by(AgentSpec.id).all()
        frozen = dict(project=_row(project), version=_row(version), items=[_row(x) for x in items],
                      edges=[_row(x) for x in edges], specs=[_row(x) for x in specs])
        approved = ApprovedSpecSnapshot(version.id, deepcopy(version.content), tuple(version.input_refs), version.content_hash)
        try:
            breakdown = self._reconstruct(items, edges, specs, approved)
            validate_breakdown(breakdown, approved, require_implementation_plan=False)
        except (ValueError, KeyError, TypeError) as error:
            raise AgentSpecDetailsNotAllowed(f"Existing task data is inconsistent; correct it before enrichment: {error}") from error
        return _Snapshot(frozen, approved, breakdown, specs)

    @staticmethod
    def _reconstruct(items, edges, specs, approved):
        by_id = {item.id: item for item in items}
        children = [item for item in items if item.kind != "ROOT"]
        keys = {item.id: item.local_key or f"work-item:{item.id}" for item in children}
        dependencies = {item.id: [] for item in children}
        for edge in edges:
            dependencies[edge.from_work_item_id].append(edge.to_work_item_id)
            if edge.to_work_item_id not in keys:
                raise ValueError("dependency points outside the canonical breakdown")
        proposals = []
        for item in children:
            parent = by_id.get(item.parent_id)
            if item.parent_id and parent is None:
                raise ValueError("missing work item parent")
            proposals.append(WorkItemProposal(
                local_key=keys[item.id], parent_key=keys.get(item.parent_id), kind=item.kind,
                title=item.title, objective=item.objective,
                dependency_keys=sorted(keys[dep] for dep in dependencies[item.id]),
            ))
        tasks = []
        for spec in specs:
            content = spec.content
            if (spec.work_item_id not in keys or spec.source_spec_version_id != approved.id
                    or content.get("source_spec_version_id") != approved.id
                    or content.get("work_item_id") != spec.work_item_id
                    or spec.content_hash != _canonical_hash(content)
                    or sorted(spec.dependency_work_item_ids) != sorted(dependencies[spec.work_item_id])
                    or content.get("dependency_work_item_ids") != spec.dependency_work_item_ids):
                raise ValueError(f"AgentSpec {spec.id} has inconsistent source, content hash, task or dependencies")
            fields = {key: deepcopy(value) for key, value in content.items() if key in AgentSpecProposal.model_fields}
            fields.update(work_item_key=keys[spec.work_item_id],
                          dependency_keys=[keys[dep] for dep in spec.dependency_work_item_ids])
            task = AgentSpecProposal.model_validate(fields)
            if "requirements" in content and content["requirements"] != requirement_snapshots(task, dict(approved.content)):
                raise ValueError(f"AgentSpec {spec.id} requirements differ from the approved PRD")
            tasks.append(task)
        return WorkBreakdown(milestones=[p for p in proposals if p.kind == "MILESTONE"],
                             tasks=[p for p in proposals if p.kind == "TASK"],
                             agent_specs=sorted(tasks, key=lambda task: task.work_item_key))

    async def enrich(self, project_id: str, actor_id: str) -> list[AgentSpec]:
        with self._session_factory() as db:
            snapshot = self._snapshot(db, project_id, actor_id)
        missing_keys = {
            task.work_item_key
            for task in snapshot.breakdown.agent_specs
            if task.implementation_plan is None
        }
        target_keys = (
            transitive_dependent_keys(snapshot.breakdown.agent_specs, missing_keys)
            if missing_keys else set()
        )
        targets = [
            task for task in snapshot.breakdown.agent_specs
            if task.work_item_key in target_keys
        ]
        if not targets and all("requirements" in spec.content for spec in snapshot.specs):
            return snapshot.specs
        call_ids, adopted = [], {}
        try:
            previous_review, previous_review_id = None, None
            for repair_round in range(3):
                # Settle every in-flight call before returning a failure; no orphaned PENDING calls.
                target_keys = {task.work_item_key for task in targets}
                for wave in topological_plan_waves(snapshot.breakdown.agent_specs, target_keys):
                    results = await asyncio.gather(*(self._plan(snapshot, task, actor_id, call_ids, repair_round,
                                                             previous_review, previous_review_id)
                                                     for task in wave), return_exceptions=True)
                    for result in results:
                        if isinstance(result, BaseException):
                            raise result
                    for task, (plan, call_id) in zip(wave, results, strict=True):
                        task.implementation_plan = plan
                        adopted[task.work_item_key] = call_id
                validate_breakdown(snapshot.breakdown, snapshot.approved, require_implementation_plan=True)
                plan_agent_call_ids = [
                    adopted[task.work_item_key]
                    for task in snapshot.breakdown.agent_specs
                    if task.work_item_key in adopted
                ]
                payload = self._decomposition._reviewer_payload(project_id, snapshot.approved, snapshot.breakdown,
                                                               command_id=None, input_hash=None)
                payload.update(self._binding(snapshot, actor_id), repair_round=repair_round,
                               plan_agent_call_ids=plan_agent_call_ids, previous_review_call_id=previous_review_id)
                review, reviewer_id = await self._call(project_id, "review_breakdown", payload, call_ids, SemanticReview)
                if not self._decomposition._review_blocks(review):
                    return self._persist(snapshot, actor_id, call_ids, [*plan_agent_call_ids, reviewer_id])
                if repair_round == 2 or not self._decomposition._can_repair_review(review):
                    raise AgentSpecDetailsReviewRejected(
                        "Reviewer blocked enrichment; resolve human decisions or review findings before retrying", call_ids)
                targets = self._repair_targets(snapshot.breakdown, review)
                previous_review, previous_review_id = review.model_dump(mode="json"), reviewer_id
        except Exception as error:
            self._record_failure(snapshot, actor_id, call_ids, error)
            raise
        raise AgentSpecDetailsReviewRejected("Implementation plan revision budget exhausted", call_ids)

    async def review_revised_plans(
        self, project_id: str, actor_id: str, source_review_call_id: str, plans: dict,
    ) -> list[AgentSpec]:
        """Review one operator-supplied full plan map; never restart automatic repair."""
        with self._session_factory() as db:
            snapshot = self._snapshot(db, project_id, actor_id)
        call_ids = []
        try:
            binding = self._binding(snapshot, actor_id)
            with self._session_factory() as db:
                source = db.get(AgentCall, source_review_call_id)
                if (source is None or source.project_id != project_id
                        or source.operation != "review_breakdown" or source.status != "RESULT_READY"):
                    raise AgentSpecDetailsNotAllowed("Source must be a RESULT_READY review_breakdown call for this project")
                request, response = deepcopy(source.request), deepcopy(source.response)
            try:
                source_binding = {key: request[key] for key in binding}
                # A different currently authorized reviewer may submit the correction.
                if any(source_binding[key] != value for key, value in binding.items() if key != "actor_id"):
                    raise AgentSpecDetailsSnapshotChanged("Source review snapshot changed; reload before correcting plans")
                previous_review = SemanticReview.model_validate(response)
                if (previous_review.verdict is not ReviewVerdict.REJECT
                        or not self._decomposition._can_repair_review(previous_review)):
                    raise AgentSpecDetailsNotAllowed("Source review must be rejected with explicit findings and no pending human decision")
                previous = WorkBreakdown.model_validate(request["canonical_breakdown"])
                validate_breakdown(previous, snapshot.approved, require_implementation_plan=True)
                non_plan_fields = {"agent_specs": {"__all__": {"implementation_plan"}}}
                if (previous.model_dump(mode="json", exclude=non_plan_fields)
                        != snapshot.breakdown.model_dump(mode="json", exclude=non_plan_fields)):
                    raise AgentSpecDetailsNotAllowed("Source review changes non-plan task content or scope")
                if request["approved_spec"] != dict(snapshot.approved.content):
                    raise AgentSpecDetailsNotAllowed("Source review approved PRD content does not match the snapshot")
                repair_round = request["repair_round"]
                if type(repair_round) is not int or not 0 <= repair_round <= 2:
                    raise AgentSpecDetailsNotAllowed("Source review has an invalid automatic repair round")
                keys = {task.work_item_key for task in snapshot.breakdown.agent_specs}
                if not isinstance(plans, dict) or set(plans) != keys:
                    raise AgentSpecDetailsNotAllowed("Revised plans must contain exactly the full current task key set")
                # Re-parse models too, so mutated Pydantic instances cannot skip validation.
                candidate_plans = {
                    key: ImplementationPlan.model_validate(
                        plan.model_dump(mode="json") if isinstance(plan, ImplementationPlan) else deepcopy(plan))
                    for key, plan in plans.items()
                }
                for task in snapshot.breakdown.agent_specs:
                    task.implementation_plan = candidate_plans[task.work_item_key]
                validate_breakdown(snapshot.breakdown, snapshot.approved, require_implementation_plan=True)
            except (ValueError, KeyError, TypeError) as error:
                raise AgentSpecDetailsNotAllowed(f"Invalid source review or revised plans: {error}") from error

            payload = self._decomposition._reviewer_payload(
                project_id, snapshot.approved, snapshot.breakdown, command_id=None, input_hash=None)
            payload.update(binding, revision_origin="operator_supplied_plan_corrections",
                           source_review_call_id=source_review_call_id, source_binding=source_binding,
                           candidate_plan_hash=_canonical_hash({key: plan.model_dump(mode="json")
                                                              for key, plan in candidate_plans.items()}),
                           repair_round=repair_round, review_feedback=previous_review.model_dump(mode="json"),
                           previous_review_call_id=source_review_call_id)
            review, reviewer_id = await self._call(project_id, "review_breakdown", payload, call_ids, SemanticReview)
            if self._decomposition._review_blocks(review):
                raise AgentSpecDetailsReviewRejected("Reviewer blocked operator-supplied plan corrections", call_ids)
            # Only this review attests to the exact candidate; prior PM results are not adopted.
            return self._persist(snapshot, actor_id, call_ids, [reviewer_id])
        except Exception as error:
            self._record_failure(snapshot, actor_id, call_ids, error)
            raise

    def _record_failure(self, snapshot, actor_id, call_ids, error):
        if isinstance(error, AgentSpecDetailsError):
            error.agent_call_ids = list(call_ids)
            error.agent_call_id = call_ids[-1] if call_ids else None
        with self._session_factory() as db:
            db.add(AuditEvent(id=_new_id(), project_id=snapshot.rows["project"]["id"], actor_id=actor_id,
                             session_id=snapshot.rows["project"]["session_id"], event_type="AGENT_SPEC_DETAILS_FAILED",
                             payload={"agent_call_ids": call_ids, "message": str(error),
                                      "error_code": getattr(error, "code", "AGENT_SPEC_DETAILS_FAILED")}))
            db.commit()

    @staticmethod
    def _binding(snapshot, actor_id):
        return {"project_id": snapshot.rows["project"]["id"], "actor_id": actor_id,
                "source_spec_version_id": snapshot.approved.id, "source_spec_content_hash": snapshot.approved.content_hash,
                "source_state_version": snapshot.rows["project"]["state_version"],
                "source_snapshot_hash": _canonical_hash(_json_snapshot(snapshot.rows))}

    async def _plan(self, snapshot, task, actor_id, call_ids, repair_round, review, review_id):
        async with self._semaphore:
            item = next(item for item in snapshot.rows["items"]
                        if (item["local_key"] or f"work-item:{item['id']}") == task.work_item_key)
            spec = next(spec for spec in snapshot.specs if spec.work_item_id == item["id"])
            payload = dict(self._binding(snapshot, actor_id), task_spec=task.model_dump(mode="json"),
                           approved_spec=dict(snapshot.approved.content), input_refs=list(snapshot.approved.input_refs),
                           work_item_id=item["id"], parent_work_item_id=item["parent_id"], agent_spec_id=spec.id,
                           planning_guidance=("Preserve the existing scope, outputs and ownership assignments. Refine vague "
                                              "legacy output names/formats in expected_output, interfaces and data definitions "
                                              "using concrete proposed formats; record these choices as PROPOSED design_decisions. "
                                              "Never propagate placeholders as implementation definitions or claim proposed formats "
                                              "are already approved. Do not rewrite the original outputs. Treat dependency_contracts "
                                              "as the only authoritative producer contracts; reuse them exactly or define explicit adapters "
                                              "where needed. related_tasks are plan-free context only. Do not invent renamed equivalent types."),
                           related_tasks=[other.model_dump(mode="json", exclude={"implementation_plan"})
                                          for other in snapshot.breakdown.agent_specs
                                          if other.work_item_key != task.work_item_key], repair_round=repair_round)
            if review is not None:
                payload.update(previous_plan=task.implementation_plan.model_dump(mode="json"),
                               review_feedback=review, previous_review_call_id=review_id)
            contracts = dependency_contracts(
                task, {other.work_item_key: other for other in snapshot.breakdown.agent_specs},
            )
            payload["dependency_contracts"] = contracts
            payload["dependency_contract_hashes"] = {
                str(contract["work_item_key"]): str(contract["contract_hash"])
                for contract in contracts
            }
            approved_ids = {r["requirement_id"] for section in ("functional_requirements", "non_functional_requirements")
                            for r in snapshot.approved.content.get(section, [])}
            plan, call_id = await self._call(snapshot.rows["project"]["id"], "plan_task", payload, call_ids, ImplementationPlan,
                                           lambda plan: validate_implementation_plan(plan, task, approved_ids))
            return plan, call_id

    async def _call(self, project_id, operation, payload, call_ids, output_type, validate=None):
        with self._session_factory() as db:
            session = (self._decomposition._pm_session if operation == "plan_task" else
                       self._decomposition._reviewer_session)(db, project_id)
            call_id = _new_id()
            db.add(AgentCall(id=call_id, project_id=project_id, agent_session_id=session.id,
                             operation=operation, request=deepcopy(payload), status="PENDING"))
            db.commit()
        call_ids.append(call_id)
        response = None
        try:
            result = await getattr(self._agent, operation)(deepcopy(payload))
            response = result.model_dump(mode="json")
            result = output_type.model_validate(response)
            if validate is not None:
                validate(result)
        except BaseException as error:
            with self._session_factory() as db:
                call = db.get(AgentCall, call_id)
                call.status, call.response, call.error, call.completed_at = "FAILED", response, str(error), _now()
                db.commit()
            if not isinstance(error, Exception):
                raise
            raise AgentSpecDetailsAgentFailure(f"{operation} failed; inspect call {call_id} and retry: {error}", [call_id]) from error
        with self._session_factory() as db:
            call = db.get(AgentCall, call_id)
            call.status, call.response, call.completed_at = "RESULT_READY", response, _now()
            db.commit()
        return result, call_id

    @staticmethod
    def _repair_targets(breakdown, review):
        keys = {task.work_item_key for task in breakdown.agent_specs}
        targets = set()
        for finding in review.findings:
            match = re.fullmatch(r"agent_specs\[([^\]]+)\](?:\..+)?", finding.spec_path)
            if match is None or match[1] not in keys:
                return breakdown.agent_specs
            targets.add(match[1])
        targets = transitive_dependent_keys(breakdown.agent_specs, targets)
        return [task for task in breakdown.agent_specs if task.work_item_key in targets]

    def _persist(self, snapshot, actor_id, call_ids, adopted_ids):
        project_id = snapshot.rows["project"]["id"]
        with self._session_factory() as db, db.begin():
            # Acquire the project write lock before checking *any* snapshot rows. The
            # no-op CAS also serializes SQLite writers without touching updated_at.
            cas = db.execute(update(Project).where(Project.id == project_id,
                             Project.state_version == snapshot.rows["project"]["state_version"])
                             .values(state_version=Project.state_version, updated_at=Project.updated_at))
            if cas.rowcount != 1:
                raise AgentSpecDetailsSnapshotChanged("Project state changed; reload and retry enrichment", call_ids)
            try:
                current = self._snapshot(db, project_id, actor_id)
            except Exception as error:
                raise AgentSpecDetailsSnapshotChanged(f"Project snapshot or reviewer permissions changed; reload: {error}", call_ids) from error
            if current.rows != snapshot.rows:
                raise AgentSpecDetailsSnapshotChanged("PRD, permissions, tasks, dependencies or AgentSpecs changed; reload and retry", call_ids)
            by_key = {task.work_item_key: task for task in snapshot.breakdown.agent_specs}
            items = {item["id"]: item for item in snapshot.rows["items"]}
            for spec in current.specs:
                item = items[spec.work_item_id]
                task = by_key[item["local_key"] or f"work-item:{item['id']}"]
                spec.content = {**spec.content, "implementation_plan": task.implementation_plan.model_dump(mode="json"),
                                "requirements": requirement_snapshots(task, dict(snapshot.approved.content))}
                spec.content_hash = _canonical_hash(spec.content)
            project = db.get(Project, project_id)
            project.state_version += 1
            for call_id in adopted_ids:
                call = db.get(AgentCall, call_id)
                if call is None or call.project_id != project_id or call.status != "RESULT_READY":
                    raise AgentSpecDetailsSnapshotChanged("Agent call evidence is no longer ready for adoption", call_ids)
                call.status = "SUCCEEDED"
            db.add(AuditEvent(id=_new_id(), project_id=project_id, session_id=project.session_id, actor_id=actor_id,
                             event_type="AGENT_SPEC_DETAILS_ENRICHED", payload={
                                 **self._binding(snapshot, actor_id), "agent_call_ids": call_ids,
                                 "adopted_agent_call_ids": adopted_ids,
                                 "old_hashes": {spec.id: spec.content_hash for spec in snapshot.specs},
                                 "new_hashes": {spec.id: spec.content_hash for spec in current.specs}}))
        return current.specs


def _json_snapshot(value):
    """Encode timestamp columns in the full durable snapshot binding."""
    if isinstance(value, dict):
        return {key: _json_snapshot(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_snapshot(item) for item in value]
    return value.isoformat() if hasattr(value, "isoformat") else value
