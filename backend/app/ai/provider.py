"""Block editing contracts and a separate read-only whole-document review."""
from abc import ABC, abstractmethod
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, create_model
from app.domain.types import ProjectSpecPayload

# A fragment uses the same item types as the downstream spec but may omit sections.
SpecFragment = create_model(
    "SpecFragment", __config__=ConfigDict(extra="forbid"),
    **{name: (field.annotation, Field(default_factory=list))
       for name, field in ProjectSpecPayload.model_fields.items()},
)


class PlanNode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=200)
    parent_key: str | None
    instruction: str = Field(max_length=6000)
    dependencies: list[str] = Field(max_length=100)


class BlockPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    blocks: list[PlanNode] = Field(min_length=1, max_length=100)


class BlockResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    block_id: str
    base_version: int
    content: str = Field(max_length=100000)
    summary: str = Field(max_length=2000)
    affected_blocks: list[str] = Field(max_length=100)
    spec_fragment: SpecFragment
    review_notes: list[str] = Field(default_factory=list, max_length=50)


class DocumentReviewEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    block_id: str
    quote: str = Field(min_length=1, max_length=2000)


class DocumentReviewIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    severity: Literal["blocker", "warning", "info"]
    block_ids: list[str] = Field(max_length=100)
    description: str = Field(min_length=1, max_length=4000)
    suggestion: str = Field(min_length=1, max_length=4000)
    alternative_suggestion: str | None = Field(default=None, min_length=1, max_length=4000)
    evidence: list[DocumentReviewEvidence] = Field(default_factory=list, max_length=20)


class DocumentReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: str
    base_revision: int
    summary: str = Field(min_length=1, max_length=6000)
    issues: list[DocumentReviewIssue] = Field(max_length=100)


class DocumentRevisionBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    block_id: str
    base_version: int
    content: str = Field(max_length=100000)
    summary: str = Field(max_length=2000)
    spec_fragment: SpecFragment


class DocumentRevisionAction(BaseModel):
    """A bounded, review-backed change that a later Skill pass may execute."""

    model_config = ConfigDict(extra="forbid")
    block_id: str
    base_version: int
    issue_numbers: list[int] = Field(min_length=1, max_length=100)
    instruction: str = Field(min_length=1, max_length=2000)
    preserve: list[str] = Field(default_factory=list, max_length=20)
    requirement_ids: list[str] = Field(default_factory=list, max_length=100)


class DocumentRevisionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: str
    base_revision: int
    summary: str = Field(min_length=1, max_length=3000)
    actions: list[DocumentRevisionAction] = Field(max_length=100)


class DocumentRevision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: str
    base_revision: int
    summary: str = Field(min_length=1, max_length=6000)
    blocks: list[DocumentRevisionBlock] = Field(max_length=100)


class AIProvider(ABC):
    @abstractmethod
    async def plan_outline(self, context: dict) -> BlockPlan: ...
    @abstractmethod
    async def generate_block(self, context: dict) -> BlockResult: ...
    @abstractmethod
    async def revise_block(self, context: dict) -> BlockResult: ...
    @abstractmethod
    async def review_block(self, context: dict) -> BlockResult: ...

    @abstractmethod
    async def review_document(self, context: dict) -> DocumentReview: ...

    @abstractmethod
    async def revise_document(self, context: dict) -> DocumentRevision: ...
