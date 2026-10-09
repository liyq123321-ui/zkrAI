"""Draft presentation (PPT) endpoints for the Block PRD editor.

Generating the team / external PPT historically required the PRD to be frozen
(「进入下一阶段」). This router adds the same capability for an **unfrozen
draft**: the current block document is fed through the identical Agent/skill
route (:meth:`PrdPresentationService.generate_draft_payload`, the same
``ppt-generation`` + ``officecli-skill`` pipeline as the frozen version) and the
resulting PPTX is persisted as a ``DocumentArtifact`` keyed by document revision,
so it can be re-downloaded without regenerating.

Alongside the PPTX the service renders a browsable HTML deck, persisted as a
second artifact and served at ``/preview`` (public-by-URL, like
``/prd-documents/{id}/prototype/content``); the download button lives inside that
page instead of downloading straight after generation.

Only the wording on the title slide and a ``stage=draft`` marker differ. The
frozen-PRD route (``/sessions/{sid}/presentations/{audience}``) is untouched.
"""

from typing import Literal

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.services.block_service import BlockError

PPTX_MIME_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
HTML_MIME_TYPE = "text/html"
DRAFT_PRESENTATION_KINDS = {
    "team": "pptx_presentation_team",
    "external": "pptx_presentation_external",
}
DRAFT_PRESENTATION_PREVIEW_KINDS = {
    "team": "pptx_presentation_team_preview",
    "external": "pptx_presentation_external_preview",
}
Audience = Literal["team", "external"]


class DraftPresentationRequest(BaseModel):
    actor_id: str | None = None


def build_router(block_service, asset_service, presentation_service, actors):
    router = APIRouter(prefix="/prd-documents", tags=["prd-documents"])

    def _path(document_id: str, audience: str) -> str:
        return f"/prd-documents/{document_id}/presentations/{audience}"

    def _preview_path(document_id: str, audience: str) -> str:
        return _path(document_id, audience) + "/preview"

    @router.get("/{document_id}/presentations/{audience}")
    def draft_presentation(document_id: str, audience: Audience, request: Request, actor_id: str | None = None):
        actor = actors.resolve(request, actor_id)
        if asset_service is None:
            raise BlockError("PPT 未配置", 503)
        source = block_service.prototype_source(document_id, actor)
        with block_service.factory() as db:
            artifact = asset_service.artifact_for_revision(
                db, document_id, source["revision"], DRAFT_PRESENTATION_KINDS[audience]
            )
        if artifact is None:
            return {
                "document_id": document_id, "revision": source["revision"],
                "audience": audience, "status": "missing",
            }
        return {
            "document_id": document_id, "revision": source["revision"],
            "audience": audience, "status": "ready",
            "file_name": artifact.file_name, "size_bytes": artifact.size_bytes,
            "content_url": _path(document_id, audience) + "/file",
            "preview_url": _preview_path(document_id, audience),
        }

    @router.post("/{document_id}/presentations/{audience}")
    async def generate_draft_presentation(
        document_id: str, audience: Audience, body: DraftPresentationRequest, request: Request
    ):
        actor = actors.resolve(request, body.actor_id)
        if presentation_service is None or asset_service is None:
            raise BlockError("PPT 生成未配置", 503)
        source = block_service.prototype_source(document_id, actor)
        markdown = (source["markdown"] or "").strip()
        if not markdown:
            raise BlockError("当前 PRD 草稿还没有正文，无法生成演示 PPT", 422)
        try:
            result = await presentation_service.generate_draft_payload(
                source["project_id"],
                document_id=document_id,
                revision=source["revision"],
                markdown=markdown,
                audience=audience,
                actor_id=actor,
            )
        except ValueError as error:
            raise BlockError(str(error), 422) from error
        except Exception as error:
            raise BlockError("PPT 生成失败，请检查模型/Agent 配置后重试", 502) from error
        with block_service.factory() as db:
            asset_service.store_artifact(
                db,
                document_id=document_id,
                revision=source["revision"],
                actor=actor,
                file_name=f"prd-{audience}.pptx",
                content=result.pptx,
                kind=DRAFT_PRESENTATION_KINDS[audience],
                mime_type=PPTX_MIME_TYPE,
                extension="pptx",
            )
            asset_service.store_artifact(
                db,
                document_id=document_id,
                revision=source["revision"],
                actor=actor,
                file_name=f"prd-{audience}-preview.html",
                content=result.preview_html.encode("utf-8"),
                kind=DRAFT_PRESENTATION_PREVIEW_KINDS[audience],
                mime_type=HTML_MIME_TYPE,
                extension="html",
            )
            db.commit()
        return {
            "document_id": document_id, "revision": source["revision"],
            "audience": audience, "status": "ready", "title": result.title,
            "content_url": _path(document_id, audience) + "/file",
            "preview_url": _preview_path(document_id, audience),
        }

    @router.get("/{document_id}/presentations/{audience}/preview", response_class=HTMLResponse)
    def preview_draft_presentation(document_id: str, audience: Audience):
        """Serve the persisted draft deck (public-by-URL, like the draft prototype page)."""

        if asset_service is None:
            raise BlockError("PPT 未配置", 503)
        revision = block_service.document_revision(document_id)
        with block_service.factory() as db:
            artifact = asset_service.artifact_for_revision(
                db, document_id, revision, DRAFT_PRESENTATION_PREVIEW_KINDS[audience]
            )
            if artifact is None:
                raise BlockError("还没有为当前 PRD 草稿生成这份 PPT 预览页", 404)
            raw = asset_service.artifact_bytes(artifact)
        return HTMLResponse(raw.decode("utf-8"))

    @router.get("/{document_id}/presentations/{audience}/file")
    def download_draft_presentation(
        document_id: str, audience: Audience, request: Request, actor_id: str | None = None
    ):
        actors.resolve(request, actor_id)
        if asset_service is None:
            raise BlockError("PPT 未配置", 503)
        revision = block_service.document_revision(document_id)
        with block_service.factory() as db:
            artifact = asset_service.artifact_for_revision(
                db, document_id, revision, DRAFT_PRESENTATION_KINDS[audience]
            )
            if artifact is None:
                raise BlockError("还没有为当前 PRD 草稿生成这份 PPT", 404)
            raw = asset_service.artifact_bytes(artifact)
        return Response(
            raw,
            media_type=PPTX_MIME_TYPE,
            headers={
                "Content-Disposition": f'attachment; filename="prd-{audience}-draft.pptx"',
                "Cache-Control": "private, no-store",
            },
        )

    return router
