"""Detached, deterministic read models for workflow resources."""

from collections.abc import Callable
from datetime import datetime, timezone
import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from app.database.models import (
    AgentSpec,
    AgentCall,
    AgentRuntimeEvent,
    AgentSession,
    AuditEvent,
    Project,
    SpecReview,
    SpecVersion,
    WorkItem,
    WorkItemDependency,
    WorkItemRun,
)
from app.schemas.workflow import SessionState
from app.services.execution_graph import (
    available_actions,
    collapse_runs,
    graph_depths,
    startable_work_item_ids,
)
from app.services.state_projection import project_state


AGENT_OPERATION_SUMMARIES = {
    "analyze_brief": "Analyzing project brief completeness",
    "generate_spec": "Generating a project specification",
    "generate_prd_prototype": "Generating an interactive HTML prototype",
    "review_spec": "Reviewing specification quality",
    "decompose_spec": "Decomposing the approved specification",
    "plan_task": "Defining task implementation steps and interfaces",
    "review_breakdown": "Reviewing work-item decomposition",
    "rewrite_prd": "Applying authoritative PRD review comments",
    "restore_spec": "Restoring historical specification content",
}


class SessionSummaryRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    project_id: str
    root_work_item_id: str | None
    title: str


class ReviewRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    project_id: str
    spec_version_id: str
    kind: str
    reviewer_id: str
    input_spec_hash: str
    verdict: str
    findings: list[dict[str, object]]
    comments: str | None
    command_id: str | None
    created_at: datetime


class SpecVersionRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    project_id: str
    revision: int
    content: dict[str, object]
    markdown: str
    generation_source: str
    input_refs: list[str]
    generator_agent_session_id: str
    generator_call_id: str
    parent_version_id: str | None
    change_summary: str
    content_hash: str
    status: str
    reviews: list[ReviewRead]
    created_at: datetime


class WorkItemRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    session_id: str | None
    project_id: str | None
    parent_id: str | None
    type: str | None
    department: str | None
    local_key: str | None
    kind: str | None
    executable: bool | None
    title: str | None
    description: str | None
    summary: str | None
    spec: str | None
    objective: str | None
    status: str | None
    scope: list[str] | None
    exclusions: list[str] | None
    inputs: list[object] | None
    outputs: list[object] | None
    acceptance_criteria: list[object] | None
    required_skills: list[str] | None
    responsible_role: str | None
    suggested_assignee: str | None
    dependency_work_item_ids: list[str] = Field(default_factory=list)
    available_actions: list[str] = Field(default_factory=list)
    graph_depth: int = 0


class AgentSpecRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    project_id: str
    work_item_id: str
    source_spec_version_id: str
    dependency_work_item_ids: list[str]
    content: dict[str, object]
    content_hash: str
    created_at: datetime


class AuditEventRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    project_id: str
    session_id: str | None
    event_type: str
    actor_id: str | None
    payload: dict[str, object]
    created_at: datetime


class AgentRuntimeRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_session_id: str
    project_id: str
    role: str
    provider: str | None
    model: str | None
    purpose: str | None
    status: Literal["running", "completed", "error"]
    current_operation: str
    current_summary: str
    current_call_id: str
    started_at: datetime
    completed_at: datetime | None
    call_count: int


class AgentRuntimeEventRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    project_id: str
    agent_session_id: str
    agent_call_id: str
    operation: str
    event_type: str
    item_type: str | None
    status: str | None
    title: str
    detail: str | None
    payload: dict[str, object]
    created_at: datetime


