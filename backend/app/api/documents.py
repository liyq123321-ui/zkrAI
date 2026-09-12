import asyncio
import base64
import io
import json
from fastapi import APIRouter, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse, PlainTextResponse
from pydantic import Field
from .blocks import Input
from app.services.prd_templates import TEMPLATES
from app.services.block_service import BlockError


class Open(Input):
    session_id: str


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


class Context(Submit):
    background: str = Field(max_length=30000)
    global_rules: str = Field(max_length=12000)


def build_router(service, ai, actors):
    router = APIRouter(prefix="/prd-documents", tags=["prd-documents"])

    @router.get("/templates")
    def templates():
        return TEMPLATES

    @router.post("")
    def open_document(body: Open, request: Request):
        return service.open(body.session_id, actors.resolve(request, body.actor_id))

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
    def submit(document_id: str, body: Submit, request: Request):
        return service.submit(document_id, actors.resolve(request, body.actor_id), body.expected_revision)

    @router.get("/{document_id}/markdown", response_class=PlainTextResponse)
    def markdown(document_id: str, request: Request, actor_id: str | None = None):
        with service.factory() as db:
            doc = service.document(db, document_id, actors.resolve(request, actor_id))
            return service.markdown(service.blocks(db, doc))

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
                await asyncio.sleep(1)
        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return router
