"""Safe compatibility facade for the legacy streaming ``POST /chat`` endpoint."""

import json
from collections.abc import AsyncIterator, Callable
from uuid import uuid4

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.agents.gateway import AgentGateway
from app.domain.types import CommandAction, ProjectPhase
from app.identity import ActorResolver
from app.schemas.chat import ChatRequest
from app.schemas.workflow import ProjectBrief, SessionCommandRequest, SessionCreateRequest, SessionState
from app.services.command_service import CommandService
from app.services.error_classification import classify_workflow_error
from app.services.project_service import ProjectService
from app.services.query_service import QueryService
from app.services.agent_backends import AgentBackendService


def _event(name: str, payload: dict[str, object]) -> str:
    """Encode one stable Server-Sent Event frame."""

    return f"event: {name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _state_payload(state: SessionState) -> dict[str, object]:
    return state.model_dump(mode="json")


def _progress_events(state: SessionState) -> list[tuple[str, dict[str, object]]]:
    """Map durable workflow state to the legacy client's stable event vocabulary."""

    state_payload = _state_payload(state)
    if state.outstanding_questions:
        return [
            (
                "clarification.requested",
                {"session_id": state.session_id, "questions": state_payload["outstanding_questions"]},
            )
        ]
    if state.phase is ProjectPhase.SPECIFICATION:
        return [("spec.ready", state_payload)]
    return []


def _error_payload(error: Exception, state: SessionState) -> dict[str, object]:
    public_error = classify_workflow_error(error)
    return {
        "code": public_error.code,
        "message": public_error.message,
        "legal_actions": [action.value for action in state.legal_actions],
    }


async def _frames(items: list[tuple[str, dict[str, object]]]) -> AsyncIterator[str]:
    for name, payload in items:
        yield _event(name, payload)


def build_router(
    session_factory: Callable[[], Session],
    agent_gateway: AgentGateway,
    actor_resolver: ActorResolver,
    agent_backends: AgentBackendService | None = None,
) -> APIRouter:
    """Build the facade with the same injected workflow dependencies as ``/sessions``."""

    router = APIRouter()
    projects = ProjectService(session_factory, agent_gateway)
    commands = CommandService(
        session_factory, handlers={CommandAction.MESSAGE: projects.as_command_handler()}
    )
    queries = QueryService(session_factory)

    @router.post("/chat")
    async def chat(
        request_body: ChatRequest,
        request: Request,
        x_session_id: str | None = Header(default=None),
    ) -> StreamingResponse:
        if (
            request_body.session_id
            and x_session_id
            and request_body.session_id != x_session_id
        ):
            raise HTTPException(
                status_code=422,
                detail={"code": "VALIDATION_ERROR", "message": "session identifiers disagree"},
            )
        session_id = x_session_id or request_body.session_id
        actor_id = actor_resolver.resolve(request, request_body.actor_id)
        events: list[tuple[str, dict[str, object]]] = []

        if session_id is None:
            if agent_backends is not None:
                try:
                    agent_backends.ensure_available(request_body.agent_backend)
                except Exception as error:
                    public_error = classify_workflow_error(error)
                    raise HTTPException(
                        status_code=public_error.status_code,
                        detail={
                            "code": public_error.code,
                            "message": public_error.message,
                        },
                    ) from error
            creation_request_id = str(uuid4())
            brief = ProjectBrief(
                motivation=request_body.message,
                final_objective=request_body.message,
                known_scope=[],
                exclusions=[],
                reference_materials=[],
                expected_deliverables=[],
                time_constraints="Not supplied",
                staffing_constraints="Not supplied",
                final_approver=actor_id,
                project_manager_ids=[actor_id],
                root_owner_ids=[actor_id],
            )
            try:
                state = await projects.create_session(
                    SessionCreateRequest(
                        request_id=creation_request_id,
                        actor_id=actor_id,
                        agent_backend=request_body.agent_backend,
                        brief=brief,
                    ),
                    propagate_agent_errors=True,
                )
            except Exception as error:
                try:
                    state = projects.state_for_creation_request(creation_request_id)
                except KeyError:
                    raise error
                session_id = state.session_id
                events.append(("session.created", _state_payload(state)))
                events.append(("workflow.error", _error_payload(error, state)))
            else:
                session_id = state.session_id
                events.append(("session.created", _state_payload(state)))
                events.extend(_progress_events(state))
        else:
            try:
                state = queries.state(session_id)
            except KeyError as error:
                raise HTTPException(
                    status_code=404,
                    detail={"code": "NOT_FOUND", "message": str(error)},
                ) from error

            if CommandAction.MESSAGE not in state.legal_actions:
                events.append(
                    (
                        "workflow.error",
                        {
                            "code": "ILLEGAL_ACTION",
                            "message": "message is not legal in the current workflow state",
                            "legal_actions": [action.value for action in state.legal_actions],
                        },
                    )
                )
            else:
                try:
                    result = await commands.execute(
                        session_id,
                        SessionCommandRequest(
                            command_id=str(uuid4()),
                            action=CommandAction.MESSAGE,
                            expected_state_version=state.state_version,
                            actor_id=actor_id,
                            message=request_body.message,
                        ),
                    )
                    state = result.state
                    events.extend(_progress_events(state))
                except Exception as error:
                    try:
                        state = queries.state(session_id)
                    except KeyError:
                        pass
                    events.append(("workflow.error", _error_payload(error, state)))

        events.append(("next_action", {"next_action": state.next_action}))
        return StreamingResponse(
            _frames(events),
            media_type="text/event-stream",
            headers={"X-Session-ID": session_id},
        )

    return router
