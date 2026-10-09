#!/usr/bin/env python3
"""Check that an imported PRD kept its outline, its text, its tables and its images.

Run it after any change to the intake extractor, the layout splitter or the
adopt step — and any time a user says an imported PRD "looks wrong".

    python check_prd_import.py                        # the only project with a source PRD
    python check_prd_import.py --project 313f1c46     # by id prefix
    python check_prd_import.py --json                 # machine readable

Exit code is 0 only when every rule passes. Standard library only: it reads the
SQLite file directly, so it works even when the backend is down.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]

HEADING = re.compile(r"(?m)^#{1,6} ")
FENCE = re.compile(r"(?m)^```.*$")
DIVIDER = re.compile(r"^\s*\|(\s*:?-{3,}:?\s*\|)+\s*$")
EMPHASIS = re.compile(r"\*\*|\*|`")
IMAGE_TOKEN = re.compile(r"\[\[FF_IMPORTED_IMAGE_\d+\]\]")
ASSET_REF = re.compile(r"!\[[^\]]*\]\(asset://[^)]*\)")
IMAGE_MAGIC = {b"\xff\xd8\xff": "jpeg", b"\x89PNG": "png"}


def normalise(text: str) -> str:
    """Strip the markers we add, so only the source wording is compared.

    ``#`` prefixes, code fences and emphasis are ours; an image token and the
    ``asset://`` reference that replaces it are two spellings of the same thing.
    """

    text = IMAGE_TOKEN.sub("\x00", text)
    text = ASSET_REF.sub("\x00", text)
    text = EMPHASIS.sub("", text)
    text = HEADING.sub("", text)
    text = FENCE.sub("", text)
    return re.sub(r"\s+", "", text)


def load(db_path: Path, project_prefix: str | None):
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    # ``projects`` has no ``name`` column: the human label lives in ``brief.title``.
    rows = con.execute("select id, brief from projects").fetchall()
    candidates = []
    for row in rows:
        brief = json.loads(row["brief"] or "{}")
        source = brief.get("source_prd")
        if isinstance(source, dict) and str(source.get("content") or "").strip():
            label = brief.get("title") or source.get("filename") or "(untitled)"
            candidates.append((row["id"], label, source))
    if project_prefix:
        candidates = [c for c in candidates if c[0].startswith(project_prefix)]
    if not candidates:
        raise SystemExit("no project carries an imported source PRD")
    if len(candidates) > 1 and not project_prefix:
        raise SystemExit(
            "several projects carry a source PRD; pass --project:\n  "
            + "\n  ".join(f"{c[0]}  {c[1]}" for c in candidates)
        )
    project_id, project_name, source = candidates[0]

    document = con.execute(
        "select id, revision, template, plan_status from prd_documents where project_id=?",
        (project_id,),
    ).fetchone()
    if document is None:
        raise SystemExit(f"project {project_id} has no PRD document")
    blocks = con.execute(
        'select id, parent_id, title, content, "order" from prd_blocks where document_id=?'
        ' order by "order"',
        (document["id"],),
    ).fetchall()
    assets = con.execute(
        "select id, block_id, storage_key, mime_type, size_bytes from prd_document_assets"
        " where document_id=?",
        (document["id"],),
    ).fetchall()
    con.close()
    return project_id, project_name, source, document, blocks, assets


def headings_outside_fences(text: str, level: int = 1) -> list[str]:
    """Level-``level`` ATX headings that are real, not ``#`` comments in code.

    Our own extractor turns single-column tables into fenced code blocks, and a
    shell or Python comment inside such a fence starts with ``#`` — counting
    those as chapters overstates the outline by 6 on the reference document.
    """

    marker = "#" * level + " "
    inside, found = False, []
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            inside = not inside
            continue
        if not inside and line.startswith(marker) and not line.startswith(marker + "#"):
            found.append(line[len(marker):].strip())
    return found


def check_outline(source, blocks):
    """Rule 1 — the file's own outline survives, including heading-only chapters."""

    wanted = headings_outside_fences(source, 1)
    parents = {row["id"]: row["parent_id"] for row in blocks}
    tops = [row for row in blocks if not parents[row["id"]]]
    empty = sum(1 for row in blocks if not (row["content"] or "").strip())
    # The document title rides ahead of the first heading as its own block.
    expected = len(wanted) + 1 if wanted else len(tops)
    ok = len(tops) == expected
    return ok, {
        "top_level_blocks": len(tops),
        "expected": expected,
        "source_level1_headings": len(wanted),
        "heading_only_blocks": empty,
        "top_level_titles": [row["title"] for row in tops],
        "source_titles": wanted,
    }


