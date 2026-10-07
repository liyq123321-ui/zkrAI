"""Block mutations and background AI endpoints."""
from typing import Literal
from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field
from app.ai.provider import DocumentEditStep


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actor_id: str | None = None


class Save(Input):
    expected_version: int = Field(ge=1)
    content: str = Field(max_length=100000)
    title: str = Field(min_length=1, max_length=200)
    instruction: str = Field(max_length=6000)
    parent_id: str | None = None
    dependencies: list[str] = Field(default_factory=list, max_length=100)


class Run(Input):
    type: Literal["generate", "revise", "review"]
    request_id: str = Field(min_length=1, max_length=100)
    feedback: str = Field(default="", max_length=12000)
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    comment_id: str | None = None


class Apply(Input):
    expected_version: int = Field(ge=1)
    force: bool = False


class Restore(Input):
    expected_version: int = Field(ge=1)
    version: int = Field(ge=1)


class Target(BaseModel):
    model_config = ConfigDict(extra="forbid")
    block_id: str
    base_version: int = Field(ge=1)
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    selected_text: str | None = Field(default=None, min_length=1, max_length=2000)
    action: Literal["comment", "delete"] = "comment"


class Comment(Input):
    text: str = Field(min_length=1, max_length=12000)
    targets: list[Target] = Field(min_length=1, max_length=100)


class Resolve(Input):
    resolved: bool


class Retry(Input):
    request_id: str = Field(min_length=1, max_length=100)


class ChatEditPlan(Input):
    instruction: str = Field(min_length=1, max_length=12000)
    target_ids: list[str] = Field(min_length=1, max_length=8)
    selected_text: str = Field(default="", max_length=2000)
    screenshot_anchor: str = Field(default="", max_length=2000)
    conversation_context: str = Field(default="", max_length=2400)


class ChatEditApply(Input):
    expected_revision: int = Field(ge=1)
    steps: list[DocumentEditStep] = Field(min_length=1, max_length=500)
    alternative_id: str | None = None
    job_id: str | None = None


class ChatRebase(Input):
    steps: list[DocumentEditStep] = Field(min_length=1, max_length=500)


class CandidateReuse(Input):
    job_id: str = Field(max_length=100)
    indices: list[int] = Field(min_length=1, max_length=500)
    take_indices: list[int] = Field(default_factory=list, max_length=500)


class SelectionPreview(Input):
    expected_revision: int = Field(ge=1)
    steps: list[DocumentEditStep] = Field(min_length=1, max_length=500)


class ChatBulkPlan(Input):
    instruction: str = Field(min_length=1, max_length=12000)


class DocumentRestorePlan(Input):
    version: int | None = Field(default=None, ge=1)


class DocumentRestoreApply(Input):
    version: int = Field(ge=1)
    expected_revision: int = Field(ge=1)


