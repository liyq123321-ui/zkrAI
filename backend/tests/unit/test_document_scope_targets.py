"""未指定章节时，命令默认面向整篇全文：先让 Agent 读目录挑章节，再规划。

用户的规则是「目录里点击章节只是查看，不划定范围；只有指令点名、目录勾选才算明确范围，
都没有时默认整篇全文」。这一段就是「默认整篇」的落地：整篇正文进不了单次上下文
（本机那份 236 块 / 25.5 万字），所以先只喂「目录 + 摘要」定位章节。
"""

import contextlib
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.ai.provider import TargetSelection
from app.services.block_service import BlockError
from app.services.document_edit_service import DocumentEditService


def row(block_id, title, parent_id=None, order=1, summary="", content=""):
    return SimpleNamespace(id=block_id, title=title, parent_id=parent_id, order=order,
                           summary=summary, content=content)


OUTLINE = [
    row("block_a", "产品背景", order=1, summary="一句话说明产品定位"),
    row("block_b", "用户问题", order=2, content="AI 在研发过程中的参与不可见、不可控、不可审"),
    row("block_c", "更新记录", order=3, summary="变更历史"),
]


class FakeBlocks:
    def __init__(self, rows):
        self.rows = rows

    def write(self):
        return contextlib.nullcontext()

    def document(self, db, document_id, actor):
        return SimpleNamespace(id=document_id, revision=1, context_revision=1)

    def blocks(self, db, doc):
        return self.rows


class FakeProvider:
    """只实现定位这一跳；返回什么由用例决定。"""

    def __init__(self, block_ids):
        self.block_ids = block_ids
        self.context = None

    async def select_chat_targets(self, context):
        self.context = context
        return {"block_ids": self.block_ids, "reason": "指令提到了该章节正文里的表格"}


@pytest.mark.asyncio
async def test_picks_chapters_from_the_whole_outline():
    provider = FakeProvider(["block_b", "block_ghost", "block_b"])
    service = DocumentEditService(FakeBlocks(OUTLINE), provider)

    ids, reason = await service.select_targets("prd_x", "actor", "把这个表重排一下", knowledge=[])

    assert ids == ["block_b"]  # 去重，并丢掉目录里不存在的 id
    assert reason
    # 目录必须带上「父标题 + 摘要」，否则模型没法按正文里的词定位到章节。
    entry = next(item for item in provider.context["document_map"] if item["id"] == "block_b")
    assert entry["title"] == "用户问题"
    assert "不可见" in entry["excerpt"]
    assert provider.context["instruction"].startswith("把这个表重排一下")


@pytest.mark.asyncio
async def test_never_returns_more_than_the_limit():
    provider = FakeProvider(["block_a", "block_b", "block_c"])
    service = DocumentEditService(FakeBlocks(OUTLINE), provider)

    ids, _reason = await service.select_targets("prd_x", "actor", "都改一遍", knowledge=[], limit=2)

    assert ids == ["block_a", "block_b"]


def test_existing_clients_keep_the_chapter_scope_default():
    """不带 scope 的老前端行为必须完全不变——只有显式声明才走「全文自动定位」。"""
    from app.api.document_workbench import JobPlan

    old = JobPlan(kind="chat", request_id="r", expected_revision=1, instruction="把 B 改短", target_ids=["B"])
    assert old.scope == "chapters"
    assert JobPlan(kind="chat", request_id="r", expected_revision=1, instruction="x", scope="document").scope == "document"


def test_the_scoped_chat_payload_is_part_of_the_job_contract():
    from app.api.document_workbench import JobPlan

    dumped = JobPlan(kind="chat", request_id="r", expected_revision=1, instruction="x", scope="document").model_dump()
    assert dumped["scope"] == "document" and dumped["target_ids"] == []

    # 一次修改的上限是 8 章（`Plan.target_ids` 与 `Command.target_ids` 都硬限 8）。
    assert DocumentEditService.MAX_SCOPE_CHAPTERS == 8
    TargetSelection(block_ids=[f"block_{index}" for index in range(8)])
    with pytest.raises(ValidationError):
        TargetSelection(block_ids=[f"block_{index}" for index in range(9)])


@pytest.mark.asyncio
async def test_asks_for_a_chapter_instead_of_guessing():
    provider = FakeProvider([])
    service = DocumentEditService(FakeBlocks(OUTLINE), provider)

    with pytest.raises(BlockError) as error:
        await service.select_targets("prd_x", "actor", "优化一下", knowledge=[])

    message = str(error.value)
    assert "勾选" in message and "写明章节" in message
    assert error.value.status == 422


@pytest.mark.asyncio
async def test_says_so_when_the_agent_cannot_locate_targets():
    class BlindProvider:
        """不支持按目录定位的 Agent：必须明确拒绝，而不是悄悄改成改某一章。"""

    service = DocumentEditService(FakeBlocks(OUTLINE), BlindProvider())
    with pytest.raises(BlockError) as error:
        await service.select_targets("prd_x", "actor", "把这个表重排一下", knowledge=[])
    assert "勾选" in str(error.value)
    assert error.value.status == 503
