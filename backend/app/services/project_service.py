"""Durable project intake and PM-led clarification workflow."""

import asyncio
import hashlib
import json
import time
from datetime import UTC, datetime, timedelta
from typing import Callable
from uuid import uuid4

from sqlalchemy import desc
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from pydantic import ValidationError

from app.agents.codex import AgentExecutionError, AgentOutputError
from app.agents.gateway import AgentGateway
from app.database.models import (
    AgentCall,
    AgentSession,
    Artifact,
    AuditEvent,
    ClarificationRequest,
    ClarificationResponse,
    Conversation,
    IntakeAnalysisClaim,
    ProcessedCommand,
    Project,
    SpecVersion,
    WorkItem,
    clarification_boundary_key,
)
from app.domain.types import ClarificationAnalysis, ProjectPhase, SpecStatus
from app.schemas.workflow import ProjectBrief, SessionCreateRequest, SessionState
from app.services.state_projection import (
    active_clarification_request,
    followup_spec_version_id,
    project_state,
)


_INTAKE_CLAIM_LEASE_SECONDS = 30
_INTAKE_CLAIM_HEARTBEAT_SECONDS = 5


class RequestConflict(Exception):
    """Raised when an idempotency key is reused with a different request body."""


class ClarificationNotAllowed(Exception):
    """Raised when a human answer is submitted outside the clarification gate."""


class ClarificationAlreadyAnswered(Exception):
    """Raised when a clarification already has its one logical human answer."""

    code = "CLARIFICATION_ALREADY_ANSWERED"


def _new_id() -> str:
    return str(uuid4())


def _now() -> datetime:
    return datetime.now(UTC)