def build_router(service, ai, actors, document_edits=None):
    router = APIRouter(prefix="/prd-documents/{document_id}", tags=["prd-blocks"])

    if document_edits is not None:
        @router.post('/chat-edits/reuse')
        def reuse_candidate(document_id: str, body: CandidateReuse, request: Request):
            from app.services.document_candidate_reuse import reuse
            return reuse(document_edits, document_id, actors.resolve(request, body.actor_id), body.job_id, body.indices, body.take_indices)

        @router.post('/chat-edits/selection-preview')
        def preview_selection(document_id: str, body: SelectionPreview, request: Request):
            actor = actors.resolve(request, body.actor_id)
            with service.factory() as db:
                doc = service.document(db, document_id, actor)
                from app.services.block_service import BlockError
                from app.ai.provider import DocumentEditPlan
                if doc.revision != body.expected_revision: raise BlockError('正文已变化，请先复用候选并核对冲突', 409)
                source = [{key: getattr(b, key) for key in ('id', 'title', 'parent_id', 'order', 'version', 'content', 'instruction', 'dependencies', 'fragment')}
                          for b in service.blocks(db, doc)]
                return {'preview': document_edits._preview(source, document_edits._simulate(source, DocumentEditPlan(summary='所选修改', steps=body.steps)))}

        @router.post("/chat-edits/restore-plan")
        def restore_plan(document_id: str, body: DocumentRestorePlan, request: Request):
            return document_edits.restore_preview(document_id, actors.resolve(request, body.actor_id), body.version)

        @router.post("/chat-edits/restore")
        def restore_document(document_id: str, body: DocumentRestoreApply, request: Request):
            return document_edits.restore_apply(document_id, actors.resolve(request, body.actor_id),
                                                 body.expected_revision, body.version)

        @router.post("/chat-edits/plan")
        async def plan_chat_edit(document_id: str, body: ChatEditPlan, request: Request):
            return await document_edits.plan(document_id, actors.resolve(request, body.actor_id),
                                             body.instruction, body.target_ids, body.selected_text,
                                             body.screenshot_anchor, body.conversation_context)

        @router.post("/chat-edits/apply")
        def apply_chat_edit(document_id: str, body: ChatEditApply, request: Request):
            return document_edits.apply(document_id, actors.resolve(request, body.actor_id),
                                        body.expected_revision, body.steps, body.alternative_id, body.job_id)

        @router.post("/chat-edits/rebase")
        def rebase_chat_edit(document_id: str, body: ChatRebase, request: Request):
            return document_edits.rebase(document_id, actors.resolve(request, body.actor_id), body.steps)

        @router.post("/chat-edits/bulk-plan")
        async def plan_bulk_edit(document_id: str, body: ChatBulkPlan, request: Request):
            return await document_edits.plan_bulk(document_id, actors.resolve(request, body.actor_id),
                                                  body.instruction)

    @router.put("/blocks/{block_id}")
    def save(document_id: str, block_id: str, body: Save, request: Request):
        return service.save(document_id, block_id, actors.resolve(request, body.actor_id), body.expected_version,
                            body.model_dump(exclude={"actor_id", "expected_version"}))

    @router.get("/blocks/{block_id}/history")
    def history(document_id: str, block_id: str, request: Request, actor_id: str | None = None):
        return service.versions(document_id, block_id, actors.resolve(request, actor_id))

    @router.post("/blocks/{block_id}/restore")
    def restore(document_id: str, block_id: str, body: Restore, request: Request):
        return service.restore(document_id, block_id, actors.resolve(request, body.actor_id), body.version, body.expected_version)

    @router.post("/blocks/{block_id}/runs", status_code=202)
    async def run(document_id: str, block_id: str, body: Run, request: Request):
        from app.services.block_service import BlockError
        if (body.start_line is None) != (body.end_line is None):
            raise BlockError("请同时指定起始行和结束行", 422)
        line_range = {"start_line": body.start_line, "end_line": body.end_line} if body.start_line else None
        result = ai.enqueue(document_id, actors.resolve(request, body.actor_id), body.type, body.request_id,
                            block_id, body.feedback, line_range, body.comment_id)
        return {k: v for k, v in result.items() if k != "input_snapshot"}

    @router.post("/runs/{run_id}/apply")
    def apply(document_id: str, run_id: str, body: Apply, request: Request):
        return ai.apply(document_id, run_id, actors.resolve(request, body.actor_id), body.expected_version, body.force)

    @router.post("/runs/{run_id}/cancel")
    async def cancel(document_id: str, run_id: str, body: Input, request: Request):
        return ai.cancel(document_id, run_id, actors.resolve(request, body.actor_id))

    @router.post("/runs/{run_id}/retry", status_code=202)
    async def retry(document_id: str, run_id: str, body: Retry, request: Request):
        run = ai.retry(document_id, run_id, actors.resolve(request, body.actor_id), body.request_id)
        return {k: v for k, v in run.items() if k != "input_snapshot"}

    @router.post("/comments")
    def comment(document_id: str, body: Comment, request: Request):
        return service.comment(document_id, actors.resolve(request, body.actor_id), body.text, [t.model_dump() for t in body.targets])

    @router.patch("/comments/{comment_id}")
    def resolve(document_id: str, comment_id: str, body: Resolve, request: Request):
        return service.resolve(document_id, comment_id, actors.resolve(request, body.actor_id), body.resolved)

    return router
