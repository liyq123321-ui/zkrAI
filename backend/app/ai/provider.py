"""Provider contracts deliberately contain one Block, never a whole PRD."""
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


class AIProvider(ABC):
    @abstractmethod
    async def plan_outline(self, context: dict) -> BlockPlan: ...
    @abstractmethod
    async def generate_block(self, context: dict) -> BlockResult: ...
    @abstractmethod
    async def revise_block(self, context: dict) -> BlockResult: ...
    @abstractmethod
    async def review_block(self, context: dict) -> BlockResult: ...
