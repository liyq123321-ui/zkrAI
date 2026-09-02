"""Stable HTTP boundary for Gitea-backed PRD review."""

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from app.schemas.prd_review import (
    CommentCreateRequest,
    CommentReplyRequest,
    CommentResolveRequest,
    PrdCommentRead,
    PrdCommentableLinesRead,
    PrdDocumentRead,
    PublishReviewRequest,
    ReviewTaskRead,
)
from app.identity import ActorResolver
from app.services.gitea import CommentLineNotInDiff, GiteaError
from app.services.pm_agent import NoUnresolvedComments, ReviewPublishCoordinator
from app.services.prd_review import (
    PrdContentConflict,
    PrdForbidden,
    PrdNotFound,
    PrdReviewClosed,
    PrdServiceError,
    PrdReviewService,
)


class PublishReviewAccepted(BaseModel):
    """The frozen task identity returned before background publication."""

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1, max_length=255)
    base_version: int = Field(gt=0)
    comment_count: int = Field(gt=0)


def _http_error(error: Exception) -> HTTPException:
    """Return only public, deterministic details for PRD review failures."""

    if isinstance(error, PrdNotFound):
        code, message, code_status = (
            error.code,
            "The requested PRD review resource was not found.",
            status.HTTP_404_NOT_FOUND,
        )
    elif isinstance(error, PrdForbidden):
        code, message, code_status = (
            error.code,
            "The actor is not allowed to modify this PRD review.",
            status.HTTP_403_FORBIDDEN,
        )
    elif isinstance(error, PrdContentConflict):
        code, message, code_status = (
            error.code,
            "The PRD review state conflicts with its current version.",
            status.HTTP_409_CONFLICT,
        )
    elif isinstance(error, PrdReviewClosed):
        code, message, code_status = (
            error.code,
            "The PRD review is not open for changes.",
            status.HTTP_400_BAD_REQUEST,
        )
    elif isinstance(error, NoUnresolvedComments):
        code, message, code_status = (
            error.code,
            "The PRD review has no unresolved comments to publish.",
            status.HTTP_400_BAD_REQUEST,
        )
    elif isinstance(error, CommentLineNotInDiff):
        code, message, code_status = (
            "COMMENT_LINE_NOT_IN_DIFF",
            "The requested comment line is not available in the PR diff.",
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    elif isinstance(error, GiteaError):
        if error.code == "GITEA_FORBIDDEN":
            code, message, code_status = (
                error.code,
                "The configured Gitea identity is not allowed to perform this action.",
                status.HTTP_403_FORBIDDEN,
            )
        elif error.code == "GITEA_NOT_FOUND":
            code, message, code_status = (
                error.code,
                "The requested Gitea PRD review resource was not found.",
                status.HTTP_404_NOT_FOUND,
            )
        else:
            code, message, code_status = (
                error.code,
                "The PRD review service is temporarily unavailable.",
                status.HTTP_503_SERVICE_UNAVAILABLE,
            )
    elif isinstance(error, PrdServiceError):
        code, message, code_status = (
            error.code,
            "The PRD review request could not be completed.",
            status.HTTP_409_CONFLICT,
        )
    else:
        code, message, code_status = (
            "PRD_REVIEW_UNAVAILABLE",
            "The PRD review service is temporarily unavailable.",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    return HTTPException(status_code=code_status, detail={"code": code, "message": message})


def build_router(
    service: PrdReviewService,
    coordinator: ReviewPublishCoordinator,
    actor_resolver: ActorResolver,
) -> APIRouter:
    """Build the synchronous review endpoints and durable publication boundary."""

    router = APIRouter(tags=["prd-review"])

    @router.get("/prd/{wi}", response_model=PrdDocumentRead)
    async def latest_prd(wi: str) -> PrdDocumentRead:
        try:
            return await service.latest(wi)
        except Exception as error:
            raise _http_error(error) from error

    @router.get("/prd/{wi}/v/{number}", response_model=PrdDocumentRead)
    async def prd_version(wi: str, number: int) -> PrdDocumentRead:
        try:
            return await service.version(wi, number)
        except Exception as error:
            raise _http_error(error) from error

    @router.get("/prd/{wi}/versions", response_model=list[PrdDocumentRead])
    async def prd_versions(wi: str) -> list[PrdDocumentRead]:
        try:
            return await service.versions(wi)
        except Exception as error:
            raise _http_error(error) from error

    @router.get("/prd/{wi}/comments", response_model=list[PrdCommentRead])
    async def prd_comments(wi: str) -> list[PrdCommentRead]:
        try:
            return await service.comments(wi)
        except Exception as error:
            raise _http_error(error) from error

    @router.get(
        "/prd/{wi}/commentable-lines", response_model=PrdCommentableLinesRead
    )
    async def prd_commentable_lines(wi: str) -> PrdCommentableLinesRead:
        try:
            return await service.commentable_lines(wi)
        except Exception as error:
            raise _http_error(error) from error

    @router.post("/prd/{wi}/comments", status_code=status.HTTP_204_NO_CONTENT)
    async def create_comment(
        wi: str, request_body: CommentCreateRequest, request: Request
    ) -> None:
        actor_id = actor_resolver.resolve(request, request_body.actor_id)
        request_body = request_body.model_copy(update={"actor_id": actor_id})
        try:
            await service.create_comment(wi, request_body)
        except Exception as error:
            raise _http_error(error) from error

    @router.post(
        "/prd/{wi}/comments/{comment_id}/reply", status_code=status.HTTP_204_NO_CONTENT
    )
    async def reply_comment(
        wi: str,
        comment_id: int,
        request_body: CommentReplyRequest,
        request: Request,
    ) -> None:
        actor_id = actor_resolver.resolve(request, request_body.actor_id)
        request_body = request_body.model_copy(update={"actor_id": actor_id})
        try:
            await service.reply(wi, comment_id, request_body)
        except Exception as error:
            raise _http_error(error) from error

    @router.post(
        "/prd/{wi}/comments/{comment_id}/resolve", status_code=status.HTTP_204_NO_CONTENT
    )
    async def resolve_comment(
        wi: str,
        comment_id: int,
        request_body: CommentResolveRequest,
        request: Request,
    ) -> None:
        actor_id = actor_resolver.resolve(request, request_body.actor_id)
        request_body = request_body.model_copy(update={"actor_id": actor_id})
        try:
            await service.resolve(wi, comment_id, request_body)
        except Exception as error:
            raise _http_error(error) from error

    @router.post(
        "/prd/{wi}/reviews/publish",
        response_model=PublishReviewAccepted,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def publish_review(
        wi: str,
        request_body: PublishReviewRequest,
        request: Request,
        background_tasks: BackgroundTasks,
    ) -> PublishReviewAccepted:
        actor_id = actor_resolver.resolve(request, request_body.actor_id)
        try:
            task = await coordinator.create_or_resume(wi, actor_id)
            background_tasks.add_task(coordinator.run, task.id)
            return PublishReviewAccepted(
                task_id=task.id,
                base_version=task.base_version,
                comment_count=len(task.comment_ids),
            )
        except Exception as error:
            raise _http_error(error) from error

    @router.get("/tasks/{task_id}", response_model=ReviewTaskRead)
    async def review_task(task_id: str) -> ReviewTaskRead:
        try:
            return coordinator.task(task_id)
        except KeyError as error:
            raise _http_error(PrdNotFound("review task not found")) from error
        except Exception as error:
            raise _http_error(error) from error

    return router
