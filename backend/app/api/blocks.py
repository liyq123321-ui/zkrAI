"""Block mutations and background AI endpoints."""
from typing import Literal
from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field


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


class Comment(Input):
    text: str = Field(min_length=1, max_length=12000)
    targets: list[Target] = Field(min_length=1, max_length=100)


class Resolve(Input):
    resolved: bool


class Retry(Input):
    request_id: str = Field(min_length=1, max_length=100)


def build_router(service, ai, actors):
    router = APIRouter(prefix="/prd-documents/{document_id}", tags=["prd-blocks"])

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
