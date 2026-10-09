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


class BlockTree(BlockPlan):
    """本地目录自检用的模型 —— 不受「模型输出」的条数上限约束。

    `BlockPlan.max_length = 100` 是给「生成 / 解析大纲」的输出契约用的（防模型一次吐几百章）。
    但同一形状也被拿来校验**既有文档**的目录树（`_simulate` 收尾、`save()` 改依赖、恢复历史版本），
    而真实导入的 PRD 可以有几百个块（本机那份 236 块）——
    于是**任何一次聊天修改都会在收尾自检时被判死**：
    `1 validation error for BlockPlan blocks List should have at most 100 items, not 236`。
    自检的是我们自己的快照，不是模型输出，所以这里不设条数上限。
    """

    blocks: list[PlanNode] = Field(min_length=1)


class TargetSelection(BaseModel):
    """未指定章节时，先单独让 Agent 在整篇目录里挑出要改的章节。

    为什么不和 `plan_chat_edit` 合成一次调用：模型必须拿到**被改章节的正文**才能给出
    精确的修改步骤，而整篇 PRD 的正文远超单次上下文（本机那份 236 块 / 25.5 万字）。
    所以分两步——先用「目录 + 摘要」定位章节（这次调用不带正文，很便宜），
    再只把这些章节的正文交给 `plan_chat_edit`。
    """

    model_config = ConfigDict(extra="forbid")
    block_ids: list[str] = Field(default_factory=list, max_length=8)
    reason: str = Field(default="", max_length=2000)


class BlockResult(BaseModel):
    decision_conflicts: list[str] = Field(default_factory=list, max_length=30)
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
    decision_conflicts: list[str] = Field(default_factory=list, max_length=30)
    model_config = ConfigDict(extra="forbid")
    document_id: str
    base_revision: int
    summary: str = Field(min_length=1, max_length=3000)
    actions: list[DocumentRevisionAction] = Field(max_length=100)


class DocumentRevision(BaseModel):
    decision_conflicts: list[str] = Field(default_factory=list, max_length=30)
    model_config = ConfigDict(extra="forbid")
    document_id: str
    base_revision: int
    summary: str = Field(min_length=1, max_length=6000)
    blocks: list[DocumentRevisionBlock] = Field(max_length=100)


class DocumentEditStep(BaseModel):
    """A small, exact operation proposed by the chat Agent."""

    model_config = ConfigDict(extra="forbid")
    action: Literal["replace_text", "move_text", "table_set", "table_add_row", "table_delete_row",
                    "table_add_column", "table_delete_column", "table_sort", "move_block",
                    "mermaid_insert", "mermaid_replace", "er_upsert", "bulk_replace",
                    "image_insert", "image_replace"]
    block_id: str
    old_text: str = ""
    new_text: str = ""
    target_text: str = ""
    table_index: int = Field(default=0, ge=0)
    row: int = Field(default=0, ge=0)
    row_match: str = ""
    column: str = ""
    value: str = ""
    values: list[str] = Field(default_factory=list)
    descending: bool = False
    target_block_id: str = ""
    position: Literal["before", "after", "child_of"] = "after"
    diagram_index: int = Field(default=0, ge=0)
    diagram_source: str = Field(default="", max_length=30000)
    diagram_id: str = ""
    diagram_title: str = ""
    after_section: str = ""
    drawio_xml: str = Field(default="", max_length=1_048_576)
    match_start: int = Field(default=0, ge=0)
    match_end: int = Field(default=0, ge=0)
    image_index: int = Field(default=0, ge=0)
    image_alt: str = Field(default="", max_length=240)
    image_prompt: str = Field(default="", max_length=4000)
    image_svg: str = Field(default="", max_length=100_000)
    image_mime: str = ""
    image_base64: str = Field(default="", max_length=16_800_000)


class DocumentEditPlan(BaseModel):
    decision_conflicts: list[str] = Field(default_factory=list, max_length=30)
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(min_length=1, max_length=1000)
    steps: list[DocumentEditStep] = Field(min_length=1, max_length=500)
    impact_notes: list[str] = Field(default_factory=list, max_length=12)


class BulkEditRule(BaseModel):
    decision_conflicts: list[str] = Field(default_factory=list, max_length=30)
    """A natural-language rule that the server applies only to eligible text spans."""

    model_config = ConfigDict(extra="forbid")
    find: str = Field(min_length=1, max_length=200)
    replace: str = Field(max_length=200)
    scope: Literal["tables", "prose", "all"] = "all"
    exclude_quotes: bool = True
    excluded_block_titles: list[str] = Field(default_factory=list, max_length=30)
    excluded_texts: list[str] = Field(default_factory=list, max_length=30)
    summary: str = Field(min_length=1, max_length=500)


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
