"""Strict transport contracts for the Gitea PRD review API."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.types import ErDiagramSection, normalize_plain_text


def _plain_text_validator(*fields: str, max_length: int):
    """Build one shared plain-text validator for a bounded group of fields."""

    @field_validator(*fields)
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return normalize_plain_text(value, max_length=max_length, field_name="text")

    return normalize_text


class _ActorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str | None = Field(default=None, min_length=1, max_length=255)

    _normalize_actor_id = _plain_text_validator("actor_id", max_length=255)


class CommentCreateRequest(_ActorRequest):
    line: int = Field(gt=0)
    text: str = Field(min_length=1, max_length=4000)
    anchor: str | None = Field(default=None, max_length=4000)

    _normalize_comment_text = _plain_text_validator("text", max_length=4000)

    @field_validator("anchor")
    @classmethod
    def normalize_anchor(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return normalize_plain_text(value, max_length=4000, field_name="anchor")


class CommentReplyRequest(_ActorRequest):
    text: str = Field(min_length=1, max_length=4000)
    author_type: Literal["human"]

    _normalize_comment_text = _plain_text_validator("text", max_length=4000)


class CommentResolveRequest(_ActorRequest):
    resolved: bool


class PublishReviewRequest(_ActorRequest):
    auto_resolve_findings: bool = False


class DiagramRevisionRequest(_ActorRequest):
    base_version: int = Field(gt=0)
    base_commit_sha: str = Field(min_length=1, max_length=128)
    drawio_xml: str = Field(min_length=1, max_length=1_048_576)
    change_summary: str = Field(min_length=1, max_length=8000)

    _normalize_revision_text = _plain_text_validator(
        "base_commit_sha", "change_summary", max_length=8000
    )


class ReviewTaskAccepted(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1, max_length=255)
    base_version: int = Field(gt=0)
    no_change: bool = False


class PrdErDiagramRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    diagram_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=80)
    title: str = Field(min_length=1, max_length=160)
    after_section: ErDiagramSection
    anchor: str = Field(pattern=r"^firstflight-er-[a-z0-9-]+-[0-9a-f]{8}$", max_length=128)
    drawio_xml: str = Field(min_length=1, max_length=1_048_576)


class PrdPrototypeRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ready", "failed"]
    title: str | None = Field(default=None, min_length=1, max_length=160)
    content_url: str | None = Field(default=None, min_length=1, max_length=4096)
    generation_summary: str | None = Field(default=None, min_length=1, max_length=4000)
    error_code: str | None = Field(default=None, min_length=1, max_length=128)
    error_message: str | None = Field(default=None, min_length=1, max_length=500)


class PrdDocumentRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    wi: str = Field(min_length=1, max_length=255)
    version: int = Field(gt=0)
    filename: str = Field(min_length=1, max_length=4096)
    pr_number: int = Field(gt=0)
    commit_sha: str | None = Field(default=None, min_length=1, max_length=128)
    content: str = Field(min_length=1)
    change_summary: str | None = Field(default=None, min_length=1, max_length=8000)
    er_diagrams: list[PrdErDiagramRead] = Field(default_factory=list, max_length=8)
    prototype: PrdPrototypeRead | None = None

    _normalize_identifiers = _plain_text_validator("wi", max_length=255)
    _normalize_filename = _plain_text_validator("filename", max_length=4096)

    @field_validator("commit_sha", "change_summary")
    @classmethod
    def normalize_optional_text(cls, value: str | None, info) -> str | None:
        if value is None:
            return None
        maximum = 128 if info.field_name == "commit_sha" else 8000
        return normalize_plain_text(value, max_length=maximum, field_name=info.field_name)


class PrdCommentReplyRead(BaseModel):
    """A read-only reply nested under its authoritative Gitea comment."""

    model_config = ConfigDict(extra="forbid")

    id: int = Field(gt=0)
    author_type: Literal["human", "agent"]
    body: str = Field(min_length=1, max_length=4000)

    _normalize_body = _plain_text_validator("body", max_length=4000)


class PrdCommentRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int = Field(gt=0)
    path: str = Field(min_length=1, max_length=4096)
    line: int = Field(gt=0)
    author_type: Literal["human", "agent"]
    body: str = Field(min_length=1, max_length=4000)
    resolved: bool
    replies: list[PrdCommentReplyRead]

    _normalize_path = _plain_text_validator("path", max_length=4096)
    _normalize_body = _plain_text_validator("body", max_length=4000)


class PrdCommentableLineRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    line: int = Field(gt=0)
    kind: Literal["context", "addition"]
    text: str


class PrdCommentableLinesRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    wi: str = Field(min_length=1, max_length=255)
    version: int = Field(gt=0)
    filename: str = Field(min_length=1, max_length=4096)
    commit_sha: str = Field(min_length=1, max_length=128)
    lines: list[PrdCommentableLineRead]


class PrdDiffRead(BaseModel):
    """Current PRD patch against its immediately preceding version."""
    model_config = ConfigDict(extra="forbid")

    wi: str = Field(min_length=1, max_length=255)
    version: int = Field(gt=0)
    filename: str = Field(min_length=1, max_length=4096)
    commit_sha: str = Field(min_length=1, max_length=128)
    patch: str


class ReviewTaskRead(BaseModel):
    """Public task progress, intentionally excluding internal failure detail."""

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1, max_length=255)
    wi: str = Field(min_length=1, max_length=255)
    status: Literal["pending", "processing", "done", "error"]
    base_version: int = Field(gt=0)
    new_version: int | None = Field(default=None, gt=0)
    new_commit_sha: str | None = Field(default=None, min_length=1, max_length=128)
    error: str | None = Field(default=None, min_length=1, max_length=500)

    _normalize_ids = _plain_text_validator("task_id", "wi", max_length=255)

    @field_validator("new_commit_sha", "error")
    @classmethod
    def normalize_safe_optional_text(cls, value: str | None, info) -> str | None:
        if value is None:
            return None
        maximum = 128 if info.field_name == "new_commit_sha" else 500
        return normalize_plain_text(value, max_length=maximum, field_name=info.field_name)
