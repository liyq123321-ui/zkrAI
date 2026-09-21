import asyncio
import base64
import io
import json
import logging
from urllib.parse import quote
from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import Response, StreamingResponse, PlainTextResponse
from pydantic import Field
from .blocks import Input
from app.services.prd_templates import TEMPLATES
from app.services.block_service import BlockError
from app.services.document_asset_service import DOCX_MIME_TYPE


logger = logging.getLogger(__name__)


async def _generate_prd_prototype(prototypes, project_id: str) -> None:
    """Start HTML prototype generation for a freshly frozen PRD.

    Reuses :class:`PrdPrototypeService` exactly as the Spec command path does.
    The prototype is a derived review aid, so a failure here must never make an
    already-committed PRD submission look failed.
    """

    try:
        if not prototypes.automatic_generation_allowed(project_id):
            return
        await prototypes.ensure_for_project(project_id)
    except Exception:
        logger.exception(
            "Could not generate PRD HTML prototype after submission",
            extra={"project_id": project_id},
        )


class Open(Input):
    session_id: str
    auto_generate: bool = False


class Outline(Input):
    template: str = Field(default="custom", max_length=100)
    outline: str = Field(default="", max_length=100000)
    expected_revision: int = Field(ge=1)


class ImportFile(Input):
    filename: str = Field(max_length=255)
    content_base64: str = Field(max_length=11_200_000)
    expected_revision: int = Field(ge=1)


class Plan(Input):
    request_id: str = Field(min_length=1, max_length=100)


class Submit(Input):
    expected_revision: int = Field(ge=1)


class Review(Submit):
    request_id: str = Field(min_length=1, max_length=100)


class Context(Submit):
    background: str = Field(max_length=30000)
    global_rules: str = Field(max_length=12000)


class AssetUpload(Input):
    block_id: str | None = Field(default=None, max_length=100)
    filename: str = Field(min_length=1, max_length=255)
    mime_type: str | None = Field(default=None, max_length=100)
    content_base64: str = Field(max_length=16_800_000)


class DocxImport(Input):
    filename: str = Field(min_length=1, max_length=255)
    content_base64: str = Field(max_length=33_600_000)
    expected_revision: int = Field(ge=1)


