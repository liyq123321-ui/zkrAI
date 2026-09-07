"""HTTP routes for the project specification workflow."""

from collections.abc import Callable
import logging

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.agents.gateway import AgentGateway
from app.agents.codex import AgentExecutionError
from app.domain.types import CommandAction, ProjectPhase
from app.identity import ActorResolver
from app.schemas.workflow import (
    CommandJobAccepted,
    CommandJobRead,
    CommandResult,
    SessionCommandRequest,
    SessionCreateRequest,
    SessionState,
)
from app.services.command_jobs import CommandJobCoordinator
from app.services.command_service import CommandService
from app.services.error_classification import classify_workflow_error
from app.services.execution_service import ExecutionService
from app.services.project_service import ProjectService
from app.services.query_service import (
    AgentRuntimeRead,
    AgentSpecRead,
    AuditEventRead,
    QueryService,
    SessionSummaryRead,
    SpecVersionRead,
    WorkItemRead,
)
from app.services.spec_service import SpecService
from app.services.prd_review import PrdReviewService
from app.services.prd_prototype import PrdPrototypeService


logger = logging.getLogger(__name__)


def build_router(
    session_factory: Callable[[], Session],
    agent_gateway: AgentGateway,
    actor_resolver: ActorResolver,
    prd_review_service: PrdReviewService | None = None,
    command_jobs: CommandJobCoordinator | None = None,
    prototype_service: PrdPrototypeService | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/sessions", tags=["sessions"])
    projects = ProjectService(session_factory, agent_gateway)
    specs = SpecService(session_factory, agent_gateway)
    execution = ExecutionService(session_factory)
    commands = CommandService(
        session_factory,
        handlers={
            CommandAction.MESSAGE: projects.as_command_handler(),
            CommandAction.CREATE_SPEC: specs.as_command_handler(),
            CommandAction.REVISE: specs.as_command_handler(),
            CommandAction.RESTORE_SPEC_VERSION: specs.as_command_handler(),
            CommandAction.START_TASK: execution,
            CommandAction.COMPLETE_TASK: execution,
            CommandAction.FAIL_TASK: execution,
        },
    )
    queries = QueryService(session_factory)

    def http_error(error: Exception) -> HTTPException:
        public_error = classify_workflow_error(error)
        return HTTPException(
            status_code=public_error.status_code,
            detail={"code": public_error.code, "message": public_error.message},
        )

    @router.get("", response_model=list[SessionSummaryRead])
    def list_sessions() -> list[SessionSummaryRead]:
        try:
            return queries.sessions()
        except Exception as error:
            raise http_error(error) from error

    @router.post("", response_model=SessionState, status_code=status.HTTP_201_CREATED)
    async def create_session(
        request_body: SessionCreateRequest, request: Request
    ) -> SessionState:
        actor_id = actor_resolver.resolve(request, request_body.actor_id)
        request_body = request_body.model_copy(update={"actor_id": actor_id})
        try:
            state = await projects.create_session(
                request_body, propagate_agent_errors=True
            )
            if state.phase is ProjectPhase.INTAKE:
                raise AgentExecutionError(
                    "Project intake is durable, but PM analysis is unavailable; retry with the same request_id"
                )
            return state
        except Exception as error:
            raise http_error(error) from error

    @router.post(
        "/{session_id}/commands",
        response_model=CommandResult,
        responses={
            status.HTTP_202_ACCEPTED: {
                "model": CommandJobAccepted,
                "description": "Durable asynchronous decomposition command accepted.",
                "headers": {
                    "Location": {
                        "description": "Durable command-job status resource.",
                        "schema": {"type": "string"},
                    }
                },
            }
        },
    )
    async def execute_command(
        session_id: str,
        request_body: SessionCommandRequest,
        request: Request,
        background_tasks: BackgroundTasks,
    ) -> CommandResult | JSONResponse:
        actor_id = actor_resolver.resolve(request, request_body.actor_id)
        request_body = request_body.model_copy(update={"actor_id": actor_id})
        try:
            if request_body.action is CommandAction.CONVERT_TO_WORK_ITEM:
                if command_jobs is None:
                    raise RuntimeError("command jobs are not configured")
                accepted, should_schedule = command_jobs.submit(session_id, request_body)
                if should_schedule:
                    background_tasks.add_task(
                        command_jobs.run,
                        command_jobs.job_id(session_id, request_body.command_id),
                    )
                return JSONResponse(
                    status_code=status.HTTP_202_ACCEPTED,
                    content=accepted.model_dump(mode="json"),
                    headers={"Location": accepted.status_url},
                )
            result = await commands.execute(session_id, request_body)
            if (
                prototype_service is not None
                and request_body.action
                in {
                    CommandAction.CREATE_SPEC,
                    CommandAction.REVISE,
                    CommandAction.RESTORE_SPEC_VERSION,
                }
                and result.state.current_spec_status
                in {"HUMAN_REVIEW", "REWORK", "NEED_CLARIFICATION"}
            ):
                try:
                    await prototype_service.ensure_for_project(
                        result.state.project_id
                    )
                except Exception:
                    # Prototype generation is a derived review aid. The frozen
                    # PRD remains authoritative and must stay reviewable.
                    logger.exception(
                        "Could not generate PRD HTML prototype",
                        extra={"project_id": result.state.project_id},
                    )
            if (
                prd_review_service is not None
                and request_body.action in {CommandAction.CREATE_SPEC, CommandAction.REVISE}
                and result.state.current_spec_status
                in {"HUMAN_REVIEW", "REWORK"}
            ):
                try:
                    await prd_review_service.ensure_project_binding(
                        result.state.project_id
                    )
                except Exception:
                    # The Spec command is already committed. Keep the generated
                    # draft readable and let the PRD endpoint expose the precise
                    # Gitea error/retry path instead of making command replay
                    # appear to have failed.
                    logger.exception(
                        "Could not eagerly create Gitea PRD review binding",
                        extra={"project_id": result.state.project_id},
                    )
            return result
        except Exception as error:
            raise http_error(error) from error

    @router.get(
        "/{session_id}/commands/{command_id}", response_model=CommandJobRead
    )
    def get_command_job(session_id: str, command_id: str) -> CommandJobRead:
        try:
            if command_jobs is None:
                raise RuntimeError("command jobs are not configured")
            return command_jobs.get(session_id, command_id)
        except Exception as error:
            raise http_error(error) from error

    @router.get(
        "/{session_id}/commands/{command_id}/events",
        response_class=StreamingResponse,
        responses={
            status.HTTP_200_OK: {
                "description": "Live command-job status events.",
                "content": {"text/event-stream": {"schema": {"type": "string"}}},
            }
        },
    )
    async def command_job_events(
        session_id: str,
        command_id: str,
        request: Request,
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ) -> StreamingResponse:
        try:
            cursor = max(0, int(last_event_id or "0"))
            if command_jobs is None:
                raise RuntimeError("command jobs are not configured")
            command_jobs.get(session_id, command_id)
        except ValueError as error:
            raise HTTPException(400, detail={
                "code": "INVALID_EVENT_ID", "message": "Last-Event-ID must be an integer."
            }) from error
        except Exception as error:
            raise http_error(error) from error

        async def stream():
            async for frame in command_jobs.events(session_id, command_id, cursor):
                if await request.is_disconnected():
                    return
                yield frame

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.get("/{session_id}/state", response_model=SessionState)
    def get_state(session_id: str) -> SessionState:
        try:
            return queries.state(session_id)
        except Exception as error:
            raise http_error(error) from error

    @router.get("/{session_id}/specs", response_model=list[SpecVersionRead])
    def list_specs(session_id: str) -> list[SpecVersionRead]:
        try:
            return queries.specs(session_id)
        except Exception as error:
            raise http_error(error) from error

    @router.get("/{session_id}/specs/{version}", response_model=SpecVersionRead)
    def get_spec(session_id: str, version: str) -> SpecVersionRead:
        try:
            return queries.spec(session_id, version)
        except Exception as error:
            raise http_error(error) from error

    @router.get("/{session_id}/work-items", response_model=list[WorkItemRead])
    def list_work_items(session_id: str) -> list[WorkItemRead]:
        try:
            return queries.work_items(session_id)
        except Exception as error:
            raise http_error(error) from error

    @router.get("/{session_id}/work-items/{work_item_id}", response_model=WorkItemRead)
    def get_work_item(session_id: str, work_item_id: str) -> WorkItemRead:
        try:
            return queries.work_item(session_id, work_item_id)
        except Exception as error:
            raise http_error(error) from error

    @router.get("/{session_id}/agents/runtime", response_model=list[AgentRuntimeRead])
    def list_agent_runtime(session_id: str) -> list[AgentRuntimeRead]:
        try:
            return queries.agent_runtime(session_id)
        except Exception as error:
            raise http_error(error) from error

    @router.get("/{session_id}/agent-specs", response_model=list[AgentSpecRead])
    def list_agent_specs(session_id: str) -> list[AgentSpecRead]:
        try:
            return queries.agent_specs(session_id)
        except Exception as error:
            raise http_error(error) from error

    @router.get("/{session_id}/agent-specs/{agent_spec_id}", response_model=AgentSpecRead)
    def get_agent_spec(session_id: str, agent_spec_id: str) -> AgentSpecRead:
        try:
            return queries.agent_spec(session_id, agent_spec_id)
        except Exception as error:
            raise http_error(error) from error

    @router.get("/{session_id}/events", response_model=list[AuditEventRead])
    def list_events(session_id: str) -> list[AuditEventRead]:
        try:
            return queries.events(session_id)
        except Exception as error:
            raise http_error(error) from error

    return router
