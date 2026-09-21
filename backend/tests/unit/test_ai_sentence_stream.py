from app.services.ai_service import line_range_change_is_local, sentence_chunks


def test_sentence_chunks_preserve_markdown_and_only_split_complete_units():
    content = "# 标题\n第一句。第二句！\n- 列表项\n最后一段"

    chunks = sentence_chunks(content)

    assert chunks == ["# 标题\n", "第一句。", "第二句！", "\n", "- 列表项\n", "最后一段"]
    assert "".join(chunks) == content


def test_sentence_chunks_keep_closing_quote_with_sentence():
    content = "他说：“可以。”然后继续。"

    assert sentence_chunks(content) == ["他说：“可以。”", "然后继续。"]


def test_line_range_accepts_neighbouring_changes_but_rejects_distant_changes():
    original_lines = [f"第 {index} 行" for index in range(1, 13)]
    original = "\r\n".join(original_lines)
    nearby_lines = original_lines.copy()
    nearby_lines[2] = "第 3 行（为保持段落连贯而同步调整）"
    distant_lines = original_lines.copy()
    distant_lines[11] = "第 12 行（远离选区的改动）"

    assert line_range_change_is_local(original, "\n".join(nearby_lines), 6, 6)
    assert not line_range_change_is_local(original, "\n".join(distant_lines), 6, 6)


def test_line_range_rejects_whole_block_rewrite_for_narrow_selection():
    original = "开头\n选中的内容\n结尾"

    assert not line_range_change_is_local(original, "完全不同的新正文", 2, 2)


def test_line_range_ignores_line_ending_only_changes():
    assert line_range_change_is_local("第一行\r\n第二行\r\n", "第一行\n第二行\n", 1, 1)
