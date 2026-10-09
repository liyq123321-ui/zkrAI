"""Turn a PRD imported during intake into document Blocks, verbatim.

Nothing here rewrites wording. The imported file is adopted as-is; these rules
only decide *where* one Block ends and the next begins, because a single Block
holding the whole 8 MB intake limit would freeze the reader.

Two layouts are supported:

* the file's own Markdown headings, when it has usable ones;
* a heading map chosen by the Agent (``split_by_heading_map``) for files that
  arrive without any heading structure at all — a DOCX saved without Heading
  styles, a PDF, pasted tables. The Agent only picks which existing lines are
  section titles and at what level; the body text is never touched.
"""

from __future__ import annotations

import re

# A Block is the unit the reader scrolls, edits and hands to an Agent, so it
# stays small enough to work with. Only paragraph boundaries are used.
IMPORTED_BLOCK_MAX_CHARS = 6000
# A "heading" longer than this, or ending in a table pipe, is really a pasted
# paragraph or table row and would carve the document at random.
IMPORTED_HEADING_MAX_CHARS = 80
IMPORTED_TITLE_MAX_CHARS = 60
# A file whose own headings already divide it this finely needs no help.
IMPORTED_CLEAR_MIN_HEADINGS = 3
IMPORTED_CLEAR_CHARS_PER_HEADING = 15000
# Lines the Agent may consider as section titles.
IMPORTED_CANDIDATE_MAX_CHARS = 45

_MARKDOWN_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_MARKDOWN_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
_TRAILING_SENTENCE = re.compile(r"[。！？；：，,.;:!?]$")
_LEADING_LIST = re.compile(r"^(?:\d+[.、)]|[-*+]\s|[（(]\d+[)）])")
_TITLE_MARKUP = re.compile(r"^[#>*\-\s]+")
# Emphasis the extractor keeps for the reader is noise in a Block title.
_EMPHASIS = re.compile(r"(\*\*|\*|`)(.+?)\1", re.S)


def strip_emphasis(text):
    """Drop ``**bold**`` / ``*italic*`` / ``code`` markers from a title."""

    previous = None
    while previous != text:
        previous = text
        text = _EMPHASIS.sub(r"\2", text)
    return text


def scan_imported_sections(markdown):
    """Split ``markdown`` at its own heading lines.

    Returns ``(sections, heading_count)`` where each section is
    ``(depth, title_or_None, body)``; ``title`` is ``None`` for text that
    appears before the first heading.
    """

    sections, lines, title, depth = [], [], None, 1
    headings, fence, saw_heading = 0, None, False
    for line in markdown.splitlines(keepends=True):
        marker = _MARKDOWN_FENCE.match(line)
        if marker:
            token = marker.group(1)[0]
            fence = None if fence == token else (token if fence is None else fence)
        heading = _MARKDOWN_HEADING.match(line) if not fence else None
        text = heading.group(2) if heading else ""
        if heading and len(text) <= IMPORTED_HEADING_MAX_CHARS and not text.endswith("|"):
            if lines or saw_heading:
                sections.append((depth, title, "".join(lines)))
            title, lines, depth = text, [], len(heading.group(1))
            saw_heading = True
            headings += 1
        else:
            lines.append(line)
    if lines or saw_heading:
        sections.append((depth, title, "".join(lines)))
    return sections, headings


def split_imported_markdown(markdown):
    """Blocks for a file whose own headings are good enough to trust."""

    sections, _ = scan_imported_sections(markdown)
    blocks, position = [], 0
    for depth, heading, body in sections:
        parts = [part.strip("\n") for part in split_imported_body(body)]
        parts = [part for part in parts if part.strip()]
        if not parts:
            # A chapter heading directly followed by its first section carries
            # no body of its own. It still has to become a Block: dropping it
            # would re-parent every section below to the previous chapter and
            # silently flatten the file's own outline.
            if heading:
                position += 1
                blocks.append((depth, heading, ""))
            continue
        for index, text in enumerate(parts):
            position += 1
            if heading:
                name = heading if index == 0 else f"{heading}（{index + 1}）"
                blocks.append((depth, name, text))
            else:
                blocks.append((1, first_line_title(text, position), text))
    return blocks


def split_by_heading_map(markdown, headings):
    """Blocks for a file laid out by an Agent-chosen heading map.

    ``headings`` entries are ``{"index": paragraph number, "level": 1-3,
    "title": optional replacement}``. Each chosen paragraph becomes a Block
    title; the paragraphs that follow it become that Block's body. Nothing is
    rewritten except an explicit ``title`` override.
    """

    paragraphs = markdown.split("\n\n")
    chosen = {}
    for item in headings or ():
        if not isinstance(item, dict):
            continue
        try:
            index, level = int(item.get("index")), int(item.get("level"))
        except (TypeError, ValueError):
            continue
        if 0 <= index < len(paragraphs) and 1 <= level <= 3:
            chosen.setdefault(index, (level, str(item.get("title") or "").strip()))
    if not chosen:
        return split_imported_markdown(markdown)

    boundaries = sorted(chosen)
    blocks, position = [], 0
    if boundaries[0] > 0:
        preamble = "\n\n".join(paragraphs[: boundaries[0]]).strip("\n")
        if preamble.strip():
            position += 1
            blocks.append((1, first_line_title(preamble, position), preamble))
    for order, index in enumerate(boundaries):
        end = boundaries[order + 1] if order + 1 < len(boundaries) else len(paragraphs)
        level, override = chosen[index]
        paragraph = paragraphs[index]
        heading = (override or plain_title(paragraph) or f"第 {order + 1} 节")[:IMPORTED_TITLE_MAX_CHARS]
        # Only the paragraph's first line became the title; anything below it is
        # still source text and must stay in the body.
        tail = heading_remainder(paragraph)
        body = "\n\n".join(([tail] if tail else []) + paragraphs[index + 1: end]).strip("\n")
        parts = [part.strip("\n") for part in split_imported_body(body) if part.strip()]
        if not parts:
            # A heading with no body still earns a Block, so the structure the
            # Agent proposed stays visible instead of silently collapsing.
            position += 1
            blocks.append((level, heading, ""))
            continue
        for part_index, text in enumerate(parts):
            position += 1
            name = heading if part_index == 0 else f"{heading}（{part_index + 1}）"
            blocks.append((level, name, text))
    return blocks


