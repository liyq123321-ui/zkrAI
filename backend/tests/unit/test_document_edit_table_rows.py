"""Table row anchors must survive the steps of the plan that rewrites them.

Regression: a chat instruction like "把 P1 与 P5 两条互换位置" is decomposed by the Agent into
several cell writes. The first write renames the row label that it — and every later step — addresses
by, so resolving row_match against the live content made step 1 fail with a misleading "定位不唯一".
"""
import pytest

from app.ai.provider import DocumentEditPlan, DocumentEditStep
from app.services.block_service import BlockError
from app.services.document_edit_service import DocumentEditService

TABLE = "\n".join([
    "| 编号 | 问题描述 | 证据 / 表现 |",
    "| --- | --- | --- |",
    "| P1 | 不可见不可控不可审 | 分散在个人工具中 |",
    "| P2 | 缺人工把关 | 缺乏逐条核查 |",
    "| P3 | 无统一编排 | 散落在聊天会话中 |",
    "| P4 | 经验不可复用 | 没有统一池化 |",
    "| P5 | worker 各自为战 | 能力分散无统一接入 |",
])

BLOCK_ID = "b1"


def blocks(content=TABLE):
    return [{"id": BLOCK_ID, "title": "用户问题", "parent_id": None, "order": 0, "version": 1,
             "content": content, "instruction": "", "dependencies": [], "fragment": None}]


def swap_steps(order="p1_first"):
    """The cell writes the Agent produces for the real instruction, in either order."""
    (match_a, row_a, label_a), (match_b, row_b, label_b) = (("P1", 1, "P5"), ("P5", 5, "P1"))
    if order == "p5_first":
        (match_a, row_a, label_a), (match_b, row_b, label_b) = \
            (match_b, row_b, label_b), (match_a, row_a, label_a)
    return [
        DocumentEditStep(action="table_set", block_id=BLOCK_ID, table_index=0, row=row_a,
                         row_match=match_a, column="编号", value=label_a),
        DocumentEditStep(action="table_set", block_id=BLOCK_ID, table_index=0, row=row_a,
                         row_match=match_a, column="问题描述", value=f"{label_a} 的描述"),
        DocumentEditStep(action="table_set", block_id=BLOCK_ID, table_index=0, row=row_b,
                         row_match=match_b, column="编号", value=label_b),
        DocumentEditStep(action="table_set", block_id=BLOCK_ID, table_index=0, row=row_b,
                         row_match=match_b, column="问题描述", value=f"{label_b} 的描述"),
    ]


def rows(content):
    """Data rows of the first table: everything after the --- divider."""
    lines = [line for line in content.splitlines() if line.startswith("|")]
    divider = next(index for index, line in enumerate(lines) if set(line) <= set("| -:"))
    return lines[divider + 1:]


def labels(content):
    return [line.split("|")[1].strip() for line in rows(content)]


def simulate(steps, content=TABLE):
    service = DocumentEditService(None, None)
    return service._simulate(blocks(content), DocumentEditPlan(summary="互换两条", steps=steps))[BLOCK_ID]["content"]


@pytest.mark.parametrize("order", ["p1_first", "p5_first"])
def test_swapping_two_rows_by_their_labels_applies_in_either_order(order):
    assert labels(simulate(swap_steps(order))) == ["P5", "P2", "P3", "P4", "P1"]


def test_the_swap_moves_both_entries_and_leaves_the_other_rows_alone():
    after = simulate(swap_steps())
    assert rows(after) == [
        "| P5 | P5 的描述 | 分散在个人工具中 |",
        "| P2 | 缺人工把关 | 缺乏逐条核查 |",
        "| P3 | 无统一编排 | 散落在聊天会话中 |",
        "| P4 | 经验不可复用 | 没有统一池化 |",
        "| P1 | P1 的描述 | 能力分散无统一接入 |",
    ]


def test_missing_row_says_what_is_missing_instead_of_claiming_ambiguity():
    steps = [DocumentEditStep(action="table_set", block_id=BLOCK_ID, table_index=0, row=1,
                              row_match="P9", column="编号", value="P0")]
    with pytest.raises(BlockError) as error:
        simulate(steps)
    message = str(error.value)
    assert "未找到匹配「P9」" in message
    assert "P1" in message and "P5" in message
    assert "定位不唯一" not in message


def test_repeated_label_reports_the_count_and_the_rows_instead_of_a_dead_end():
    content = TABLE.replace("| P5 | worker 各自为战 |", "| P1 | worker 各自为战 |")
    steps = [DocumentEditStep(action="table_set", block_id=BLOCK_ID, table_index=0, row=1,
                              row_match="P1", column="问题描述", value="X")]
    with pytest.raises(BlockError) as error:
        simulate(steps, content)
    message = str(error.value)
    assert "「P1」匹配到 2 行" in message
    assert "第 1 行" in message and "第 5 行" in message


def test_a_row_added_by_an_earlier_step_is_still_addressable():
    """The snapshot must not shadow a row that only exists because a previous step created it."""
    steps = [
        DocumentEditStep(action="table_add_row", block_id=BLOCK_ID, table_index=0, row=5, position="after",
                         values=["P6", "新增的痛点", "新增的证据"]),
        DocumentEditStep(action="table_set", block_id=BLOCK_ID, table_index=0, row=6,
                         row_match="新增的痛点", column="问题描述", value="改写后的痛点"),
    ]
    after = simulate(steps)
    assert "| P6 | 改写后的痛点 | 新增的证据 |" in after
    assert labels(after) == ["P1", "P2", "P3", "P4", "P5", "P6"]


