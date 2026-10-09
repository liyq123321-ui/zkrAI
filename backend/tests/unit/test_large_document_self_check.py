"""目录自检不该被「模型输出上限」卡死。

`BlockPlan.blocks` 的 `max_length = 100` 是给「生成 / 解析大纲」的输出契约用的（防模型一次吐几百章），
但同一形状也被拿来校验**既有文档**的目录树 —— 而真实导入的 PRD 可以远超 100 块。

生产现场：一份 236 块的 PRD，任何一次聊天修改都在 `_simulate` 收尾自检时被判死，
`prd_document_jobs` 里留下 `1 validation error for BlockPlan blocks List should have at most 100
items after validation, not 236`，而模型其实已经把方案正常生成出来了。

自检的是我们自己的快照，不是模型输出，所以改用不设条数上限的 `BlockTree`。
"""
import pytest
from pydantic import ValidationError

from app.ai.provider import BlockPlan, BlockTree, DocumentEditPlan, DocumentEditStep, PlanNode
from app.services.block_service import BlockError
from app.services.document_edit_service import DocumentEditService

TABLE = "\n".join([
    "| 区域 | 内容 | 交互 / 说明 |",
    "| --- | --- | --- |",
    "| 租户信息区 | Logo 与名称 | 仅展示当前租户 |",
])

DOCUMENT_BLOCKS = 236


def source(count=DOCUMENT_BLOCKS):
    items = [{"id": "b0", "title": "第一章", "parent_id": None, "order": 0, "version": 1,
              "content": TABLE, "instruction": "", "dependencies": [], "fragment": None}]
    for index in range(1, count):
        items.append({"id": f"b{index}", "title": f"第 {index + 1} 章", "parent_id": None,
                      "order": index, "version": 1, "content": f"正文 {index}。",
                      "instruction": "", "dependencies": [], "fragment": None})
    return items


def rename_header_plan():
    return DocumentEditPlan(summary="改表头", steps=[
        DocumentEditStep(action="table_set", block_id="b0", table_index=0, row=0,
                         column="区域", value="区域（新）")])


def test_a_document_far_larger_than_the_outline_cap_can_still_be_edited():
    changed = DocumentEditService(None, None)._simulate(source(), rename_header_plan())
    assert len(changed) == DOCUMENT_BLOCKS
    assert "| 区域（新） | 内容 | 交互 / 说明 |" in changed["b0"]["content"]


def test_the_same_document_is_still_too_big_for_the_block_plan_contract():
    """锁住输出契约本身 —— 放宽自检不等于放宽模型允许吐多少章。"""
    nodes = [PlanNode(key=f"section_{index}", title=f"第 {index} 章", parent_key=None,
                      instruction="", dependencies=[]) for index in range(101)]
    with pytest.raises(ValidationError) as error:
        BlockPlan(blocks=nodes)
    assert "at most 100 items" in str(error.value)
    assert len(BlockTree(blocks=nodes).blocks) == 101


def test_the_self_check_still_catches_a_broken_tree():
    """放开条数上限不等于关掉校验：同一份大文档，目录坏掉时仍必须报错。"""
    items = source(150)
    items[3]["dependencies"] = ["block_不存在的章节"]
    with pytest.raises(BlockError) as error:
        DocumentEditService(None, None)._simulate(items, rename_header_plan())
    assert "不存在的引用" in str(error.value)
