from typing import Literal
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from .blocks import Input
from app.services.document_object_target import ObjectTarget


class Payload(BaseModel):
    id: str | None = None
    text: str = Field(default="", max_length=4000)
    answer: str = Field(default="", max_length=6000)
    category: Literal["business", "information", "implementation"] = "business"
    block_ids: list[str] = Field(default_factory=list, max_length=100)
    source: str = Field(default="手动收集", max_length=200)
    note: str = Field(default="", max_length=4000)
    run_id: str | None = None
    status: Literal["unresolved", "decision", "recheck", "confirmed"] = "unresolved"
    question_status: Literal['open', 'answered', 'ignored', 'archived'] | None = None
    merge_into: str | None = None


class Update(Input):
    expected_revision: int = Field(ge=1)
    action: Literal["question", "question_state", "question_merge", "issue", "alternative_note"]
    payload: Payload


class Collect(Input):
    expected_revision: int = Field(ge=1)


class Plan(Input):
    kind: Literal["answers", "alternative", "anchor", "update"]
    instruction: str = Field(default="", max_length=12000)
    target_ids: list[str] = Field(default_factory=list, max_length=8)
    item_ids: list[str] = Field(default_factory=list, max_length=50)
    anchor_id: str = ""


class JobPlan(Plan):
    kind: Literal["answers", "alternative", "anchor", "update", "chat", "bulk"]
    request_id: str = Field(min_length=1, max_length=100)
    expected_revision: int = Field(ge=1)
    selected_text: str = Field(default="", max_length=2000)
    screenshot_anchor: str = Field(default="", max_length=2000)
    conversation_context: str = Field(default="", max_length=2400)
    object_target: ObjectTarget | None = None


class Retry(Input):
    request_id: str = Field(min_length=1, max_length=100)
    expected_revision: int = Field(ge=1)


class Command(Retry):
    instruction: str = Field(min_length=1, max_length=12000)
    target_ids: list[str] = Field(default_factory=list, max_length=8)
    comment_id: str | None = None


class LinkedPlan(Retry):
    instruction: str = Field(min_length=1, max_length=12000)
    target_ids: list[str] = Field(min_length=1, max_length=100)
    origin_id: str = Field(max_length=100)


class PolicyInput(BaseModel):
    id: str | None = None
    kind: Literal['decision', 'preference']
    scope: Literal['once', 'document', 'project'] = 'document'
    status: Literal['active', 'revoked'] = 'active'
    text: str = Field(default='', max_length=6000)
    reason: str = Field(default='', max_length=2000)
    block_ids: list[str] = Field(default_factory=list, max_length=100)
    requirement_ids: list[str] = Field(default_factory=list, max_length=100)
    source_kind: Literal['question', 'issue'] | None = None
    source_id: str | None = None


class PolicyUpdate(Input):
    expected_revision: int = Field(ge=1)
    payload: PolicyInput


def build_router(workbench, actors, jobs=None):
    router = APIRouter(prefix="/prd-documents/{document_id}/workbench", tags=["prd-workbench"])

    if jobs is not None:
        @router.post('/linked-jobs', status_code=202)
        async def linked_jobs(document_id: str, body: LinkedPlan, request: Request):
            return jobs.enqueue_linked(document_id, actors.resolve(request, body.actor_id), body.request_id, body.expected_revision, body.instruction, body.target_ids, body.origin_id)

        @router.get('/performance')
        def performance(document_id: str, request: Request):
            return jobs.performance(document_id, actors.resolve(request, None))
        @router.post("/jobs", status_code=202)
        async def enqueue(document_id: str, body: JobPlan, request: Request):
            return jobs.enqueue(document_id, actors.resolve(request, body.actor_id), body.request_id,
                                body.model_dump(exclude={"actor_id", "request_id", "expected_revision"}), body.expected_revision)

        @router.get("/jobs")
        def list_jobs(document_id: str, request: Request):
            return jobs.list(document_id, actors.resolve(request, None))

        @router.get("/jobs/{job_id}")
        def get_job(document_id: str, job_id: str, request: Request):
            return jobs.get(document_id, actors.resolve(request, None), job_id)

        @router.post("/jobs/{job_id}/cancel")
        async def cancel_job(document_id: str, job_id: str, body: Input, request: Request):
            return jobs.cancel(document_id, actors.resolve(request, body.actor_id), job_id)

        @router.post("/jobs/{job_id}/retry", status_code=202)
        async def retry_job(document_id: str, job_id: str, body: Retry, request: Request):
            return jobs.retry(document_id, actors.resolve(request, body.actor_id), job_id, body.request_id, body.expected_revision)

        @router.post("/command")
        async def command(document_id: str, body: Command, request: Request):
            from app.services.document_commands import dispatch
            from app.services.block_service import BlockError
            actor = actors.resolve(request, body.actor_id)
            with workbench.blocks.write() as db:
                doc = workbench.blocks.document(db, document_id, actor)
                _, state = workbench._state(db, doc)
                old = next((r for r in state.get("receipts", []) if r["receipt_id"] == body.request_id), None)
                if old:
                    if old["instruction"] != body.instruction: raise BlockError("请求标识已用于其他指令")
                    return old
            result = dispatch(workbench, jobs, document_id, actor, body.instruction,
                              body.target_ids, body.comment_id, body.request_id, body.expected_revision)
            result = {**result, "receipt_id": body.request_id, "instruction": body.instruction}
            with workbench.blocks.write() as db:
                doc = workbench.blocks.document(db, document_id, actor)
                row, state = workbench._state(db, doc)
                state["receipts"] = (state.get("receipts", []) + [result])[-100:]
                row.state, row.revision = state, row.revision + 1
            return result

        @router.get("/receipts/{receipt_id}")
        def receipt(document_id: str, receipt_id: str, request: Request):
            from app.services.block_service import BlockError
            with workbench.blocks.factory() as db:
                doc = workbench.blocks.document(db, document_id, actors.resolve(request, None))
                from app.models import DocumentWorkspace
                row = db.get(DocumentWorkspace, doc.id)
                result = next((r for r in (row.state if row else {}).get("receipts", []) if r["receipt_id"] == receipt_id), None)
                if not result: raise BlockError("操作记录不存在或已超出最近 100 条保留范围", 404)
                return result

    @router.get("")
    def inspect(document_id: str, request: Request):
        return workbench.inspect(document_id, actors.resolve(request, None))

    @router.post('/policies')
    def policy(document_id: str, body: PolicyUpdate, request: Request):
        from app.services.document_decisions import update_policy
        return update_policy(workbench, document_id, actors.resolve(request, body.actor_id), body.expected_revision, body.payload.model_dump())

    @router.post("")
    def update(document_id: str, body: Update, request: Request):
        return workbench.update(document_id, actors.resolve(request, body.actor_id), body.expected_revision, body.action, body.payload.model_dump())

    @router.post("/collect")
    def collect(document_id: str, body: Collect, request: Request):
        return workbench.collect(document_id, actors.resolve(request, body.actor_id), body.expected_revision)

    @router.post("/plan")
    async def plan(document_id: str, body: Plan, request: Request):
        return await workbench.plan(document_id, actors.resolve(request, body.actor_id), body.kind,
                                    body.instruction, body.target_ids, body.item_ids, body.anchor_id)

    @router.get("/meeting")
    def meeting(document_id: str, left: int, right: int, request: Request):
        return workbench.meeting(document_id, actors.resolve(request, None), left, right)

    return router
