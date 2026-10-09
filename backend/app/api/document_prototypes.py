"""Draft HTML prototype endpoints for the Block PRD editor.

Generating an HTML prototype historically required the PRD to be frozen
(「进入下一阶段」). This router adds the same capability for an **unfrozen
draft**: the current block document is fed through the identical Agent/skill
route (:meth:`PrdPrototypeService.generate_draft_payload`) and the resulting
HTML is persisted as a ``DocumentArtifact`` keyed by document revision, so it
can be re-opened without regenerating.

The frozen-PRD route (``/prd/{wi}/v/{number}/prototype``) is untouched.
"""

import hashlib

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.services.block_service import BlockError

DRAFT_PROTOTYPE_KIND = "html_prototype"
DRAFT_PROTOTYPE_MIME = "text/html"


class DraftPrototypeRequest(BaseModel):
    actor_id: str | None = None


def build_router(block_service, asset_service, prototype_service, actors):
    router = APIRouter(prefix="/prd-documents", tags=["prd-documents"])

    def _content_url(document_id: str) -> str:
        return f"/prd-documents/{document_id}/prototype/content"

    @router.get("/{document_id}/prototype")
    def draft_prototype(document_id: str, request: Request, actor_id: str | None = None):
        actor = actors.resolve(request, actor_id)
        if asset_service is None:
            raise BlockError("HTML 原型未配置", 503)
        source = block_service.prototype_source(document_id, actor)
        with block_service.factory() as db:
            artifact = asset_service.artifact_for_revision(
                db, document_id, source["revision"], DRAFT_PROTOTYPE_KIND
            )
        if artifact is None:
            return {"document_id": document_id, "revision": source["revision"], "status": "missing"}
        return {
            "document_id": document_id,
            "revision": source["revision"],
            "status": "ready",
            "file_name": artifact.file_name,
            "size_bytes": artifact.size_bytes,
            "content_url": _content_url(document_id),
        }

    @router.post("/{document_id}/prototype")
    async def generate_draft_prototype(
        document_id: str, body: DraftPrototypeRequest, request: Request
    ):
        actor = actors.resolve(request, body.actor_id)
        if prototype_service is None or asset_service is None:
            raise BlockError("HTML 原型生成未配置", 503)
        source = block_service.prototype_source(document_id, actor)
        markdown = (source["markdown"] or "").strip()
        if not markdown:
            raise BlockError("当前 PRD 草稿还没有正文，无法生成 HTML 原型", 422)
        digest = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
        payload = await prototype_service.generate_draft_payload(
            source["project_id"],
            document_id=document_id,
            revision=source["revision"],
            content_hash=digest,
            markdown=markdown,
        )
        if payload is None:
            raise BlockError("HTML 原型生成失败或未启用，请检查模型/Agent 配置后重试", 502)
        raw = payload.html.encode("utf-8")
        with block_service.factory() as db:
            artifact = asset_service.store_artifact(
                db,
                document_id=document_id,
                revision=source["revision"],
                actor=actor,
                file_name="prototype.html",
                content=raw,
                kind=DRAFT_PROTOTYPE_KIND,
                mime_type=DRAFT_PROTOTYPE_MIME,
                extension="html",
            )
            db.commit()
        return {
            "document_id": document_id,
            "revision": source["revision"],
            "status": "ready",
            "title": payload.title,
            "generation_summary": payload.generation_summary,
            "content_url": _content_url(document_id),
        }

    @router.get("/{document_id}/prototype/content", response_class=HTMLResponse)
    def draft_prototype_content(document_id: str):
        """Serve the persisted draft prototype (public-by-URL, like the frozen route)."""

        if asset_service is None:
            raise BlockError("HTML 原型未配置", 503)
        revision = block_service.document_revision(document_id)
        with block_service.factory() as db:
            artifact = asset_service.artifact_for_revision(
                db, document_id, revision, DRAFT_PROTOTYPE_KIND
            )
            if artifact is None:
                raise BlockError("还没有为当前 PRD 草稿生成 HTML 原型", 404)
            raw = asset_service.artifact_bytes(artifact)
        return HTMLResponse(raw.decode("utf-8"))

    return router
