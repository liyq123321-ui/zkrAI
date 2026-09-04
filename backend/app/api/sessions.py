"""HTTP routes for the project specification workflow."""

from collections.abc import Callable

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.agents.gateway import AgentGateway
from app.agents.codex import AgentExecutionError
from app.domain.types import CommandAction, ProjectPhase
from app.identity import ActorResolver
from app.schemas.workflow import CommandResult, SessionCommandRequest, SessionCreateRequest, SessionState
from app.services.command_service import CommandService
from app.services.error_classification import classify_workflow_error
from app.services.execution_service import ExecutionService
from app.services.project_service import ProjectService
from app.services.query_service import (
    AgentSpecRead,
    AuditEventRead,
    QueryService,
    SessionSummaryRead,
    SpecVersionRead,
    WorkItemRead,
)
from app.services.spec_service import SpecService


def build_router(
    session_factory: Callable[[], Session],
    agent_gateway: AgentGateway,
    actor_resolver: ActorResolver,
) -> APIRouter:
    router = APIRouter(prefix="/sessions", tags=["sessions"])
    projects = ProjectService(session_factory, agent_gateway)
    specs = SpecService(session_factory, agent_gateway)
    from app.services.decomposition_service import DecompositionService

    decomposition = DecompositionService(session_factory, agent_gateway)
    execution = ExecutionService(session_factory)
    commands = CommandService(
        session_factory,
        handlers={
            CommandAction.MESSAGE: projects.as_command_handler(),
            CommandAction.CREATE_SPEC: specs.as_command_handler(),
            CommandAction.REVISE: specs.as_command_handler(),
            CommandAction.RESTORE_SPEC_VERSION: specs.as_command_handler(),
            CommandAction.CONVERT_TO_WORK_ITEM: decomposition.as_command_handler(),
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

    @router.post("/{session_id}/commands", response_model=CommandResult)
    async def execute_command(
        session_id: str, request_body: SessionCommandRequest, request: Request
    ) -> CommandResult:
        actor_id = actor_resolver.resolve(request, request_body.actor_id)
        request_body = request_body.model_copy(update={"actor_id": actor_id})
        try:
            return await commands.execute(session_id, request_body)
        except Exception as error:
            raise http_error(error) from error

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