def check_verbatim(source, blocks):
    """Rule 2 — not one character of the source is dropped or reordered.

    ``difflib`` at character level is O(n²) and dies on a 250k-character
    document, so we use the cheaper equivalent question instead: *is the source
    still a subsequence of what we produced?* Our own markers only ever add
    characters, so ``removed == 0`` means nothing was deleted or moved.
    """

    rendered = "".join(f"{row['title']}\n\n{row['content'] or ''}" for row in blocks)
    left, right = normalise(source), normalise(rendered)
    if left == right:
        return True, {"source_characters": len(left), "removed_characters": 0,
                      "added_characters": 0, "removed_samples": []}

    cursor, misses = 0, []
    for char in right:
        if left[cursor:cursor + 1] == char:
            cursor += 1
        elif len(misses) < 3:
            misses.append(left[cursor:cursor + 40])
    removed = len(left) - cursor
    added = len(right) - len(left)
    return removed == 0, {
        "source_characters": len(left),
        "rendered_characters": len(right),
        "removed_characters": removed,
        "added_characters": added,
        # Only meaningful when something really went missing.
        "removed_samples": [sample for sample in misses if sample] if removed else [],
    }


def check_tables(blocks):
    """Rule 3 — a GFM table only renders when its rows sit on consecutive lines."""

    broken, good = [], 0
    for row in blocks:
        lines = (row["content"] or "").splitlines()
        for index, line in enumerate(lines):
            if not DIVIDER.match(line):
                continue
            previous = lines[index - 1] if index else ""
            following = lines[index + 1] if index + 1 < len(lines) else ""
            if previous.startswith("|") and (not following or following.startswith("|")):
                good += 1
            else:
                broken.append({"block": row["title"][:60], "line": line[:60]})
    return not broken, {"tables": good, "broken_dividers": broken[:5],
                        "broken_count": len(broken)}


def docx_image_count(path: Path) -> int | None:
    """How many images the original DOCX actually embeds, or ``None`` if absent.

    Rule 4 needs a witness that lives *outside* our own pipeline: the image
    tokens in the stored source are produced by the extractor, so an extractor
    that silently drops every image yields zero tokens and would pass. The
    stored ``.docx`` is that witness.
    """

    if not path.is_file():
        return None
    try:
        with zipfile.ZipFile(path) as archive:
            document = archive.read("word/document.xml").decode("utf-8", "replace")
    except (zipfile.BadZipFile, KeyError, OSError):
        return None
    return document.count("<w:drawing")