def test_content_before_the_table_is_left_alone():
    content = "覆盖说明：本矩阵覆盖全部 11 个 Must 功能域。\n\n" + TABLE
    after = simulate(swap_steps(), content)
    assert after.startswith("覆盖说明：本矩阵覆盖全部 11 个 Must 功能域。")
    assert labels(after) == ["P5", "P2", "P3", "P4", "P1"]


# --- 第二轮：锚点常常是「整行拼接」形态 -------------------------------------------

# 取自 prdjob_100075164c5448398929d0936ae7c214 的真实锚点：与表格里那一行**逐字相同**，
# 只是把三个单元格用 markdown 的「 | 」拼了起来 —— 因为模型看到的就是这一行。
# `_row_hits` 收到的 `row` 是单元格列表，所以这种锚点既不等于任一单元格、
# 也不可能是任一单元格的子串，必然 0 匹配，把一次完全正确的方案判死。
WHOLE_ROW = ("协同 | 消息中心、待办、评审组件框架（独立评判 G1、检查条目、确认流程，含双开关） "
             "| FR-4.1–4.5、FR-8.1–8.7")

BASE_TABLE = "\n".join([
    "| 底座模块 | 通用机制职责 | 对应 FR |",
    "| --- | --- | --- |",
    "| 身份与空间 | 人/Agent 同一身份平面、组织/岗位/角色 RBAC、个体/协作/企业三类空间权限隔离 | FR-1.1、FR-1.6、FR-6.2 |",
    "| 编排引擎 | 图定义/执行驱动、Work Item 状态机、子任务动态规划与运行子图 | FR-2.1–2.6、FR-3.1–3.5 |",
    "| 协同 | 消息中心、待办、评审组件框架（独立评判 G1、检查条目、确认流程，含双开关） | FR-4.1–4.5、FR-8.1–8.7 |",
    "| 插件框架 | 知识形成范式、应用包加载器、SDK、包安全校验 | FR-1.18、FR-1.20 |",
])


def base_labels(content=None):
    return labels(content or BASE_TABLE)


def test_a_whole_row_anchor_finds_the_row_it_was_copied_from():
    steps = [DocumentEditStep(action="table_set", block_id=BLOCK_ID, table_index=0, row=3,
                              row_match=WHOLE_ROW, column="底座模块", value="协作")]
    after = simulate(steps, BASE_TABLE)
    assert "| 协作 | 消息中心、待办" in after
    assert base_labels(after) == ["身份与空间", "编排引擎", "协作", "插件框架"]


def test_a_whole_row_anchor_tolerates_losing_a_later_column():
    """抄整行时最容易记错的是靠后的长文本；首列可靠时就该落到那一行。"""
    broken = "协同 | 消息中心、待办、评审组件框架（独立评判 G1） | FR-9.9"
    steps = [DocumentEditStep(action="table_set", block_id=BLOCK_ID, table_index=0, row=3,
                              row_match=broken, column="底座模块", value="协作")]
    assert base_labels(simulate(steps, BASE_TABLE)) == ["身份与空间", "编排引擎", "协作", "插件框架"]


def test_a_whole_row_anchor_that_fits_two_rows_reports_the_count():
    content = BASE_TABLE.replace("| 插件框架 |", "| 协同 |")
    steps = [DocumentEditStep(action="table_set", block_id=BLOCK_ID, table_index=0, row=3,
                              row_match=WHOLE_ROW, column="底座模块", value="协作")]
    with pytest.raises(BlockError) as error:
        simulate(steps, content)
    assert "匹配到 2 行" in str(error.value)


def test_an_unrecognised_whole_row_anchor_reports_what_exists():
    steps = [DocumentEditStep(action="table_set", block_id=BLOCK_ID, table_index=0, row=1,
                              row_match="不存在 | 的 | 行", column="底座模块", value="X")]
    with pytest.raises(BlockError) as error:
        simulate(steps, BASE_TABLE)
    message = str(error.value)
    assert "未找到匹配" in message
    assert "身份与空间" in message and "协同" in message


def test_moving_a_row_to_the_bottom_and_renaming_it():
    """用户原始指令：把「协同」整行放到最下面，并把名字改为「协作」。"""
    renamed = ["协作", "消息中心、待办、评审组件框架（独立评判 G1、检查条目、确认流程，含双开关）",
               "FR-4.1–4.5、FR-8.1–8.7"]
    steps = [
        DocumentEditStep(action="table_delete_row", block_id=BLOCK_ID, table_index=0, row=3,
                         row_match=WHOLE_ROW),
        DocumentEditStep(action="table_add_row", block_id=BLOCK_ID, table_index=0, row=3,
                         position="after", values=renamed),
    ]
    after = simulate(steps, BASE_TABLE)
    assert base_labels(after) == ["身份与空间", "编排引擎", "插件框架", "协作"]
    assert "| 协作 | 消息中心、待办" in after
    assert "| 身份与空间 |" in after and "| 插件框架 |" in after
