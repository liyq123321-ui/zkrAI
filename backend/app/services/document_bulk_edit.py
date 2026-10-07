"""Find literal PRD replacements without touching code, references or excluded text."""

import re

from app.ai.provider import BulkEditRule, DocumentEditStep
from app.services.block_service import BlockError


_DIVIDER = re.compile(r"^\s*\|?\s*:?-{3,}:?(?:\s*\|\s*:?-{3,}:?)+\s*\|?\s*$")
_PROTECTED = re.compile(r"!?\[[^\]]*\]\([^)]+\)|`[^`]*`|https?://\S+")
_QUOTES = re.compile(r"“[^”]*”|‘[^’]*’|「[^」]*」|『[^』]*』|\"[^\"]*\"")
_SOURCE_TITLE = re.compile(r"引用原文|原始资料|来源摘录|参考原文")


def _table_lines(lines: list[str]) -> set[int]:
    found: set[int] = set()
    index = 0
    while index + 1 < len(lines):
        if "|" not in lines[index] or not _DIVIDER.fullmatch(lines[index + 1].strip()):
            index += 1
            continue
        end = index + 2
        while end < len(lines) and "|" in lines[end] and lines[end].strip():
            end += 1
        found.update(range(index, end))
        index = end
    return found


def scan_bulk_matches(blocks: list[dict], rule: BulkEditRule) -> tuple[list[DocumentEditStep], list[dict], int]:
    if rule.find == rule.replace or "\n" in rule.find or "\r" in rule.find:
        raise BlockError("批量规则需要明确且不同的原文与替换文字", 422)
    steps: list[DocumentEditStep] = []
    excluded: list[dict] = []
    excluded_count = 0
    for block in blocks:
        lines = block["content"].splitlines(keepends=True)
        table_lines = _table_lines(lines)
        block_excluded = block["title"] in rule.excluded_block_titles or (
            rule.exclude_quotes and bool(_SOURCE_TITLE.search(block["title"]))
        )
        offset = 0
        fence = ""
        for line_number, line in enumerate(lines):
            stripped = line.lstrip()
            marker = re.match(r"^(`{3,}|~{3,})", stripped)
            if marker:
                fence = "" if fence == marker.group(1)[0] else marker.group(1)[0] if not fence else fence
            table = line_number in table_lines and not _DIVIDER.fullmatch(line.strip())
            protected = [(match.start(), match.end()) for match in _PROTECTED.finditer(line)]
            if rule.exclude_quotes:
                protected.extend((match.start(), match.end()) for match in _QUOTES.finditer(line))
            from_index = 0
            while (at := line.find(rule.find, from_index)) >= 0:
                from_index = at + len(rule.find)
                reason = ""
                if block_excluded:
                    reason = "排除的章节或引用原文"
                elif fence or marker:
                    reason = "代码或图源"
                elif stripped.startswith(">"):
                    reason = "引用原文"
                elif rule.scope == "tables" and not table:
                    reason = "不在表格中"
                elif rule.scope == "prose" and table:
                    reason = "不在正文中"
                elif any(text and text in line for text in rule.excluded_texts):
                    reason = "指令指定的例外"
                elif any(start <= at < end or start < at + len(rule.find) <= end for start, end in protected):
                    reason = "引用、链接或行内代码"
                elif rule.find.isascii() and (
                    (at > 0 and re.match(r"[A-Za-z0-9_]", line[at - 1]))
                    or (at + len(rule.find) < len(line) and re.match(r"[A-Za-z0-9_]", line[at + len(rule.find)]))
                ):
                    reason = "标识符的一部分"
                context = line.strip()[:240]
                if reason:
                    excluded_count += 1
                    if len(excluded) < 80:
                        excluded.append({"block_id": block["id"], "title": block["title"],
                                         "line": line_number + 1, "context": context, "reason": reason})
                else:
                    steps.append(DocumentEditStep(
                        action="bulk_replace", block_id=block["id"], old_text=rule.find,
                        new_text=rule.replace, match_start=offset + at, match_end=offset + at + len(rule.find),
                    ))
                    if len(steps) > 500:
                        raise BlockError("命中超过 500 处，请缩小批量修改范围", 422)
            offset += len(line)
    order = {block["id"]: index for index, block in enumerate(blocks)}
    steps.sort(key=lambda step: (order[step.block_id], -step.match_start))
    return steps, excluded, excluded_count
