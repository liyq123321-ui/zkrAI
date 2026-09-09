"""Recommend and persist the human-confirmed SDLC route before planning."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.orm import Session

from app.agents.gateway import AgentGateway
from app.database.models import AgentCall, AgentSession, AuditEvent, Project, SdlcRouteDecision
from app.domain.sdlc import LifecycleAssessment, LifecycleRouteDecision
from app.domain.types import ProjectPhase
from app.services.sdlc_rules import planning_context, recommend_models, route_confirmation


def _new_id() -> str:
    return str(uuid4())


def _now() -> datetime:
    return datetime.now(UTC)


class LifecycleDecisionService:
    def __init__(self, session_factory, agent: AgentGateway) -> None:
        self._session_factory = session_factory
        self._agent = agent

    def read(self, session_id: str) -> LifecycleRouteDecision | None:
        with self._session_factory() as db:
            project = db.query(Project).filter_by(session_id=session_id).one_or_none()
            if project is None:
                raise KeyError(f"Session not found: {session_id}")
            row = db.query(SdlcRouteDecision).filter_by(project_id=project.id).one_or_none()
            return self._read(row) if row else None

    async def recommend(self, session_id: str) -> LifecycleRouteDecision:
        with self._session_factory() as db:
            project = db.query(Project).filter_by(session_id=session_id).one_or_none()
            if project is None:
                raise KeyError(f"Session not found: {session_id}")
            existing = db.query(SdlcRouteDecision).filter_by(project_id=project.id).one_or_none()
            if existing is not None:
                return self._read(existing)
            if project.phase != ProjectPhase.SPECIFICATION.value or project.current_spec_version_id:
                raise ValueError("SDLC route recommendation is only available after clarification and before PRD generation")
            pm = db.query(AgentSession).filter_by(project_id=project.id, role="PM").one()
            rules = planning_context()
            payload = {
                "project_id": project.id,
                "project_brief": project.brief,
                "sdlc_selection": rules["selection"],
                "rules_version": rules["version"],
                "rules_hash": rules["hash"],
            }
            call = AgentCall(
                id=_new_id(), project_id=project.id, agent_session_id=pm.id,
                operation="recommend_lifecycle", request=payload, status="PENDING",
            )
            db.add(call)
            db.commit()
            project_id, call_id = project.id, call.id

        try:
            assessment = LifecycleAssessment.model_validate(
                await self._agent.recommend_lifecycle(payload)
            )
            decision = recommend_models(assessment, rules)
        except Exception as error:
            with self._session_factory() as db:
                call = db.get(AgentCall, call_id)
                if call is not None:
                    call.status = "FAILED"
                    call.error = str(error)
                    call.completed_at = _now()
                    db.commit()
            raise

        with self._session_factory() as db:
            existing = db.query(SdlcRouteDecision).filter_by(project_id=project_id).one_or_none()
            if existing is not None:
                return self._read(existing)
            call = db.get(AgentCall, call_id)
            row = SdlcRouteDecision(
                id=_new_id(), project_id=project_id,
                rules_version=decision.rules_version, rules_hash=decision.rules_hash,
                assessment=decision.assessment.model_dump(mode="json"),
                options=[option.model_dump(mode="json") for option in decision.options],
            )
            db.add(row)
            if call is not None:
                call.status = "SUCCEEDED"
                call.response = decision.model_dump(mode="json")
                call.completed_at = _now()
            db.commit()
            return self._read(row)

    def select(self, session_id: str, model: str, actor_id: str) -> LifecycleRouteDecision:
        with self._session_factory() as db:
            project = db.query(Project).filter_by(session_id=session_id).one_or_none()
            if project is None:
                raise KeyError(f"Session not found: {session_id}")
            row = db.query(SdlcRouteDecision).filter_by(project_id=project.id).one_or_none()
            if row is None:
                raise ValueError("generate SDLC route recommendations before selecting a route")
            if row.selected_model is not None:
                if row.selected_model == model:
                    return self._read(row)
                raise ValueError("SDLC route has already been confirmed and cannot be changed")
            offered = {option["model"] for option in row.options}
            if model not in offered:
                raise ValueError("selected SDLC route was not offered for this project")
            if project.phase != ProjectPhase.SPECIFICATION.value or project.current_spec_version_id:
                raise ValueError("SDLC route is locked after PRD generation begins")
            row.selected_model = model
            row.selected_by = actor_id
            row.selected_at = _now()
            brief = dict(project.brief)
            refs = [item for item in brief.get("reference_materials", [])
                    if not str(item).startswith("生命周期路线已由用户确认：")]
            brief["reference_materials"] = [*refs, route_confirmation(model)]
            project.brief = brief
            project.state_version += 1
            db.add(AuditEvent(
                id=_new_id(), project_id=project.id, session_id=session_id,
                event_type="SDLC_ROUTE_SELECTED", actor_id=actor_id,
                payload={"selected_model": model, "rules_hash": row.rules_hash},
            ))
            db.commit()
            return self._read(row)

    @staticmethod
    def _read(row: SdlcRouteDecision) -> LifecycleRouteDecision:
        return LifecycleRouteDecision(
            rules_version=row.rules_version,
            rules_hash=row.rules_hash,
            assessment=LifecycleAssessment.model_validate(row.assessment),
            options=row.options,
            selected_model=row.selected_model,
        )
