"""Generate and persist one isolated HTML prototype for each PRD version."""

from collections.abc import Callable
import hashlib
from uuid import uuid4

from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.agents.gateway import AgentGateway
from app.database.models import (
    AgentCall,
    AgentSession,
    AuditEvent,
    PrdPrototype,
    PrdVersion,
    Project,
    SpecVersion,
    utc_now,
)
from app.domain.types import HtmlPrototypePayload


_PUBLIC_FAILURE = "HTML 原型生成失败，可在生成下一版 PRD 后重试。"


def _new_id() -> str:
    return str(uuid4())


class PrdPrototypeService:
    """Derive a prototype without mutating or blocking the reviewed Spec."""

    def __init__(
        self, session_factory: Callable[[], Session], agent: AgentGateway
    ) -> None:
        self._session_factory = session_factory
        self._agent = agent

    @property
    def enabled(self) -> bool:
        return bool(getattr(self._agent, "prototype_enabled", False))

    async def ensure_for_project(self, project_id: str) -> PrdPrototype | None:
        """Generate at most once for the project's current immutable Spec."""

        if not self.enabled:
            return None
        with self._session_factory() as db:
            project = db.get(Project, project_id)
            spec = (
                db.get(SpecVersion, project.current_spec_version_id)
                if project is not None and project.current_spec_version_id
                else None
            )
            if project is None or spec is None or spec.project_id != project.id:
                raise ValueError("current Project Spec is unavailable for prototype generation")
            existing = (
                db.query(PrdPrototype)
                .filter_by(spec_version_id=spec.id)
                .one_or_none()
            )
            if existing is not None:
                return existing
            request = {
                "project_id": project.id,
                "spec_version_id": spec.id,
                "spec_revision": spec.revision,
                "spec_content_hash": spec.content_hash,
                "spec": dict(spec.content),
                "prd_markdown": spec.markdown,
                "constraints": {
                    "prd_is_read_only": True,
                    "single_file_html": True,
                    "network_access_from_prototype": False,
                },
            }
            reusable = next(
                (
                    call
                    for call in db.query(AgentCall)
                    .filter_by(
                        project_id=project.id,
                        operation="generate_prd_prototype",
                        status="RESULT_READY",
                    )
                    .order_by(desc(AgentCall.started_at), desc(AgentCall.id))
                    .all()
                    if call.request == request
                ),
                None,
            )
            if reusable is not None:
                call_id = reusable.id
                output = HtmlPrototypePayload.model_validate(reusable.response)
            else:
                agent_session = self._agent_session(db, project.id)
                call = AgentCall(
                    id=_new_id(),
                    project_id=project.id,
                    agent_session_id=agent_session.id,
                    operation="generate_prd_prototype",
                    request=request,
                    status="PENDING",
                )
                db.add(call)
                db.commit()
                call_id = call.id
                output = None

        if output is None:
            try:
                output = await self._agent.generate_prd_prototype(request)
            except Exception as error:
                return self._record_failure(project_id, spec.id, call_id, error)
            with self._session_factory() as db:
                call = db.get(AgentCall, call_id)
                if call is None or call.project_id != project_id or call.status != "PENDING":
                    raise RuntimeError("prototype Agent call is no longer pending")
                call.response = output.model_dump(mode="json")
                call.status = "RESULT_READY"
                call.completed_at = utc_now()
                db.commit()

        digest = hashlib.sha256(output.html.encode("utf-8")).hexdigest()
        with self._session_factory() as db:
            existing = (
                db.query(PrdPrototype)
                .filter_by(spec_version_id=spec.id)
                .one_or_none()
            )
            if existing is not None:
                return existing
            call = db.get(AgentCall, call_id)
            project = db.get(Project, project_id)
            if (
                call is None
                or call.status != "RESULT_READY"
                or project is None
                or project.current_spec_version_id != spec.id
            ):
                raise RuntimeError("prototype source changed before persistence")
            prototype = PrdPrototype(
                id=_new_id(),
                project_id=project_id,
                spec_version_id=spec.id,
                generator_agent_session_id=call.agent_session_id,
                generator_call_id=call.id,
                status="ready",
                title=output.title,
                html=output.html,
                content_hash=digest,
                generation_summary=output.generation_summary,
            )
            call.status = "SUCCEEDED"
            db.add(prototype)
            db.add(
                AuditEvent(
                    id=_new_id(),
                    project_id=project_id,
                    session_id=project.session_id,
                    event_type="PRD_PROTOTYPE_GENERATED",
                    actor_id=None,
                    payload={
                        "spec_version_id": spec.id,
                        "prototype_id": prototype.id,
                        "content_hash": digest,
                        "agent_call_id": call.id,
                    },
                )
            )
            db.commit()
            return prototype

    async def ensure_for_prd(self, wi: str, version: int) -> PrdPrototype | None:
        """Generate a prototype for the requested current PRD version on demand."""

        with self._session_factory() as db:
            binding = db.get(PrdVersion, (wi, version))
            spec = (
                db.get(SpecVersion, binding.spec_version_id)
                if binding is not None
                else None
            )
            project = db.get(Project, spec.project_id) if spec is not None else None
            if (
                binding is None
                or spec is None
                or project is None
                or project.current_spec_version_id != spec.id
            ):
                raise ValueError("manual prototype generation requires the current PRD version")
            project_id = project.id
        return await self.ensure_for_project(project_id)

    def automatic_generation_allowed(self, project_id: str) -> bool:
        """Return false when the current PRD has any review finding."""

        from app.database.models import SpecReview

        with self._session_factory() as db:
            project = db.get(Project, project_id)
            if project is None or project.current_spec_version_id is None:
                return False
            reviews = db.query(SpecReview).filter_by(
                spec_version_id=project.current_spec_version_id
            ).all()
            return not any(review.findings for review in reviews)

    @staticmethod
    def _agent_session(db: Session, project_id: str) -> AgentSession:
        session = (
            db.query(AgentSession)
            .filter_by(project_id=project_id, role="PROTOTYPE_DESIGNER")
            .one_or_none()
        )
        if session is None:
            session = AgentSession(
                id=_new_id(),
                project_id=project_id,
                role="PROTOTYPE_DESIGNER",
                purpose="Generate an isolated HTML prototype from the frozen PRD",
                metadata_json={"provider": "21st-magic-mcp"},
            )
            db.add(session)
            db.flush()
        return session

    def _record_failure(
        self, project_id: str, spec_version_id: str, call_id: str, error: Exception
    ) -> PrdPrototype:
        with self._session_factory() as db:
            call = db.get(AgentCall, call_id)
            project = db.get(Project, project_id)
            if call is None or project is None:
                raise RuntimeError("prototype failure context disappeared") from error
            call.status = "FAILED"
            call.error = str(error)
            call.completed_at = utc_now()
            prototype = PrdPrototype(
                id=_new_id(),
                project_id=project_id,
                spec_version_id=spec_version_id,
                generator_agent_session_id=call.agent_session_id,
                generator_call_id=call.id,
                status="failed",
                error_code="PROTOTYPE_GENERATION_FAILED",
                error_message=_PUBLIC_FAILURE,
            )
            db.add(prototype)
            db.add(
                AuditEvent(
                    id=_new_id(),
                    project_id=project_id,
                    session_id=project.session_id,
                    event_type="PRD_PROTOTYPE_GENERATION_FAILED",
                    actor_id=None,
                    payload={
                        "spec_version_id": spec_version_id,
                        "prototype_id": prototype.id,
                        "agent_call_id": call.id,
                        "error_code": "PROTOTYPE_GENERATION_FAILED",
                    },
                )
            )
            db.commit()
            return prototype
