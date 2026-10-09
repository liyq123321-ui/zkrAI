"""The imported-PRD layout rules must never lose or rewrite source text."""

import difflib
import re

from app.services.imported_prd_layout import (
    assess_imported_hierarchy,
    heading_candidates,
    heading_digest,
    heading_remainder,
    resolve_heading_map,
    split_by_heading_map,
    split_imported_body,
    split_imported_markdown,
)

DOCUMENT = """平台总览

这是第一段说明文字。

核心能力

能力一段落。

能力二段落。

系统架构

架构说明。"""


def _squeeze(text):
    return re.sub(r"\s+", "", text)


def _dropped(source, blocks):
    """Source characters that the layout failed to carry into the Blocks."""

    rendered = "".join(f"{title}\n\n{body}" for _, title, body in blocks)
    matcher = difflib.SequenceMatcher(None, _squeeze(source), _squeeze(rendered), autojunk=False)
    return sum(i2 - i1 for tag, i1, i2, j1, j2 in matcher.get_opcodes() if tag in ("delete", "replace"))


def test_heading_map_keeps_every_character():
    headings = [
        {"index": 0, "level": 1, "title": ""},
        {"index": 2, "level": 2, "title": ""},
        {"index": 5, "level": 1, "title": ""},
    ]
    blocks = split_by_heading_map(DOCUMENT, headings)
    assert _dropped(DOCUMENT, blocks) == 0
    assert [title for _, title, _ in blocks] == ["平台总览", "核心能力", "系统架构"]


def test_heading_map_keeps_the_lines_below_a_multi_line_heading():
    """Only the first line becomes a title; the rest is still source text."""

    markdown = "标题行\n副标题行\n\n正文。"
    headings = [{"index": 0, "level": 1, "title": ""}]
    blocks = split_by_heading_map(markdown, headings)
    assert _dropped(markdown, blocks) == 0
    assert blocks[0][1] == "标题行"
    assert "副标题行" in blocks[0][2]


def test_heading_remainder_returns_everything_after_the_title_line():
    assert heading_remainder("标题\n正文甲\n正文乙") == "正文甲\n正文乙"
    assert heading_remainder("标题") == ""
    assert heading_remainder("\n\n标题") == ""


def test_heading_map_keeps_text_before_the_first_heading():
    headings = [{"index": 2, "level": 1, "title": ""}]
    blocks = split_by_heading_map(DOCUMENT, headings)
    assert _dropped(DOCUMENT, blocks) == 0
    assert [title for _, title, _ in blocks] == ["平台总览", "核心能力"]


def test_heading_map_falls_back_to_the_plain_import_when_nothing_is_chosen():
    assert split_by_heading_map(DOCUMENT, []) == split_imported_markdown(DOCUMENT)


def test_own_headings_become_titles_and_bodies_keep_the_prose():
    markdown = "## 系统架构\n\n架构说明。\n\n### 状态机\n\n状态说明。"
    blocks = split_imported_markdown(markdown)
    assert [(depth, title) for depth, title, _ in blocks] == [(2, "系统架构"), (3, "状态机")]
    assert blocks[0][2] == "架构说明。"
    assert blocks[1][2] == "状态说明。"
    # Only the "## " markers are dropped; every content character survives.
    assert _dropped(markdown.replace("### ", "").replace("## ", ""), blocks) == 0


def test_a_chapter_heading_without_its_own_body_still_becomes_a_block():
    """Chapter titles are usually followed straight by their first section.

    Dropping the empty chapter would re-parent every section below it to the
    previous chapter and silently flatten the file's own outline.
    """

    markdown = "# 背景与用户问题\n\n## 背景\n\n时代背景说明。\n\n## 用户问题\n\n用户问题说明。\n\n# 产品目标\n\n## 产品目标\n\n目标说明。"
    blocks = split_imported_markdown(markdown)

    assert [(depth, title) for depth, title, _ in blocks] == [
        (1, "背景与用户问题"), (2, "背景"), (2, "用户问题"), (1, "产品目标"), (2, "产品目标"),
    ]
    assert blocks[0][2] == "" and blocks[3][2] == ""
    assert _dropped(markdown.replace("## ", "").replace("# ", ""), blocks) == 0


def test_an_empty_tail_section_is_dropped_but_a_heading_only_one_is_not():
    markdown = "# 只有标题\n\n# 有正文\n\n正文段落。"
    blocks = split_imported_markdown(markdown)
    assert [(depth, title) for depth, title, _ in blocks] == [(1, "只有标题"), (1, "有正文")]


def test_body_is_grouped_up_to_the_limit():
    body = "\n\n".join(f"第{i}段" + "字" * 40 for i in range(6))
    parts = split_imported_body(body, limit=100)
    assert len(parts) > 1
    assert all(len(part) <= 100 + 45 for part in parts)


def test_a_table_is_never_cut_in_half():
    rows = ["| 字段 | 说明 |", "| --- | --- |"] + [f"| 字段{i} | 说明{i} |" for i in range(20)]
    parts = split_imported_body("\n\n".join(rows), limit=100)
    assert len(parts) == 1


def test_marker_only_and_pipe_only_lines_never_become_titles():
    assert split_imported_markdown("---\n\n正文内容。")[0][1] == "正文内容。"
    assert split_imported_markdown("| a | b |\n\n正文内容。")[0][1] == "正文内容。"


def test_assessment_separates_clear_from_unclear_hierarchy():
    clear = "".join(f"# 章节{i}\n\n正文。\n\n" for i in range(4))
    unclear = "。".join(["很长的正文" * 40] * 4) + "。"
    assert assess_imported_hierarchy(clear)["clear"] is True
    assert assess_imported_hierarchy(unclear)["clear"] is False
    assert assess_imported_hierarchy(unclear)["candidates"] >= 0


def test_digest_numbers_resolve_back_to_paragraph_indices():
    candidates = heading_candidates(DOCUMENT)
    assert [item["text"] for item in candidates] == ["平台总览", "核心能力", "系统架构"]
    digest = heading_digest(DOCUMENT)
    # Only the number and the line travel in the prompt.
    assert set(digest[0]) == {"n", "text"}

    plan = resolve_heading_map(DOCUMENT, _Choices([{"n": 3, "level": 1, "title": ""}]))
    assert plan == [{"index": candidates[2]["index"], "level": 1, "title": ""}]


def test_digest_numbers_outside_the_candidate_list_are_ignored():
    assert resolve_heading_map(DOCUMENT, [{"n": 99, "level": 1, "title": ""}]) == []
    assert resolve_heading_map(DOCUMENT, [{"n": 1, "level": 9, "title": ""}]) == []


class _Choices:
    """Stand-in for the Agent's Pydantic heading choices."""

    def __init__(self, items):
        self._items = items

    def __iter__(self):
        return iter(self._items)
