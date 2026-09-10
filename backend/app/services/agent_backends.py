"""Server-side catalogue and durable project selection for Agent backends."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.orm import Session

from app.config import Settings
from app.database.models import AgentCall, AgentSession, AuditEvent, Project
from app.schemas.workflow import (
    AgentBackendName,
    AgentBackendOption,
    AgentBackendSelection,
)
from app.services.command_service import ForbiddenActor


class AgentBackendService:
    """Keep API keys server-side while binding one backend to each project."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        settings: Settings,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings

    def catalogue(self) -> list[AgentBackendOption]:
        return [
            AgentBackendOption(
                provider="codex",
                label="Codex CLI",
                model=self._settings.codex_model,
                available=True,
            ),
            AgentBackendOption(
                provider="deepseek",
                label="DeepSeek",
                model=self._settings.deepseek_model,
                available=bool(self._settings.deepseek_api_key),
                configuration_env="DEEPSEEK_API_KEY",
            ),
            AgentBackendOption(
                provider="glm",
                label="GLM",
                model=self._settings.zhipu_model,
                available=bool(self._settings.zhipu_api_key),
                configuration_env="ZHIPU_API_KEY",
            ),
            AgentBackendOption(
                provider="kimi",
                label="Kimi",
                model=self._settings.moonshot_model,
                available=bool(self._settings.moonshot_api_key),
                configuration_env="MOONSHOT_API_KEY",
            ),
        ]

    def option(self, provider: AgentBackendName) -> AgentBackendOption:
        option = next(
            (item for item in self.catalogue() if item.provider == provider),
            None,
        )
        if option is None:
            raise ValueError("unsupported Agent backend")
        return option

    def ensure_available(self, provider: AgentBackendName) -> AgentBackendOption:
        option = self.option(provider)
        if not option.available:
            raise ValueError(
                f"Agent backend is not configured: {option.configuration_env}"
            )
        return option

    def read(self, session_id: str) -> AgentBackendSelection:
        with self._session_factory() as db:
            project = self._project(db, session_id)
            return self._project_selection(db, project.id)

    def select(
        self,
        session_id: str,
        provider: AgentBackendName,
        actor_id: str,
    ) -> AgentBackendSelection:
        option = self.ensure_available(provider)
        with self._session_factory() as db:
            project = self._project(db, session_id)
            participants = {
                project.final_approver,
                *(project.project_manager_ids or []),
                *(project.root_owner_ids or []),
            }
            if actor_id not in participants:
                raise ForbiddenActor("actor is not a project participant")
            previous = self._project_selection(db, project.id)
            for agent_session in db.query(AgentSession).filter_by(
                project_id=project.id
            ):
                agent_session.provider = option.provider
                agent_session.model = option.model
            db.add(
                AuditEvent(
                    id=str(uuid4()),
                    project_id=project.id,
                    session_id=session_id,
                    event_type="AGENT_BACKEND_SELECTED",
                    actor_id=actor_id,
                    payload={
                        "previous_provider": previous.provider,
                        "provider": option.provider,
                        "model": option.model,
                    },
                    created_at=datetime.now(UTC),
                )
            )
            db.commit()
        return AgentBackendSelection(provider=option.provider, model=option.model)

    def resolve_call(
        self,
        operation: str,
        payload: Mapping[str, object],
    ) -> AgentBackendSelection:
        """Resolve the project selection and stamp the concrete Agent session."""

        with self._session_factory() as db:
            project_id = payload.get("project_id")
            if not isinstance(project_id, str) or not project_id:
                expected = dict(payload)
                calls = (
                    db.query(AgentCall)
                    .filter_by(operation=operation, status="PENDING")
                    .order_by(AgentCall.started_at.desc(), AgentCall.id.desc())
                    .all()
                )
                call = next(
                    (candidate for candidate in calls if candidate.request == expected),
                    None,
                )
                project_id = call.project_id if call is not None else None
            if not isinstance(project_id, str) or not project_id:
                return AgentBackendSelection(
                    provider="codex", model=self._settings.codex_model
                )
            selection = self._project_selection(db, project_id)
            self.ensure_available(selection.provider)
            expected = dict(payload)
            calls = (
                db.query(AgentCall)
                .filter_by(
                    project_id=project_id,
                    operation=operation,
                    status="PENDING",
                )
                .order_by(AgentCall.started_at.desc(), AgentCall.id.desc())
                .all()
            )
            call = next(
                (candidate for candidate in calls if candidate.request == expected),
                None,
            )
            if call is not None:
                agent_session = db.get(AgentSession, call.agent_session_id)
                if agent_session is not None and (
                    agent_session.provider != selection.provider
                    or agent_session.model != selection.model
                ):
                    agent_session.provider = selection.provider
                    agent_session.model = selection.model
                    db.commit()
            return selection

    def _project_selection(
        self, db: Session, project_id: str
    ) -> AgentBackendSelection:
        pm = (
            db.query(AgentSession)
            .filter_by(project_id=project_id, role="PM")
            .order_by(AgentSession.created_at, AgentSession.id)
            .first()
        )
        provider = (
            pm.provider
            if pm is not None
            and pm.provider in {"codex", "deepseek", "glm", "kimi"}
            else "codex"
        )
        option = self.option(provider)
        return AgentBackendSelection(
            provider=provider,
            model=(pm.model if pm is not None and pm.model else option.model),
        )

    @staticmethod
    def _project(db: Session, session_id: str) -> Project:
        project = db.query(Project).filter_by(session_id=session_id).one_or_none()
        if project is None:
            raise KeyError("project not found")
        return project
