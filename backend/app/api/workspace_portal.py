"""HTTP facade for the owner workspace, knowledge, and skill catalog."""

from fastapi import APIRouter, HTTPException, Query, Request, status

from app.identity import ActorResolver
from app.services.weknora import WeKnoraError
from app.services.workspace_portal import WorkspacePortalService


def build_router(
    service: WorkspacePortalService,
    actor_resolver: ActorResolver,
) -> APIRouter:
    router = APIRouter(prefix="/workspace", tags=["workspace"])

    def actor(request: Request, actor_id: str | None) -> str:
        return actor_resolver.resolve(request, actor_id)

    def remote_error(error: Exception) -> HTTPException:
        if isinstance(error, ValueError):
            return HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "INVALID_WORKSPACE_REQUEST", "message": str(error)},
            )
        return HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "WEKNORA_UNAVAILABLE", "message": str(error)},
        )

    @router.get("/overview")
    async def overview(
        request: Request,
        actor_id: str | None = Query(default=None, min_length=1, max_length=128),
    ) -> dict:
        return await service.overview(actor(request, actor_id))

    @router.post("/sync")
    async def sync(
        request: Request,
        actor_id: str | None = Query(default=None, min_length=1, max_length=128),
    ) -> dict:
        try:
            return await service.sync(actor(request, actor_id))
        except (WeKnoraError, ValueError) as error:
            raise remote_error(error) from error

    @router.get("/knowledge-bases/{knowledge_base_id}/documents")
    async def knowledge_documents(
        knowledge_base_id: str,
        tenant_id: int = Query(gt=0),
    ) -> dict:
        try:
            return await service.knowledge_documents(knowledge_base_id, tenant_id)
        except (WeKnoraError, ValueError) as error:
            raise remote_error(error) from error

    @router.get("/skills/{skill_id}/files")
    async def skill_files(skill_id: str) -> dict:
        try:
            return {"items": await service.skill_files(skill_id)}
        except (WeKnoraError, ValueError) as error:
            raise remote_error(error) from error

    return router