def split_imported_body(body, limit=IMPORTED_BLOCK_MAX_CHARS):
    """Group the paragraphs of ``body`` into parts of at most ``limit`` chars.

    One Markdown table is never cut in half: consecutive table rows keep
    accumulating past the limit rather than starting a new Block mid-table.
    """

    parts, current, length, table_run = [], [], 0, False
    for paragraph in body.split("\n\n"):
        text = paragraph.strip("\n")
        if not text.strip():
            continue
        table = text.lstrip().startswith("|")
        if current and length + len(text) + 2 > limit and not (table and table_run):
            parts.append("\n\n".join(current))
            current, length = [], 0
        table_run = table
        current.append(text)
        length += len(text) + 2
    if current:
        parts.append("\n\n".join(current))
    return parts


def assess_imported_hierarchy(markdown):
    """Decide whether the file can be laid out as-is or needs the Agent.

    ``candidates`` counts paragraphs that could plausibly be section titles, so
    a caller can tell "no structure at all" from "structure exists but was
    stored as plain text".
    """

    _, headings = scan_imported_sections(markdown)
    characters = len(markdown.strip())
    candidates = heading_candidates(markdown)
    return {
        "clear": headings >= IMPORTED_CLEAR_MIN_HEADINGS
        and characters / max(1, headings) <= IMPORTED_CLEAR_CHARS_PER_HEADING,
        "headings": headings,
        "characters": characters,
        "candidates": len(candidates),
    }


def heading_candidates(markdown):
    """Short standalone paragraphs, in document order, as ``{index, text}``.

    ``index`` is the paragraph number in ``markdown.split("\\n\\n")``, which is
    what :func:`split_by_heading_map` expects back from the Agent.
    """

    found = []
    for index, paragraph in enumerate(markdown.split("\n\n")):
        text = paragraph.strip()
        if looks_like_title(text):
            found.append({"index": index, "text": text})
    return found


def heading_digest(markdown):
    """The numbered candidate list handed to the Agent.

    Only the number and the line travel in the prompt; paragraph offsets stay
    server-side so the Agent cannot point at text it has not seen.
    """

    return [
        {"n": position, "text": plain_title(item["text"])}
        for position, item in enumerate(heading_candidates(markdown), start=1)
    ]


def resolve_heading_map(markdown, choices):
    """Translate Agent-chosen digest numbers back into paragraph indices."""

    candidates = heading_candidates(markdown)
    resolved = []
    for choice in choices or ():
        item = choice.model_dump() if hasattr(choice, "model_dump") else choice
        if not isinstance(item, dict):
            continue
        try:
            position, level = int(item.get("n")), int(item.get("level"))
        except (TypeError, ValueError):
            continue
        if not 1 <= position <= len(candidates) or not 1 <= level <= 3:
            continue
        resolved.append({
            "index": candidates[position - 1]["index"],
            "level": level,
            "title": str(item.get("title") or "").strip(),
        })
    return resolved


def looks_like_title(text):
    if not text or len(text) > IMPORTED_CANDIDATE_MAX_CHARS:
        return False
    if text.startswith("|") or _LEADING_LIST.match(text):
        return False
    if _TRAILING_SENTENCE.search(text):
        return False
    return True


def plain_title(text):
    line = next((item.strip() for item in text.splitlines() if item.strip()), "")
    # Emphasis first: _TITLE_MARKUP would strip the opening "**" and leave the
    # closing one with nothing to pair with.
    result = _TITLE_MARKUP.sub("", strip_emphasis(line)).strip()
    return result[:IMPORTED_TITLE_MAX_CHARS]


def first_line_title(text, position):
    """Title a heading-less Block by its opening prose line."""

    for raw in text.splitlines():
        line = raw.strip()
        # Table rows read as pipe soup and list markers carry no topic.
        if not line or line.startswith("|") or set(line) <= {"-", "|", ":", " "}:
            continue
        candidate = _TITLE_MARKUP.sub("", strip_emphasis(line)).strip()
        if candidate:
            return candidate[:IMPORTED_TITLE_MAX_CHARS] + (
                "…" if len(candidate) > IMPORTED_TITLE_MAX_CHARS else ""
            )
    return f"原始片段 {position}"


def heading_remainder(text):
    """The lines of a heading paragraph below the line used as its title.

    A candidate is normally one short line, so this is usually empty. Keeping
    the rest as body anyway means a heading choice can never drop source text.
    """

    lines = text.splitlines()
    for position, line in enumerate(lines):
        if line.strip():
            return "\n".join(lines[position + 1:]).strip("\n")
    return ""