def build_router(service, ai, actors, assets=None, docx=None, prototypes=None):
    router = APIRouter(prefix="/prd-documents", tags=["prd-documents"])

    @router.get("/templates")
    def templates():
        return TEMPLATES

    @router.post("")
    async def open_document(body: Open, request: Request):
        actor = actors.resolve(request, body.actor_id)
        snapshot = service.open(body.session_id, actor)
        if body.auto_generate:
            service_id = snapshot["document"]["id"]
            ai.start_initial(service_id, actor)
            return service.snapshot(service_id, actor)
        return snapshot

    @router.get("/{document_id}")
    def snapshot(document_id: str, request: Request, actor_id: str | None = None):
        return service.snapshot(document_id, actors.resolve(request, actor_id))

    @router.put("/{document_id}/outline")
    def outline(document_id: str, body: Outline, request: Request):
        return service.import_outline(document_id, actors.resolve(request, body.actor_id), body.template, body.outline, body.expected_revision)

    @router.post("/{document_id}/import")
    def import_file(document_id: str, body: ImportFile, request: Request):
        actor = actors.resolve(request, body.actor_id)
        service.snapshot(document_id, actor)
        try:
            raw = base64.b64decode(body.content_base64, validate=True)
            if len(raw) > 8 * 1024 * 1024:
                raise ValueError("文件不能超过 8 MB")
            if body.filename.lower().endswith('.pdf'):
                from pypdf import PdfReader
                reader = PdfReader(io.BytesIO(raw))
                if reader.is_encrypted or len(reader.pages) > 50:
                    raise ValueError("只支持未加密且不超过 50 页的文字 PDF")
                outline = '\n'.join(page.extract_text() or '' for page in reader.pages)
                if not outline.strip():
                    raise ValueError("PDF 没有可提取文字，请粘贴大纲或上传文字型 PDF")
            elif body.filename.lower().endswith(('.md', '.txt')):
                outline = raw.decode('utf-8-sig')
            else:
                raise ValueError("支持 Markdown、TXT 或文字型 PDF")
        except Exception as error:
            raise BlockError("导入失败：" + str(error)[:300], 422) from error
        # A PDF contains prose as well as headings. Return its extracted text for
        # the user to trim/confirm; never turn tutorial paragraphs into live blocks.
        return {"outline": outline[:100000], "requires_outline_confirmation": True}

    @router.post("/{document_id}/plan", status_code=202)
    async def plan(document_id: str, body: Plan, request: Request):
        run = ai.enqueue(document_id, actors.resolve(request, body.actor_id), "plan", body.request_id)
        return {k: v for k, v in run.items() if k != "input_snapshot"}

    @router.put("/{document_id}/context")
    def context(document_id: str, body: Context, request: Request):
        return service.context(document_id, actors.resolve(request, body.actor_id), body.expected_revision, body.background, body.global_rules)

    @router.post("/{document_id}/submit")
    def submit(document_id: str, body: Submit, request: Request, background_tasks: BackgroundTasks):
        result = service.submit(document_id, actors.resolve(request, body.actor_id), body.expected_revision)
        project_id = result.get("project_id") if isinstance(result, dict) else None
        if prototypes is not None and project_id:
            # Freezing the PRD is the trigger for its HTML prototype.
            background_tasks.add_task(_generate_prd_prototype, prototypes, project_id)
        return result

    @router.post("/{document_id}/review", status_code=202)
    async def review(document_id: str, body: Review, request: Request):
        run = ai.enqueue_document_review(document_id, actors.resolve(request, body.actor_id), body.request_id, body.expected_revision)
        return {key: value for key, value in run.items() if key != "input_snapshot"}

    @router.post("/{document_id}/review/{review_run_id}/resolve", status_code=202)
    async def resolve_review(document_id: str, review_run_id: str, body: Review, request: Request):
        run = ai.enqueue_document_revision(document_id, actors.resolve(request, body.actor_id), review_run_id,
                                           body.request_id, body.expected_revision)
        return {key: value for key, value in run.items() if key != "input_snapshot"}

    @router.get("/{document_id}/markdown", response_class=PlainTextResponse)
    def markdown(document_id: str, request: Request, actor_id: str | None = None):
        with service.factory() as db:
            doc = service.document(db, document_id, actors.resolve(request, actor_id))
            return service.markdown(service.blocks(db, doc))

    if assets is not None:
        @router.post("/{document_id}/assets", status_code=201)
        def upload_asset(document_id: str, body: AssetUpload, request: Request):
            try:
                raw = base64.b64decode(body.content_base64, validate=True)
            except Exception as error:
                raise BlockError("图片编码无效", 422) from error
            return assets.upload(
                document_id,
                body.block_id,
                actors.resolve(request, body.actor_id),
                body.filename,
                body.mime_type,
                raw,
            )

        @router.get("/{document_id}/assets/{asset_id}")
        def asset(document_id: str, asset_id: str, request: Request, actor_id: str | None = None):
            stored, raw = assets.read(document_id, asset_id, actors.resolve(request, actor_id))
            return Response(
                raw,
                media_type=stored.mime_type,
                headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(stored.file_name)}"},
            )

    if docx is not None:
        @router.post("/{document_id}/export/docx")
        def export_docx(document_id: str, body: Input, request: Request):
            file_name, raw, revision = docx.export(
                document_id, actors.resolve(request, body.actor_id)
            )
            return Response(
                raw,
                media_type=DOCX_MIME_TYPE,
                headers={
                    "Content-Disposition": f"attachment; filename*=UTF-8''{quote(file_name)}",
                    "X-FirstFlight-Document-Revision": str(revision),
                },
            )

        @router.post("/{document_id}/import/docx")
        def import_docx(document_id: str, body: DocxImport, request: Request):
            if not body.filename.lower().endswith(".docx"):
                raise BlockError("请选择 DOCX 文件", 422)
            try:
                raw = base64.b64decode(body.content_base64, validate=True)
            except Exception as error:
                raise BlockError("DOCX 编码无效", 422) from error
            actor = actors.resolve(request, body.actor_id)
            result = docx.import_document(
                document_id, actor, body.expected_revision, body.filename, raw
            )
            return {**service.snapshot(document_id, actor), "docx_import": result}

    @router.get("/{document_id}/events")
    async def events(document_id: str, request: Request, actor_id: str | None = None):
        actor = actors.resolve(request, actor_id)
        service.snapshot(document_id, actor)
        async def stream():
            last = None
            while not await request.is_disconnected():
                payload = json.dumps(jsonable_encoder(service.snapshot(document_id, actor)), ensure_ascii=False)
                if payload != last:
                    yield f"event: snapshot\ndata: {payload}\n\n"
                    last = payload
                else:
                    yield ": heartbeat\n\n"
                # Sentence-level Block previews are short-lived. A responsive poll
                # interval lets the client render each converted sentence without
                # weakening the final version/apply checks.
                await asyncio.sleep(0.1)
        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return router