class QueryService:
    """Read workflow state without returning attached SQLAlchemy instances."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def sessions(self) -> list[SessionSummaryRead]:
        """List persisted projects for the shared board without invoking agents."""
        with self._session_factory() as db:
            projects = db.query(Project).order_by(Project.created_at, Project.id).all()
            roots = {
                item.project_id: item
                for item in db.query(WorkItem).filter_by(kind="ROOT").order_by(WorkItem.id).all()
            }
            return [SessionSummaryRead(
                session_id=project.session_id,
                project_id=project.id,
                root_work_item_id=roots[project.id].id if project.id in roots else None,
                title=(roots[project.id].title if project.id in roots else None)
                or (project.brief or {}).get("final_objective") or project.session_id,
            ) for project in projects]

    def state(self, session_id: str) -> SessionState:
        with self._session_factory() as db:
            project = self._project(db, session_id)
            return project_state(db, project)

    def specs(self, session_id: str) -> list[SpecVersionRead]:
        with self._session_factory() as db:
            project = self._project(db, session_id)
            versions = (
                db.query(SpecVersion)
                .filter_by(project_id=project.id)
                .order_by(SpecVersion.revision, SpecVersion.id)
                .all()
            )
            return [self._spec_read(db, item) for item in versions]

    def spec(self, session_id: str, version: str) -> SpecVersionRead:
        with self._session_factory() as db:
            project = self._project(db, session_id)
            query = db.query(SpecVersion).filter_by(project_id=project.id)
            item = query.filter_by(revision=int(version)).one_or_none() if version.isdigit() else query.filter_by(id=version).one_or_none()
            if item is None:
                raise KeyError(f"Spec not found: {version}")
            return self._spec_read(db, item)

    def work_items(self, session_id: str) -> list[WorkItemRead]:
        with self._session_factory() as db:
            project = self._project(db, session_id)
            items = (
                db.query(WorkItem)
                .filter_by(project_id=project.id)
                .order_by(WorkItem.local_key, WorkItem.id)
                .all()
            )
            kind_order = {"ROOT": 0, "MILESTONE": 1, "TASK": 2}
            items.sort(
                key=lambda item: (
                    kind_order.get(item.kind or "", 99),
                    item.local_key or "",
                    item.id,
                )
            )
            dependencies, status_by_item, ready_ids, depths = self._execution_view(
                db, project.id, items
            )
            return [
                self._work_item_read(
                    item,
                    dependencies.get(item.id, []),
                    status=status_by_item.get(item.id, item.status or "todo"),
                    actions=available_actions(
                        item.id,
                        bool(item.executable),
                        status_by_item.get(item.id, "todo"),
                        ready_ids,
                    ),
                    depth=depths.get(item.id, 0),
                )
                for item in items
            ]

    def work_item(self, session_id: str, work_item_id: str) -> WorkItemRead:
        with self._session_factory() as db:
            project = self._project(db, session_id)
            item = db.get(WorkItem, work_item_id)
            if item is None or item.project_id != project.id:
                raise KeyError(f"WorkItem not found: {work_item_id}")
            dependencies, status_by_item, ready_ids, depths = self._execution_view(
                db, project.id, [item]
            )
            status = status_by_item.get(item.id, item.status or "todo")
            return self._work_item_read(
                item,
                dependencies.get(item.id, []),
                status=status,
                actions=available_actions(
                    item.id, bool(item.executable), status, ready_ids
                ),
                depth=depths.get(item.id, 0),
            )

    def agent_specs(self, session_id: str) -> list[AgentSpecRead]:
        with self._session_factory() as db:
            project = self._project(db, session_id)
            items = (
                db.query(AgentSpec)
                .filter_by(project_id=project.id)
                .order_by(AgentSpec.work_item_id, AgentSpec.id)
                .all()
            )
            return [self._agent_spec_read(item) for item in items]

    def agent_spec(self, session_id: str, agent_spec_id: str) -> AgentSpecRead:
        with self._session_factory() as db:
            project = self._project(db, session_id)
            item = db.get(AgentSpec, agent_spec_id)
            if item is None or item.project_id != project.id:
                raise KeyError(f"AgentSpec not found: {agent_spec_id}")
            return self._agent_spec_read(item)

    def events(self, session_id: str) -> list[AuditEventRead]:
        with self._session_factory() as db:
            project = self._project(db, session_id)
            items = (
                db.query(AuditEvent)
                .filter_by(project_id=project.id)
                .order_by(AuditEvent.created_at, AuditEvent.id)
                .all()
            )
            public_events = [
                AuditEventRead(
                    id=item.id,
                    project_id=item.project_id,
                    session_id=item.session_id,
                    event_type=item.event_type,
                    actor_id=item.actor_id,
                    payload=self._public_event_payload(dict(item.payload or {})),
                    created_at=item.created_at,
                )
                for item in items
            ]
            calls = (
                db.query(AgentCall)
                .filter_by(project_id=project.id)
                .order_by(AgentCall.started_at, AgentCall.id)
                .all()
            )
            public_events.extend(
                self._agent_trace_read(project, call, sequence)
                for sequence, call in enumerate(calls, start=1)
            )
            public_events.sort(key=lambda item: (item.created_at, item.id))
            return public_events

    def agent_runtime(self, session_id: str) -> list[AgentRuntimeRead]:
        with self._session_factory() as db:
            project = self._project(db, session_id)
            ranked_calls = (
                db.query(
                    AgentCall.id.label("current_call_id"),
                    AgentCall.project_id.label("project_id"),
                    AgentCall.agent_session_id.label("agent_session_id"),
                    AgentCall.operation.label("current_operation"),
                    AgentCall.status.label("stored_status"),
                    AgentCall.started_at.label("started_at"),
                    AgentCall.completed_at.label("completed_at"),
                    func.count(AgentCall.id).over(
                        partition_by=AgentCall.agent_session_id
                    ).label("call_count"),
                    func.row_number().over(
                        partition_by=AgentCall.agent_session_id,
                        order_by=(AgentCall.started_at.desc(), AgentCall.id.desc()),
                    ).label("latest_rank"),
                )
                .filter(AgentCall.project_id == project.id)
                .subquery()
            )
            rows = (
                db.query(
                    AgentSession.id.label("agent_session_id"),
                    AgentSession.project_id.label("project_id"),
                    AgentSession.role.label("role"),
                    AgentSession.provider.label("provider"),
                    AgentSession.model.label("model"),
                    AgentSession.purpose.label("purpose"),
                    ranked_calls.c.stored_status,
                    ranked_calls.c.current_operation,
                    ranked_calls.c.current_call_id,
                    ranked_calls.c.started_at,
                    ranked_calls.c.completed_at,
                    ranked_calls.c.call_count,
                )
                .join(
                    ranked_calls,
                    and_(
                        ranked_calls.c.agent_session_id == AgentSession.id,
                        ranked_calls.c.project_id == AgentSession.project_id,
                    ),
                )
                .filter(
                    AgentSession.project_id == project.id,
                    ranked_calls.c.latest_rank == 1,
                )
                .order_by(AgentSession.created_at, AgentSession.id)
                .all()
            )
            return [
                AgentRuntimeRead(
                    agent_session_id=row.agent_session_id,
                    project_id=row.project_id,
                    role=row.role,
                    provider=row.provider,
                    model=row.model,
                    purpose=row.purpose,
                    status=self._agent_runtime_status(row.stored_status),
                    current_operation=row.current_operation,
                    current_summary=self._agent_operation_summary(
                        row.current_operation
                    ),
                    current_call_id=row.current_call_id,
                    started_at=self._as_utc(row.started_at),
                    completed_at=(
                        self._as_utc(row.completed_at)
                        if row.completed_at is not None
                        else None
                    ),
                    call_count=row.call_count,
                )
                for row in rows
            ]

    def agent_runtime_events(
        self, session_id: str, agent_session_id: str
    ) -> list[AgentRuntimeEventRead]:
        """Return the complete sanitized Codex event timeline for one Agent."""

        with self._session_factory() as db:
            project = self._project(db, session_id)
            agent = db.get(AgentSession, agent_session_id)
            if agent is None or agent.project_id != project.id:
                raise KeyError("Agent session not found for project")
            rows = (
                db.query(AgentRuntimeEvent)
                .filter_by(
                    project_id=project.id,
                    agent_session_id=agent_session_id,
                )
                .order_by(AgentRuntimeEvent.created_at, AgentRuntimeEvent.id)
                .all()
            )
            return [
                AgentRuntimeEventRead(
                    id=row.id,
                    project_id=row.project_id,
                    agent_session_id=row.agent_session_id,
                    agent_call_id=row.agent_call_id,
                    operation=row.operation,
                    event_type=row.event_type,
                    item_type=row.item_type,
                    status=row.status,
                    title=row.title,
                    detail=row.detail,
                    payload=dict(row.payload or {}),
                    created_at=self._as_utc(row.created_at),
                )
                for row in rows
            ]

    @staticmethod
    def _safe_hash(value: object) -> str:
        canonical = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @classmethod
    def _agent_trace_read(
        cls, project: Project, call: AgentCall, sequence: int
    ) -> AuditEventRead:
        request = dict(call.request or {})
        done_statuses = {"RESULT_READY", "SUCCEEDED", "COMPLETED", "NO_CHANGE"}
        status = (
            "done"
            if call.status in done_statuses
            else "error"
            if call.status in {"FAILED", "AMBIGUOUS"}
            else "running"
        )
        safe_error_code = (
            "AGENT_CALL_AMBIGUOUS"
            if call.status == "AMBIGUOUS"
            else "AGENT_CALL_FAILED"
            if call.status == "FAILED"
            else None
        )
        trace_id = str(request.get("command_id") or f"agent-{call.id}")
        return AuditEventRead(
            id=f"agent-trace:{call.id}",
            project_id=project.id,
            session_id=project.session_id,
            event_type="AGENT_TRACE",
            actor_id=None,
            payload={
                "trace_id": trace_id,
                "agent_call_id": call.id,
                "sequence": sequence,
                "phase": call.operation,
                "summary": cls._agent_operation_summary(call.operation),
                "input_hash": cls._safe_hash(request),
                "output_hash": (
                    cls._safe_hash(call.response) if call.response is not None else None
                ),
                "status": status,
                "started_at": call.started_at.isoformat(),
                "completed_at": (
                    call.completed_at.isoformat() if call.completed_at else None
                ),
                "safe_error_code": safe_error_code,
            },
            created_at=call.started_at,
        )

    @staticmethod
    def _agent_runtime_status(
        status: str,
    ) -> Literal["running", "completed", "error"]:
        if status in {"FAILED", "AMBIGUOUS"}:
            return "error"
        if status in {"RESULT_READY", "SUCCEEDED", "COMPLETED", "NO_CHANGE"}:
            return "completed"
        return "running"

    @staticmethod
    def _agent_operation_summary(operation: str) -> str:
        return AGENT_OPERATION_SUMMARIES.get(operation, "Processing an Agent stage")

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        return (
            value.replace(tzinfo=timezone.utc)
            if value.tzinfo is None
            else value.astimezone(timezone.utc)
        )

    @staticmethod
    def _public_event_payload(value: object, *, key: str | None = None) -> object:
        """Redact diagnostic fields from both current and historical public events."""

        if key is not None and key.lower() in {
            "error",
            "stderr",
            "stdout",
            "traceback",
            "stacktrace",
            "details",
        }:
            return "Diagnostic detail is restricted."
        if isinstance(value, dict):
            return {
                str(child_key): QueryService._public_event_payload(
                    child_value, key=str(child_key)
                )
                for child_key, child_value in value.items()
            }
        if isinstance(value, list):
            return [QueryService._public_event_payload(item) for item in value]
        return value

    @staticmethod
    def _project(db: Session, session_id: str) -> Project:
        project = db.query(Project).filter_by(session_id=session_id).one_or_none()
        if project is None:
            raise KeyError(f"session not found: {session_id}")
        return project

    @staticmethod
    def _current_spec(db: Session, project: Project) -> SpecVersion | None:
        return db.get(SpecVersion, project.current_spec_version_id) if project.current_spec_version_id else None

    @staticmethod
    def _spec_read(db: Session, item: SpecVersion) -> SpecVersionRead:
        reviews = (
            db.query(SpecReview)
            .filter_by(spec_version_id=item.id)
            .order_by(SpecReview.created_at, SpecReview.id)
            .all()
        )
        return SpecVersionRead(
            id=item.id,
            project_id=item.project_id,
            revision=item.revision,
            content=dict(item.content),
            markdown=item.markdown,
            generation_source=item.generation_source,
            input_refs=list(item.input_refs),
            generator_agent_session_id=item.generator_agent_session_id,
            generator_call_id=item.generator_call_id,
            parent_version_id=item.parent_version_id,
            change_summary=item.change_summary,
            content_hash=item.content_hash,
            status=item.status,
            reviews=[
                ReviewRead(
                    id=review.id,
                    project_id=review.project_id,
                    spec_version_id=review.spec_version_id,
                    kind=review.kind,
                    reviewer_id=review.reviewer_id,
                    input_spec_hash=review.input_spec_hash,
                    verdict=review.verdict,
                    findings=list(review.findings or []),
                    comments=review.comments,
                    command_id=review.command_id,
                    created_at=review.created_at,
                )
                for review in reviews
            ],
            created_at=item.created_at,
        )

    @staticmethod
    def _dependencies(db: Session, project_id: str) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        for edge in (
            db.query(WorkItemDependency)
            .filter_by(project_id=project_id)
            .order_by(
                WorkItemDependency.from_work_item_id,
                WorkItemDependency.to_work_item_id,
                WorkItemDependency.id,
            )
            .all()
        ):
            result.setdefault(edge.from_work_item_id, []).append(edge.to_work_item_id)
        return result

    @classmethod
    def _execution_view(
        cls, db: Session, project_id: str, items: list[WorkItem]
    ) -> tuple[dict[str, list[str]], dict[str, str], set[str], dict[str, int]]:
        """Derive run status, ready set and lane depth for the dependency DAG.

        Returns ``(dependencies, status_by_item, ready_ids, depths)``. Both the
        projection and the command handlers must read status through here so
        the UI can never offer an action the engine would reject.
        """

        runs = (
            db.query(WorkItemRun)
            .filter_by(project_id=project_id)
            .order_by(
                WorkItemRun.work_item_id,
                WorkItemRun.created_at,
                WorkItemRun.id,
            )
            .all()
        )
        status_by_item = collapse_runs(runs)
        dependencies = cls._dependencies(db, project_id)
        known_ids = {item.id for item in items if item.id is not None}
        executable_ids = {item.id for item in items if item.executable and item.id}
        ready_ids = set(
            startable_work_item_ids(executable_ids, dependencies, status_by_item)
        )
        depths = graph_depths(known_ids, dependencies)
        return dependencies, status_by_item, ready_ids, depths

    @staticmethod
    def _work_item_read(
        item: WorkItem,
        dependencies: list[str],
        *,
        status: str | None = None,
        actions: list[str] | None = None,
        depth: int = 0,
    ) -> WorkItemRead:
        return WorkItemRead(
            id=item.id,
            session_id=item.session_id,
            project_id=item.project_id,
            parent_id=item.parent_id,
            type=item.type,
            department=item.department,
            local_key=item.local_key,
            kind=item.kind,
            executable=item.executable,
            title=item.title,
            description=item.description,
            summary=item.summary,
            spec=item.spec,
            objective=item.objective,
            status=status if status is not None else item.status,
            scope=list(item.scope) if item.scope is not None else None,
            exclusions=list(item.exclusions) if item.exclusions is not None else None,
            inputs=list(item.inputs) if item.inputs is not None else None,
            outputs=list(item.outputs) if item.outputs is not None else None,
            acceptance_criteria=(
                list(item.acceptance_criteria)
                if item.acceptance_criteria is not None
                else None
            ),
            required_skills=(
                list(item.required_skills) if item.required_skills is not None else None
            ),
            responsible_role=item.responsible_role,
            suggested_assignee=item.suggested_assignee,
            dependency_work_item_ids=list(dependencies),
            available_actions=list(actions or []),
            graph_depth=depth,
        )

    @staticmethod
    def _agent_spec_read(item: AgentSpec) -> AgentSpecRead:
        return AgentSpecRead(
            id=item.id,
            project_id=item.project_id,
            work_item_id=item.work_item_id,
            source_spec_version_id=item.source_spec_version_id,
            dependency_work_item_ids=list(item.dependency_work_item_ids),
            content=dict(item.content),
            content_hash=item.content_hash,
            created_at=item.created_at,
        )