def _canonical_hash(value: object) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ProjectService:
    """Creates project sessions and records each PM clarification decision."""

    def __init__(self, session_factory: Callable[[], Session], agent: AgentGateway) -> None:
        self._session_factory = session_factory
        self._agent = agent

    async def create_session(
        self,
        request: SessionCreateRequest | None = None,
        *,
        request_id: str | None = None,
        actor_id: str | None = None,
        brief: ProjectBrief | None = None,
        propagate_agent_errors: bool = False,
    ) -> SessionState:
        """Create durable intake records, then run the PM clarity analysis.

        The external Agent is intentionally called only after the intake transaction
        commits, so a failed call leaves a retryable project rather than a partial
        project graph.
        """

        request = self._normalize_request(request, request_id, actor_id, brief)
        request_body = request.model_dump(mode="json")
        body_hash = _canonical_hash(request_body)

        with self._session_factory() as db:
            project = db.query(Project).filter_by(creation_request_id=request.request_id).one_or_none()
            if project is not None:
                receipt = (
                    db.query(ProcessedCommand)
                    .filter_by(session_id=project.session_id, command_id=request.request_id)
                    .one_or_none()
                )
                if receipt is None or receipt.input_hash != body_hash:
                    raise RequestConflict("request_id has already been used with different input")
                existing_project_id = project.id
                should_analyze = project.phase == ProjectPhase.INTAKE.value
            else:
                project = self._create_intake_records(db, request, request_body, body_hash)
                try:
                    db.commit()
                except IntegrityError:
                    db.rollback()
                    project = (
                        db.query(Project)
                        .filter_by(creation_request_id=request.request_id)
                        .one_or_none()
                    )
                    if project is None:
                        raise
                    receipt = (
                        db.query(ProcessedCommand)
                        .filter_by(
                            session_id=project.session_id,
                            command_id=request.request_id,
                        )
                        .one_or_none()
                    )
                    if receipt is None or receipt.input_hash != body_hash:
                        raise RequestConflict(
                            "request_id has already been used with different input"
                        )
                existing_project_id = project.id
                should_analyze = project.phase == ProjectPhase.INTAKE.value

        if should_analyze:
            await self._run_analysis(
                existing_project_id, propagate_agent_errors=propagate_agent_errors
            )
        return self._state_for_project(existing_project_id)

    async def answer_clarification(
        self, project_id: str, actor_id: str, message: str
    ) -> SessionState:
        """Persist an answer before asking the PM Agent to re-assess readiness."""

        with self._session_factory() as db:
            project = db.get(Project, project_id)
            if project is None:
                raise KeyError(f"project not found: {project_id}")
            spec_status = self._current_spec_status(db, project)
            if not (
                project.phase == ProjectPhase.NEED_CLARIFICATION.value
                or spec_status is SpecStatus.NEED_CLARIFICATION
            ):
                raise ClarificationNotAllowed("project is not waiting for clarification")

            clarification_request = active_clarification_request(db, project)
            if clarification_request is None:
                raise ClarificationNotAllowed("no clarification request is available")

            response = ClarificationResponse(
                id=_new_id(),
                project_id=project_id,
                clarification_request_id=clarification_request.id,
                response_slot="PRIMARY",
                actor_id=actor_id,
                answers={"message": message},
            )
            db.add(response)
            db.add(
                AuditEvent(
                    id=_new_id(),
                    project_id=project_id,
                    session_id=project.session_id,
                    event_type="CLARIFICATION_RESPONSE_RECORDED",
                    actor_id=actor_id,
                    payload={
                        "clarification_request_id": clarification_request.id,
                        "clarification_response_id": response.id,
                    },
                )
            )
            try:
                db.commit()
            except IntegrityError as error:
                db.rollback()
                existing = (
                    db.query(ClarificationResponse.id)
                    .filter_by(
                        clarification_request_id=clarification_request.id,
                        response_slot="PRIMARY",
                    )
                    .first()
                )
                if existing is not None:
                    raise ClarificationAlreadyAnswered(
                        "clarification request already has a primary response"
                    ) from error
                raise

        await self._run_analysis(project_id)
        return self._state_for_project(project_id)

    def as_command_handler(self):
        """Expose clarification as a production-safe two-phase command handler."""
        from app.services.command_service import (
            CommandHandlerFailure,
            CommandHandlerResult,
            ForbiddenActor,
            PreparedCommand,
        )

        service = self

        class _Handler:
            async def prepare(self, context):
                message = (context.request.message or "").strip()
                if not message:
                    raise ValueError("clarification message is required")
                with service._session_factory() as db:
                    project = service._project_for_command(db, context.project_id)
                    participants = {
                        project.final_approver,
                        *(project.project_manager_ids or []),
                        *(project.root_owner_ids or []),
                    }
                    if context.request.actor_id not in participants:
                        raise ForbiddenActor(
                            f"actor {context.request.actor_id!r} is not a project participant"
                        )
                    clarification = active_clarification_request(db, project)
                    if clarification is None:
                        raise ClarificationNotAllowed("no clarification request is available")
                    response_id = _new_id()
                    payload = service._analysis_payload(db, project)
                    payload["clarification_history"] = [
                        *payload["clarification_history"],
                        {
                            "request_id": clarification.id,
                            "questions": clarification.questions,
                            "answers": {"message": message},
                            "actor_id": context.request.actor_id,
                            "response_id": response_id,
                            "answered_at": _now().isoformat(),
                        },
                    ]
                    payload.update(
                        command_id=context.request.command_id,
                        input_hash=context.input_hash,
                    )
                    pm_session = (
                        db.query(AgentSession)
                        .filter_by(project_id=project.id, role="PM")
                        .one()
                    )
                    call = AgentCall(
                        id=_new_id(),
                        project_id=project.id,
                        agent_session_id=pm_session.id,
                        operation="analyze_brief",
                        request=payload,
                        status="PENDING",
                    )
                    next_round = (
                        db.query(ClarificationRequest.analysis_round)
                        .filter_by(project_id=project.id)
                        .order_by(desc(ClarificationRequest.analysis_round))
                        .first()
                    )
                    db.add(call)
                    db.commit()
                try:
                    analysis = service._validated_analysis(
                        await service._agent.analyze_brief(payload)
                    )
                except Exception as error:
                    service._record_analysis_failure(context.project_id, call.id, error)
                    raise CommandHandlerFailure(
                        str(error), agent_call_ids=[call.id]
                    ) from error
                with service._session_factory() as db:
                    persisted = db.get(AgentCall, call.id)
                    if persisted is None:
                        raise RuntimeError("clarification Agent call disappeared")
                    persisted.status = "RESULT_READY"
                    persisted.response = analysis.model_dump(mode="json")
                    persisted.completed_at = _now()
                    db.commit()
                return PreparedCommand(
                    payload={
                        "analysis": analysis.model_dump(mode="json"),
                        "clarification_request_id": clarification.id,
                        "clarification_response_id": response_id,
                        "message": message,
                        "next_analysis_round": (next_round[0] if next_round else 0) + 1,
                        "new_clarification_request_id": _new_id(),
                    },
                    agent_backed=True,
                    agent_call_ids=[call.id],
                )

            def materialize(self, uow, context, prepared):
                from app.domain.types import ClarificationAnalysis

                analysis = ClarificationAnalysis.model_validate(prepared.payload["analysis"])
                response = ClarificationResponse(
                    id=str(prepared.payload["clarification_response_id"]),
                    project_id=context.project_id,
                    clarification_request_id=str(prepared.payload["clarification_request_id"]),
                    response_slot="PRIMARY",
                    actor_id=context.request.actor_id,
                    answers={"message": str(prepared.payload["message"])},
                )
                uow.add_clarification_response(response)
                uow.update_project_brief(analysis.brief_updates)
                if analysis.brief_updates.summary is not None:
                    uow.update_root_summary(analysis.brief_updates.summary)
                created_ids = [response.id]
                if analysis.ready_for_spec:
                    if context.state.current_spec_status is SpecStatus.NEED_CLARIFICATION:
                        phase, spec_status = ProjectPhase.REVIEW, SpecStatus.REWORK
                    else:
                        phase, spec_status = ProjectPhase.SPECIFICATION, None
                else:
                    spec_version_id = followup_spec_version_id(
                        context.state.current_spec_version_id,
                        context.state.current_spec_status,
                    )
                    request = ClarificationRequest(
                        id=str(prepared.payload["new_clarification_request_id"]),
                        project_id=context.project_id,
                        spec_version_id=spec_version_id,
                        boundary_key=clarification_boundary_key(spec_version_id),
                        questions=[item.model_dump(mode="json") for item in analysis.questions],
                        analysis_round=int(prepared.payload["next_analysis_round"]),
                        blocking=any(item.blocking for item in analysis.questions),
                        agent_call_id=prepared.agent_call_ids[0],
                    )
                    uow.add_clarification_request(request)
                    created_ids.append(request.id)
                    phase, spec_status = context.state.phase, context.state.current_spec_status
                uow.mark_analysis_call_succeeded(prepared.agent_call_ids[0])
                return CommandHandlerResult(
                    phase=phase,
                    spec_status=spec_status,
                    created_resource_ids=created_ids,
                    audit_payload={
                        "brief_updated_fields": sorted(
                            analysis.brief_updates.model_dump(
                                exclude_none=True, exclude={"summary"}
                            )
                        ),
                    },
                )

        return _Handler()

    def state_for_creation_request(self, request_id: str) -> SessionState:
        """Recover the durable intake state after its PM analysis call fails."""

        with self._session_factory() as db:
            project = (
                db.query(Project)
                .filter_by(creation_request_id=request_id)
                .one_or_none()
            )
            if project is None:
                raise KeyError(f"project creation request not found: {request_id}")
            return self._state(db, project)

    @staticmethod
    def _project_for_command(db: Session, project_id: str) -> Project:
        project = db.get(Project, project_id)
        if project is None:
            raise KeyError(f"project not found: {project_id}")
        return project

    def _normalize_request(
        self,
        request: SessionCreateRequest | None,
        request_id: str | None,
        actor_id: str | None,
        brief: ProjectBrief | None,
    ) -> SessionCreateRequest:
        if request is not None:
            if request_id is not None or actor_id is not None or brief is not None:
                raise TypeError("pass either request or request fields, not both")
            return request
        if request_id is None or actor_id is None or brief is None:
            raise TypeError("request_id, actor_id, and brief are required")
        return SessionCreateRequest(request_id=request_id, actor_id=actor_id, brief=brief)

    def _create_intake_records(
        self,
        db: Session,
        request: SessionCreateRequest,
        request_body: dict[str, object],
        body_hash: str,
    ) -> Project:
        session_id = _new_id()
        project_id = _new_id()
        pm_session_id = _new_id()
        root_work_item_id = _new_id()
        artifact_id = _new_id()
        brief_body = request.brief.model_dump(mode="json")

        db.add(Conversation(id=session_id, workflow="project_spec", agent="pm"))
        project = Project(
            id=project_id,
            session_id=session_id,
            creation_request_id=request.request_id,
            brief=brief_body,
            final_approver=request.brief.final_approver,
            project_manager_ids=request.brief.project_manager_ids,
            root_owner_ids=request.brief.root_owner_ids,
            phase=ProjectPhase.INTAKE.value,
            state_version=0,
        )
        db.add(project)
        db.add(
            WorkItem(
                id=root_work_item_id,
                session_id=session_id,
                project_id=project_id,
                local_key="root",
                kind="ROOT",
                executable=False,
                title=request.brief.final_objective,
                objective=request.brief.final_objective,
                scope=request.brief.known_scope,
                exclusions=request.brief.exclusions,
                responsible_role="Project Owner",
                suggested_assignee=request.actor_id,
            )
        )
        db.add(
            AgentSession(
                id=pm_session_id,
                project_id=project_id,
                role="PM",
                purpose="Analyze Project Brief clarity before specification",
                metadata_json={},
            )
        )
        db.add(
            Artifact(
                id=artifact_id,
                project_id=project_id,
                kind="ORIGINAL_REQUIREMENT",
                content=brief_body,
                content_hash=_canonical_hash(brief_body),
                source_actor_id=request.actor_id,
            )
        )
        db.add(
            ProcessedCommand(
                id=_new_id(),
                session_id=session_id,
                command_id=request.request_id,
                input_hash=body_hash,
                state_version=0,
                result={"status": "INTAKE_PENDING_ANALYSIS"},
                side_effect_refs=[project_id, root_work_item_id, pm_session_id, artifact_id],
            )
        )
        db.add(
            AuditEvent(
                id=_new_id(),
                project_id=project_id,
                session_id=session_id,
                event_type="PROJECT_INTAKE_CREATED",
                actor_id=request.actor_id,
                payload={"request": request_body, "input_hash": body_hash},
            )
        )
        return project

    async def _run_analysis(
        self, project_id: str, *, propagate_agent_errors: bool = False
    ) -> None:
        """Record the PM call and apply its typed result in a separate transaction."""

        with self._session_factory() as db:
            project = db.get(Project, project_id)
            if project is None:
                raise KeyError(f"project not found: {project_id}")
            initial_intake = project.phase == ProjectPhase.INTAKE.value

        claim_id: str | None = None
        if initial_intake:
            owns_claim, agent_call_id = self._claim_initial_analysis(project_id)
            if not owns_claim:
                await self._wait_for_initial_analysis(
                    project_id,
                    agent_call_id,
                    propagate_agent_errors=propagate_agent_errors,
                )
                return
            with self._session_factory() as db:
                agent_call = db.get(AgentCall, agent_call_id)
                claim = (
                    db.query(IntakeAnalysisClaim)
                    .filter_by(project_id=project_id)
                    .one()
                )
                if agent_call is None:
                    raise RuntimeError("claimed intake Agent call disappeared")
                payload = dict(agent_call.request)
                claim_id = claim.id
        else:
            with self._session_factory() as db:
                project = db.get(Project, project_id)
                if project is None:
                    raise KeyError(f"project not found: {project_id}")
                pm_session = (
                    db.query(AgentSession)
                    .filter_by(project_id=project_id, role="PM")
                    .one()
                )
                payload = self._analysis_payload(db, project)
                agent_call = AgentCall(
                    id=_new_id(),
                    project_id=project_id,
                    agent_session_id=pm_session.id,
                    operation="analyze_brief",
                    request=payload,
                    status="PENDING",
                )
                db.add(agent_call)
                db.commit()
                agent_call_id = agent_call.id

        try:
            analysis = await self._execute_analysis(
                payload, claim_id=claim_id, agent_call_id=agent_call_id
            )
        except asyncio.CancelledError as error:
            self._record_analysis_failure(
                project_id, agent_call_id, error, claim_id=claim_id
            )
            raise
        except Exception as error:
            self._record_analysis_failure(
                project_id, agent_call_id, error, claim_id=claim_id
            )
            if propagate_agent_errors:
                if isinstance(error, (AgentExecutionError, AgentOutputError)):
                    raise
                raise AgentExecutionError(str(error)) from error
            return

        with self._session_factory() as db:
            if claim_id is not None and not self._fence_initial_analysis_completion(
                db, claim_id, agent_call_id
            ):
                db.rollback()
                return
            project = db.get(Project, project_id)
            agent_call = db.get(AgentCall, agent_call_id)
            if project is None or agent_call is None:
                raise RuntimeError("project or Agent call disappeared during analysis")
            agent_call.status = "SUCCEEDED"
            agent_call.response = analysis.model_dump(mode="json")
            agent_call.completed_at = _now()
            project.brief = analysis.brief_updates.apply_to(project.brief)
            if analysis.brief_updates.summary is not None:
                root = (
                    db.query(WorkItem)
                    .filter_by(project_id=project_id, local_key="root", kind="ROOT")
                    .one_or_none()
                )
                if root is None:
                    raise RuntimeError("project ROOT WorkItem disappeared during analysis")
                root.summary = analysis.brief_updates.summary
            if analysis.ready_for_spec:
                current_spec = self._current_spec(db, project)
                if current_spec is not None and current_spec.status == SpecStatus.NEED_CLARIFICATION.value:
                    current_spec.status = SpecStatus.REWORK.value
                    project.phase = ProjectPhase.REVIEW.value
                else:
                    project.phase = ProjectPhase.SPECIFICATION.value
            else:
                latest_round = (
                    db.query(ClarificationRequest.analysis_round)
                    .filter_by(project_id=project_id)
                    .order_by(desc(ClarificationRequest.analysis_round))
                    .first()
                )
                analysis_round = (latest_round[0] if latest_round is not None else 0) + 1
                spec_version_id = followup_spec_version_id(
                    project.current_spec_version_id,
                    self._current_spec_status(db, project),
                )
                db.add(
                    ClarificationRequest(
                        id=_new_id(),
                        project_id=project_id,
                        spec_version_id=spec_version_id,
                        boundary_key=clarification_boundary_key(spec_version_id),
                        questions=[question.model_dump(mode="json") for question in analysis.questions],
                        analysis_round=analysis_round,
                        blocking=any(question.blocking for question in analysis.questions),
                        agent_session_id=agent_call.agent_session_id,
                        agent_call_id=agent_call.id,
                    )
                )
                project.phase = (
                    ProjectPhase.REVIEW.value
                    if spec_version_id is not None
                    else ProjectPhase.NEED_CLARIFICATION.value
                )
            project.state_version += 1
            db.add(
                AuditEvent(
                    id=_new_id(),
                    project_id=project_id,
                    session_id=project.session_id,
                    event_type="PM_BRIEF_ANALYZED",
                    actor_id=None,
                    payload={
                        "agent_call_id": agent_call.id,
                        "ready_for_spec": analysis.ready_for_spec,
                        "brief_updated_fields": sorted(
                            analysis.brief_updates.model_dump(
                                exclude_none=True, exclude={"summary"}
                            )
                        ),
                    },
                )
            )
            self._update_intake_receipt(db, project)
            db.commit()

    async def _execute_analysis(
        self,
        payload: dict[str, object],
        *,
        claim_id: str | None,
        agent_call_id: str,
    ) -> ClarificationAnalysis:
        if claim_id is None:
            return self._validated_analysis(await self._agent.analyze_brief(payload))

        agent_task = asyncio.create_task(self._agent.analyze_brief(payload))
        heartbeat_task = asyncio.create_task(
            self._heartbeat_initial_analysis(claim_id, agent_call_id)
        )
        try:
            completed, _ = await asyncio.wait(
                {agent_task, heartbeat_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if heartbeat_task in completed:
                try:
                    await heartbeat_task
                except Exception as error:
                    raise AgentExecutionError(
                        "PM intake analysis lease heartbeat failed"
                    ) from error
                raise AgentExecutionError("PM intake analysis claim ownership was lost")
            return self._validated_analysis(await agent_task)
        finally:
            agent_task.cancel()
            heartbeat_task.cancel()
            await asyncio.gather(agent_task, heartbeat_task, return_exceptions=True)

    @staticmethod
    def _fence_initial_analysis_completion(
        db: Session, claim_id: str, agent_call_id: str
    ) -> bool:
        updated = (
            db.query(IntakeAnalysisClaim)
            .filter_by(
                id=claim_id,
                agent_call_id=agent_call_id,
                status="PREPARING",
            )
            .update(
                {"status": "COMPLETED", "updated_at": _now()},
                synchronize_session=False,
            )
        )
        return updated == 1

    def _claim_initial_analysis(self, project_id: str) -> tuple[bool, str]:
        """Atomically claim initial PM analysis or observe its durable owner."""

        while True:
            with self._session_factory() as db:
                project = db.get(Project, project_id)
                if project is None:
                    raise KeyError(f"project not found: {project_id}")
                claim = (
                    db.query(IntakeAnalysisClaim)
                    .filter_by(project_id=project_id)
                    .one_or_none()
                )
                if claim is not None and claim.status == "COMPLETED":
                    return False, claim.agent_call_id
                if (
                    claim is not None
                    and claim.status == "PREPARING"
                    and not self._claim_is_stale(claim.updated_at)
                ):
                    return False, claim.agent_call_id
                pm_session = (
                    db.query(AgentSession)
                    .filter_by(project_id=project_id, role="PM")
                    .one()
                )
                call = AgentCall(
                    id=_new_id(),
                    project_id=project_id,
                    agent_session_id=pm_session.id,
                    operation="analyze_brief",
                    request=self._analysis_payload(db, project),
                    status="PENDING",
                )
                db.add(call)
                if claim is None:
                    db.add(
                        IntakeAnalysisClaim(
                            id=_new_id(),
                            project_id=project_id,
                            agent_call_id=call.id,
                            status="PREPARING",
                        )
                    )
                else:
                    previous_call_id = claim.agent_call_id
                    previous_status = claim.status
                    previous_updated_at = claim.updated_at
                    filters = [
                        IntakeAnalysisClaim.id == claim.id,
                        IntakeAnalysisClaim.status == previous_status,
                        IntakeAnalysisClaim.agent_call_id == previous_call_id,
                    ]
                    if previous_status == "PREPARING":
                        filters.append(IntakeAnalysisClaim.updated_at == previous_updated_at)
                    updated = (
                        db.query(IntakeAnalysisClaim)
                        .filter(*filters)
                        .update(
                            {
                                "agent_call_id": call.id,
                                "status": "PREPARING",
                                "updated_at": _now(),
                            },
                            synchronize_session=False,
                        )
                    )
                    if updated != 1:
                        db.rollback()
                        continue
                    if previous_status == "PREPARING":
                        abandoned = db.get(AgentCall, previous_call_id)
                        if abandoned is not None and abandoned.status == "PENDING":
                            abandoned.status = "FAILED"
                            abandoned.error = "Intake analysis claim lease expired"
                            abandoned.response = {"error_code": "ANALYSIS_CLAIM_EXPIRED"}
                            abandoned.completed_at = _now()
                try:
                    db.commit()
                except IntegrityError:
                    db.rollback()
                    continue
                return True, call.id

    @staticmethod
    def _claim_is_stale(updated_at: datetime) -> bool:
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=UTC)
        return _now() - updated_at >= timedelta(seconds=_INTAKE_CLAIM_LEASE_SECONDS)

    async def _heartbeat_initial_analysis(
        self, claim_id: str, agent_call_id: str
    ) -> None:
        while True:
            await asyncio.sleep(_INTAKE_CLAIM_HEARTBEAT_SECONDS)
            with self._session_factory() as db:
                updated = (
                    db.query(IntakeAnalysisClaim)
                    .filter_by(
                        id=claim_id,
                        agent_call_id=agent_call_id,
                        status="PREPARING",
                    )
                    .update({"updated_at": _now()}, synchronize_session=False)
                )
                db.commit()
                if updated != 1:
                    return

    async def _wait_for_initial_analysis(
        self,
        project_id: str,
        agent_call_id: str,
        *,
        propagate_agent_errors: bool,
    ) -> None:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            with self._session_factory() as db:
                claim = (
                    db.query(IntakeAnalysisClaim)
                    .filter_by(project_id=project_id)
                    .one_or_none()
                )
                if claim is None or claim.agent_call_id != agent_call_id:
                    return
                if claim.status == "COMPLETED":
                    return
                if claim.status == "FAILED":
                    if propagate_agent_errors:
                        raise AgentExecutionError(
                            "PM intake analysis failed; retry with the same request_id"
                        )
                    return
            await asyncio.sleep(0.01)
        raise AgentExecutionError("PM intake analysis is still in progress; retry this request")

    def _record_analysis_failure(
        self,
        project_id: str,
        agent_call_id: str,
        error: BaseException,
        *,
        claim_id: str | None = None,
    ) -> None:
        with self._session_factory() as db:
            project = db.get(Project, project_id)
            agent_call = db.get(AgentCall, agent_call_id)
            if project is None or agent_call is None:
                raise RuntimeError("project or Agent call disappeared during analysis")
            error_code = (
                "INVALID_AGENT_RESULT"
                if isinstance(error, AgentOutputError)
                else "AGENT_UNAVAILABLE"
            )
            agent_call.status = "FAILED"
            agent_call.error = str(error)
            agent_call.response = {"error_code": error_code}
            agent_call.completed_at = _now()
            if claim_id is not None:
                claim = db.get(IntakeAnalysisClaim, claim_id)
                if claim is not None and claim.agent_call_id == agent_call_id:
                    claim.status = "FAILED"
                    claim.updated_at = _now()
            db.add(
                AuditEvent(
                    id=_new_id(),
                    project_id=project_id,
                    session_id=project.session_id,
                    event_type="PM_BRIEF_ANALYSIS_FAILED",
                    actor_id=None,
                    payload={
                        "agent_call_id": agent_call.id,
                        "error_code": error_code,
                        "message": "PM brief analysis failed; retry the workflow action.",
                    },
                )
            )
            db.commit()

    def _analysis_payload(self, db: Session, project: Project) -> dict[str, object]:
        # Spec revisions can restart analysis_round at 1. Decisions must be
        # reconciled in answer order so an old intake answer cannot win again.
        responses = (
            db.query(ClarificationResponse, ClarificationRequest)
            .join(
                ClarificationRequest,
                ClarificationResponse.clarification_request_id == ClarificationRequest.id,
            )
            .filter(
                ClarificationResponse.project_id == project.id,
                ClarificationRequest.project_id == project.id,
            )
            .order_by(ClarificationResponse.created_at, ClarificationResponse.id)
            .all()
        )
        history = [
            {
                "request_id": request.id,
                "questions": request.questions,
                "answers": response.answers,
                "actor_id": response.actor_id,
                "response_id": response.id,
                "answered_at": response.created_at.isoformat(),
            }
            for response, request in responses
        ]
        return {"brief": project.brief, "clarification_history": history}

    @staticmethod
    def _validated_analysis(value: object) -> ClarificationAnalysis:
        try:
            payload = value.model_dump(mode="json") if hasattr(value, "model_dump") else value
            return ClarificationAnalysis.model_validate(payload)
        except ValidationError as error:
            raise AgentOutputError("PM Agent returned invalid clarification output") from error

    def _state_for_project(self, project_id: str) -> SessionState:
        with self._session_factory() as db:
            project = db.get(Project, project_id)
            if project is None:
                raise KeyError(f"project not found: {project_id}")
            return self._state(db, project)

    def _state(self, db: Session, project: Project) -> SessionState:
        return project_state(db, project)

    def _current_spec_status(self, db: Session, project: Project) -> SpecStatus | None:
        spec = self._current_spec(db, project)
        return SpecStatus(spec.status) if spec is not None else None

    @staticmethod
    def _current_spec(db: Session, project: Project) -> SpecVersion | None:
        if project.current_spec_version_id is None:
            return None
        return db.get(SpecVersion, project.current_spec_version_id)

    def _update_intake_receipt(self, db: Session, project: Project) -> None:
        receipt = (
            db.query(ProcessedCommand)
            .filter_by(session_id=project.session_id, command_id=project.creation_request_id)
            .one()
        )
        receipt.state_version = project.state_version
        receipt.result = {
            "project_id": project.id,
            "session_id": project.session_id,
            "phase": project.phase,
            "state_version": project.state_version,
        }
