"""FastAPI application assembly for the project workflow gateway."""

from collections.abc import Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.agents.codex import CodexAgentGateway
from app.agents.compatible import OpenAICompatibleStructuredRunner
from app.agents.gateway import AgentGateway
from app.agents.routing import RoutingAgentGateway
from app.api.chat import build_router as build_chat_router
from app.api.prd_review import build_router as build_prd_review_router
from app.api.sessions import build_router as build_sessions_router
from app.api.workspace_portal import build_router as build_workspace_router
from app.config import Settings
from app.database.database import SessionLocal, init_database
from app.domain.types import CommandAction
from app.identity import ActorResolver
from app.services.command_jobs import CommandJobCoordinator
from app.services.command_service import CommandService
from app.services.decomposition_service import DecompositionService
from app.services.gitea import GiteaClient
from app.services.pm_agent import ReviewPublishCoordinator
from app.services.prd_review import PrdReviewService
from app.services.prd_prototype import PrdPrototypeService
from app.services.agent_backends import AgentBackendService
from app.services.agent_runtime_events import AgentRuntimeEventStore
from app.services.weknora import WeKnoraClient
from app.services.workspace_portal import WorkspacePortalService
from app.api.documents import build_router as build_documents_router
from app.api.blocks import build_router as build_blocks_router
from app.services.block_service import BlockService, BlockError
from app.services.ai_service import AIService
from app.ai.codex_provider import CodexProvider
from app.ai.provider import AIProvider


def create_app(
    settings: Settings | None = None,
    agent_gateway: AgentGateway | None = None,
    session_factory: Callable[[], Session] | None = None,
    gitea_client: GiteaClient | None = None,
    auto_bind_prd_review: bool | None = None,
    workspace_service: WorkspacePortalService | None = None,
    block_provider: AIProvider | None = None,
) -> FastAPI:
    """Build an application whose runtime dependencies can be safely injected."""

    settings = settings or Settings.from_env()
    uses_runtime_database = session_factory is None
    session_factory = session_factory or SessionLocal
    agent_backends = AgentBackendService(session_factory, settings)
    if agent_gateway is None:
        routed_gateways = {
            "codex": CodexAgentGateway(
                settings=settings, session_factory=session_factory
            )
        }
        compatible = (
            (
                "deepseek",
                settings.deepseek_api_key,
                settings.deepseek_base_url,
                settings.deepseek_model,
            ),
            (
                "glm",
                settings.zhipu_api_key,
                settings.zhipu_base_url,
                settings.zhipu_model,
            ),
            (
                "kimi",
                settings.moonshot_api_key,
                settings.moonshot_base_url,
                settings.moonshot_model,
            ),
        )
        for provider, api_key, base_url, model in compatible:
            if not api_key:
                continue
            event_store = AgentRuntimeEventStore(session_factory)
            routed_gateways[provider] = CodexAgentGateway(
                runner=OpenAICompatibleStructuredRunner(
                    provider=provider,
                    api_key=api_key,
                    base_url=base_url,
                    model=model,
                    timeout_seconds=settings.agent_api_timeout_seconds,
                    runtime_event_store=event_store,
                ),
                settings=settings,
                session_factory=session_factory,
            )
        agent_gateway = RoutingAgentGateway(routed_gateways, agent_backends)
    if auto_bind_prd_review is None:
        auto_bind_prd_review = uses_runtime_database
    owns_gitea_client = gitea_client is None
    if gitea_client is None:
        gitea_client = GiteaClient(settings)
    review_service = PrdReviewService(session_factory, gitea_client)
    prototype_service = PrdPrototypeService(session_factory, agent_gateway)
    actor_resolver = ActorResolver(settings)
    block_service = BlockService(session_factory)
    block_ai = AIService(block_service, block_provider or CodexProvider(settings))
    publish_coordinator = ReviewPublishCoordinator(
        session_factory,
        gitea_client,
        agent_gateway,
        prototype_service=prototype_service,
    )
    decomposition_commands = CommandService(
        session_factory,
        handlers={
            CommandAction.CONVERT_TO_WORK_ITEM: DecompositionService(
                session_factory, agent_gateway
            ).as_command_handler()
        },
    )
    command_jobs = CommandJobCoordinator(session_factory, decomposition_commands.execute)
    workspace_service = workspace_service or WorkspacePortalService(
        session_factory, WeKnoraClient(settings)
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            with session_factory() as db:
                init_database(db.get_bind())
            command_jobs.mark_interrupted_jobs()
            publish_coordinator.mark_interrupted_tasks()
            block_ai.recover()
            yield
        finally:
            await block_ai.close()
            if owns_gitea_client:
                await gitea_client.aclose()

    application = FastAPI(lifespan=lifespan)
    application.state.block_ai = block_ai

    @application.exception_handler(BlockError)
    async def block_error(_, error: BlockError):
        return JSONResponse(status_code=error.status, content={"detail": {"code": "BLOCK_ERROR", "message": str(error)}})

    application.include_router(build_documents_router(block_service, block_ai, actor_resolver))
    application.include_router(build_blocks_router(block_service, block_ai, actor_resolver))

    if settings.cors_allowed_origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_allowed_origins),
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @application.get("/healthz")
    def health() -> dict[str, str]:
        with session_factory() as db:
            db.execute(text("SELECT 1"))
        return {"status": "ok", "database": "ok"}

    @application.exception_handler(RequestValidationError)
    async def request_validation_error(_, error: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={
                "detail": {
                    "code": "VALIDATION_ERROR",
                    "message": "request validation failed",
                    "errors": jsonable_encoder(error.errors()),
                }
            },
        )

    application.include_router(
        build_sessions_router(
            session_factory,
            agent_gateway,
            actor_resolver,
            review_service if auto_bind_prd_review else None,
            command_jobs,
            prototype_service,
            agent_backends,
        )
    )
    application.include_router(
        build_chat_router(
            session_factory, agent_gateway, actor_resolver, agent_backends
        )
    )
    application.include_router(
        build_prd_review_router(
            review_service,
            publish_coordinator,
            actor_resolver,
            prototype_service,
        )
    )
    application.include_router(
        build_workspace_router(workspace_service, actor_resolver)
    )
    return application


app = create_app()