def check_images(source_text, import_id, blocks, assets, asset_root: Path, imported_root: Path):
    """Rule 4 — every image in the file is stored and referenced by its Block.

    The count is anchored to the original DOCX, never to the extractor's own
    output, so "the extractor never looked for images" fails loudly instead of
    passing vacuously.
    """

    tokens = len(IMAGE_TOKEN.findall(source_text))
    references = sum(len(ASSET_REF.findall(row["content"] or "")) for row in blocks)
    leftovers = sum(len(IMAGE_TOKEN.findall(row["content"] or "")) for row in blocks)
    live = {row["id"] for row in blocks}
    orphaned = [row["id"] for row in assets if row["block_id"] and row["block_id"] not in live]
    missing, corrupt = [], []
    total_bytes = 0
    for row in assets:
        path = asset_root / row["storage_key"]
        if not path.is_file():
            missing.append(row["id"])
            continue
        total_bytes += path.stat().st_size
        if path.stat().st_size != row["size_bytes"]:
            corrupt.append(row["id"])
            continue
        head = path.read_bytes()[:4]
        if not any(head.startswith(magic) for magic in IMAGE_MAGIC):
            corrupt.append(row["id"])

    embedded = docx_image_count(imported_root / f"{import_id}.docx") if import_id else None
    # Prefer the document itself; fall back to the extractor's tokens only when
    # no stored DOCX is available (an older, pre-``import_id`` import).
    expected = embedded if embedded is not None else tokens

    ok = (tokens == expected and leftovers == 0 and not orphaned and not missing
          and not corrupt and references == len(assets))
    detail = {
        "docx_embedded_images": embedded if embedded is not None else "(no stored docx)",
        "source_image_tokens": tokens,
        "asset_rows": len(assets),
        "asset_references_in_blocks": references,
        "asset_bytes": total_bytes,
        "leftover_tokens": leftovers,
        "orphaned_assets": len(orphaned),
        "missing_files": len(missing),
        "corrupt_files": len(corrupt),
    }
    if embedded is None:
        detail["note"] = ("no stored .docx (pre-import_id import): this rule is "
                          "unanchored and can pass vacuously")
    return ok, detail


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(REPO_ROOT / "backend" / "data" / "gateway.db"))
    parser.add_argument("--asset-root", default=None)
    parser.add_argument("--imported-root", default=None)
    parser.add_argument("--project", default=None, help="project id or id prefix")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.is_file():
        raise SystemExit(f"database not found: {db_path}")
    asset_root = Path(args.asset_root) if args.asset_root else REPO_ROOT / "backend" / "data" / "prd_assets"
    imported_root = (Path(args.imported_root) if args.imported_root
                     else REPO_ROOT / "backend" / "data" / "imported-prd")

    project_id, project_name, source, document, blocks, assets = load(db_path, args.project)
    source_text = str(source["content"])
    import_id = str(source.get("import_id") or "")

    results = [
        ("outline", "outline: file's own hierarchy survives", *check_outline(source_text, blocks)),
        ("verbatim", "verbatim: no source character lost", *check_verbatim(source_text, blocks)),
        ("tables", "tables: rows stay on consecutive lines", *check_tables(blocks)),
        ("images", "images: stored, bound and referenced",
         *check_images(source_text, import_id, blocks, assets, asset_root, imported_root)),
    ]

    if args.json:
        print(json.dumps({
            "project": {"id": project_id, "name": project_name},
            "document": dict(document),
            "blocks": len(blocks),
            "import_id": source.get("import_id", ""),
            "checks": {key: {"pass": ok, "detail": detail} for key, _, ok, detail in results},
        }, ensure_ascii=False, indent=2))
    else:
        print(f"project  : {project_name} ({project_id})")
        print(f"document : {document['id']}  revision {document['revision']}"
              f"  template {document['template']}  {document['plan_status']}")
        print(f"blocks   : {len(blocks)}   import_id {source.get('import_id') or '(none)'}")
        print()
        for key, label, ok, detail in results:
            print(f"[{'PASS' if ok else 'FAIL'}] {label}")
            for name, value in detail.items():
                if name in ("top_level_titles", "source_titles"):
                    continue
                print(f"         {name}: {value}")
            if not ok and detail.get("top_level_titles"):
                print("         blocks: " + " / ".join(detail["top_level_titles"][:8]))
            if not ok and detail.get("source_titles"):
                print("         source: " + " / ".join(detail["source_titles"][:8]))
        failed = [label for _, label, ok, _ in results if not ok]
        print()
        print("RESULT   : " + ("all rules pass" if not failed
                               else "FAILED -> " + "; ".join(failed)))
    return 0 if all(ok for _, _, ok, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())
