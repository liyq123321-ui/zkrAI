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
from app.agents.gateway import AgentGateway
from app.api.chat import build_router as build_chat_router
from app.api.prd_review import build_router as build_prd_review_router
from app.api.sessions import build_router as build_sessions_router
from app.config import Settings
from app.database.database import SessionLocal, init_database
from app.identity import ActorResolver
from app.services.gitea import GiteaClient
from app.services.pm_agent import ReviewPublishCoordinator
from app.services.prd_review import PrdReviewService


def create_app(
    settings: Settings | None = None,
    agent_gateway: AgentGateway | None = None,
    session_factory: Callable[[], Session] | None = None,
    gitea_client: GiteaClient | None = None,
) -> FastAPI:
    """Build an application whose runtime dependencies can be safely injected."""

    settings = settings or Settings.from_env()
    agent_gateway = agent_gateway or CodexAgentGateway(settings=settings)
    session_factory = session_factory or SessionLocal
    owns_gitea_client = gitea_client is None
    if gitea_client is None:
        gitea_client = GiteaClient(settings)
    review_service = PrdReviewService(session_factory, gitea_client)
    actor_resolver = ActorResolver(settings)
    publish_coordinator = ReviewPublishCoordinator(
        session_factory, gitea_client, agent_gateway
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            with session_factory() as db:
                init_database(db.get_bind())
            publish_coordinator.mark_interrupted_tasks()
            yield
        finally:
            if owns_gitea_client:
                await gitea_client.aclose()

    application = FastAPI(lifespan=lifespan)

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
        build_sessions_router(session_factory, agent_gateway, actor_resolver)
    )
    application.include_router(
        build_chat_router(session_factory, agent_gateway, actor_resolver)
    )
    application.include_router(
        build_prd_review_router(review_service, publish_coordinator, actor_resolver)
    )
    return application


app = create_app()
