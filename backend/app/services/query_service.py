"""Detached, deterministic read models for workflow resources."""

from collections.abc import Callable
from datetime import datetime
import hashlib
import json

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.database.models import (
    AgentSpec,
    AgentCall,
    AuditEvent,
    Project,
    SpecReview,
    SpecVersion,
    WorkItem,
    WorkItemDependency,
)
from app.schemas.workflow import SessionState
from app.services.state_projection import project_state


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


class QueryService:
    """Read workflow state without returning attached SQLAlchemy instances."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

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
            dependencies = self._dependencies(db, project.id)
            return [self._work_item_read(item, dependencies.get(item.id, [])) for item in items]

    def work_item(self, session_id: str, work_item_id: str) -> WorkItemRead:
        with self._session_factory() as db:
            project = self._project(db, session_id)
            item = db.get(WorkItem, work_item_id)
            if item is None or item.project_id != project.id:
                raise KeyError(f"WorkItem not found: {work_item_id}")
            dependencies = self._dependencies(db, project.id)
            return self._work_item_read(item, dependencies.get(item.id, []))

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
        summaries = {
            "analyze_brief": "Analyzing project brief completeness",
            "generate_spec": "Generating a project specification",
            "review_spec": "Reviewing specification quality",
            "decompose_spec": "Decomposing the approved specification",
            "plan_task": "Defining task implementation steps and interfaces",
            "review_breakdown": "Reviewing work-item decomposition",
            "rewrite_prd": "Applying authoritative PRD review comments",
            "restore_spec": "Restoring historical specification content",
        }
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
                "summary": summaries.get(call.operation, "Processing an Agent stage"),
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

    @staticmethod
    def _work_item_read(item: WorkItem, dependencies: list[str]) -> WorkItemRead:
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
            spec=item.spec,
            objective=item.objective,
            status=item.status,
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
