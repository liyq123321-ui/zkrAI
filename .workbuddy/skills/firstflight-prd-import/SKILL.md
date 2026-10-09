---
name: firstflight-prd-import
description: Import, re-import and audit an existing PRD (docx / pdf / md) into firstFlight's prd_blocks tree, preserving the source file's own outline, every character of its text, its tables and its images. Use when a user says an imported PRD's 目录 / 分块 / 大纲 looks wrong or does not match the original file, when imported 图片 are missing, when 表格 render as unreadable pipe soup, when asked to re-issue / 重新出 a version of an imported PRD, or after touching backend/app/services/prd_source_import.py, imported_prd_layout.py, block_service.import_source or the asset services. Ships two runnable scripts: check_prd_import.py (a four-rule regression check anchored to the original .docx) and reimport_prd_source.py (backup + re-import through the real service path).
agent_created: true
---

# firstFlight PRD import

## Overview

An existing document enters the system through one chain:

```
POST /sessions/.../import-prd-source        (keep_file=True → stores the original .docx)
  → api/sessions.py            import_prd_source()
  → services/prd_source_import import_prd_source()   docx/pdf → canonical markdown + import_id
  → brief.source_prd           {filename, media_type, title, content, import_id, hierarchy}
  → services/imported_prd_layout split_imported_markdown()   markdown → (depth, title, body)
  → services/block_service     import_source()               → prd_blocks rows + assets
  → services/document_asset_service store_asset() / discard_blocks()
```

`brief.source_prd.content` is the **source of truth** for the imported PRD. Blocks are a
*rendering* of it and must stay lossless against it.

## The five durable rules

Every one of these was learned by breaking it. They are ordered by how often they bite.

### Rule 1 — Never trust Word styles for hierarchy

**Symptom:** every block lands at the same level; the tree is flat; the Agent then guesses
which short lines are titles and produces an outline that does not match the file.

**Cause:** the source DOCX has **no outline at all in Word's model**. On the reference
document: 1892/1892 paragraphs are style `Normal`, `outlineLvl=None`, and **no** heading
paragraph carries auto-numbering. The hierarchy exists *only in font sizes*:

| Size | Role | Count |
|------|------|-------|
| 26pt | document title | 1 |
| 18pt | level 1 (chapters) | 12 |
| 16pt | level 2 | 54 |
| 15pt | level 3 | 139 |
| 14pt | level 4 | 24 |
| 11pt | body | 1587 |

**Rule:** if `Heading N` styles exist, they win. Otherwise derive levels from a font-size
histogram (`_docx_size_levels()` in `prd_source_import.py`):

1. The **most common** size is body.
2. Every size **larger than body** is a heading level, largest = level 1, capped at
   `MAX_DOCX_HEADING_LEVEL = 6`.
3. A size that appears **exactly once** is the document title, not a level — drop it
   before numbering.
4. Only count runs with `run.text.strip()` and `run.font.size is not None`; a paragraph's
   size is the max over its runs.

**Never** flip this on file extension or on heuristics like "short line ⇒ heading".
Note the vendor *can* revert this: a future revision of the same file may carry real
Heading styles, and the branch handles that automatically.

### Rule 2 — A heading-only chapter must still become a block

**Symptom:** chapter N goes missing and all of its sections re-parent under chapter N-1.
The result is a plausible-looking tree with the wrong parents — easy to skim past.

**Cause:** `split_imported_markdown()` legitimately produces no body parts when a chapter
heading is immediately followed by a sub-heading. The old code dropped the empty section.

**Rule:** keep it, with an empty body:

```python
if not parts:
    if heading:
        position += 1
        blocks.append((depth, heading, ""))
    continue
```

**Invariant:** the number of top-level blocks must equal *source `# ` headings + 1*
(the document title rides ahead of the first heading as its own block).

### Rule 3 — GFM tables must be emitted with consecutive rows

**Symptom:** tables that looked fine in Word render as a wall of `|` pipes, or as plain
text. Losing one blank line is enough.

**Cause:** the frontend uses `react-markdown` + `remark-gfm`, which only recognises a table
when the header row, the `---` divider row and the body rows sit on **consecutive lines**.
Writing each row as its own paragraph introduced blank lines between them.

**Rule:** emit the whole table as **one** part:

```python
append("\n".join(
    ["| " + " | ".join(rows[0]) + " |",
     "| " + " | ".join("---" for _ in range(width)) + " |"]
    + ["| " + " | ".join(row) + " |" for row in rows[1:]]
))
```

Pad short rows to `width` — a ragged body row silently breaks the whole table. Two cell
escapes are also mandatory, both already in the loop above:

- `re.sub(r"\s+", " ", cell)` — a cell containing a line break spills out of its row and
  breaks that table **and every table below it**.
- `.replace("|", "\\|")` — a literal pipe inside a cell would otherwise add a column.

### Rule 4 — A one-column "table" is not a table

**Symptom:** the outline gains phantom chapters whose titles are code comments
(`# MemoryItem 增量 schema …`).

**Cause:** Word authors fake code samples and callouts as 1×N tables. A 1-column table
rendered as a markdown table leaks every `#`-comment line into the document as a heading.

**Rule:** detect via `_single_column_text(table)`, then split on shape:

- First line is a bare language token (`_CODE_LANGUAGE`) → **fenced code block**
  (`_code_block()`). This also protects any `#` inside the sample.
- Otherwise → **plain callout text**, unmarked.

### Rule 5 — Images need a stored original and an anchored asset row

**Symptom:** every image is simply gone; the PRD reads as if the figures never existed.

**Cause:** the extractor never read `w:drawing` — the bytes were discarded, not lost later.
Storage infrastructure (`DocumentAssetService`) already existed; the intake step was the
hole.

**Rule:** run the full three-hop path, never a shortcut:

1. **Extract.** `extract_docx_images(raw)` walks `w:drawing` / `w:blip`, keeps the bytes and
   the media type; the paragraph emits a positional token `[[FF_IMPORTED_IMAGE_n]]`.
2. **Persist the original.** `import_prd_source(keep_file=True)` writes the `.docx` to
   `backend/data/imported-prd/<import_id>.docx` (`imported_prd_store`,
   `import_id = sha256(content)[:32]`, cap `MAX_STORED_BYTES = 24 MiB`,
   `FIRSTFLIGHT_IMPORTED_PRD_ROOT` overrides). Without `import_id` the image rule cannot be
   verified later — always pass `keep_file=True`.
3. **Bind.** `block_service._restore_images()` calls `assets.store_asset(..., metadata=
   {"source": "imported_prd"})` with a `block_id`, then rewrites the token to
   `![file](asset://<asset_id>)`. The frontend renders it via
   `apiBaseUrl + documentPath(id) + '/assets/' + asset.id`.

**Two mandatory companions:**

- Pass `assets=assets` from `POST /documents/{id}/import-source`. Forget it and the tokens
  stay literal.
- When `import_source` deletes blocks it **must** call
  `assets.discard_blocks(db, doc.id, {…})`. Skipping it leaks asset rows pointing at
  deleted blocks (observed: 30 orphans from revisions 7–8). Wrap the per-block work so that
  a failure unlinks files written during that block.

## The verification loop — `scripts/check_prd_import.py`

Run this after **any** change to the chain, and any time a user says an import "looks
wrong". It reads `gateway.db` read-only, so the backend can be down.

```bash
cd "<repo>" && backend/.venv/Scripts/python.exe \
  .workbuddy/skills/firstflight-prd-import/scripts/check_prd_import.py
# --db <path>  --project <id-prefix>  --json  --asset-root  --imported-root
```

Exit code is 0 only when all four pass: `outline`, `verbatim`, `tables`, `images`. Each
rule is written to be **anchored to something outside our own output** — that is the whole
point, and the reason a naive checker is worthless:

| Rule | What it actually compares | Trap it avoids |
|------|---------------------------|----------------|
| `outline` | top-level blocks vs source `# ` headings **outside code fences** | counting `#` comments inside fenced code inflates the count by 6 on the reference file |
| `verbatim` | source must remain a **subsequence** of the rendered blocks | character-level `difflib` is O(n²) and gets SIGTERM'd on a 250k-char document |
| `tables` | every `\|---\|` divider has a `\|`-line above and below | a table split by blank lines still "looks like" markdown |
| `images` | **`w:drawing` count in the stored `.docx`** vs asset rows, plus magic bytes and sizes | counting the extractor's own tokens passes vacuously when the extractor never looked for images |

That last row is the load-bearing one: rule 5's anchor is the original DOCX, so
"the extractor silently dropped every image" fails loudly instead of passing with
`tokens=0, assets=0`. When no stored `.docx` exists (a pre-`import_id` import) the detail
says so explicitly rather than pretending to pass.

### Prove the checker can fail

A checker that only ever passes is decoration. Snapshots of the reference document, all
taken the same day, each fail exactly the rule that was still broken at that point:

| Snapshot | Fails |
|----------|-------|
| `…bak-before-import-source-313f1c46-20261008-143701` | outline, verbatim |
| `…bak-before-relayout-313f1c46-20261008-152838` | outline, tables |
| `…bak-before-images-313f1c46-20261008-154416` | tables |
| `…bak-before-images-313f1c46-20261008-155342` | images |
| `gateway.db` (current) | **none — all rules pass** |

Re-run this table after editing the checker. If a snapshot suddenly passes a rule it used
to fail, the rule lost its teeth.

## Procedure: change → re-import → verify

Two shipped scripts cover steps 2 and 3, so the loop is executable rather than
described. Both live in `scripts/`.

### Step 1 — back up · Step 2 — re-import

```bash
cd "<repo>/backend"
./.venv/Scripts/python.exe \
  ../.workbuddy/skills/firstflight-prd-import/scripts/reimport_prd_source.py \
  --project 313f1c46 --source "C:/Users/zkr/Downloads/原稿.docx"
```

`reimport_prd_source.py` drives the **real service path** (`import_prd_source` →
`BlockService.import_source(replace=True, assets=…)` → `DocumentAssetService`), so it
exercises every rule instead of hand-editing rows. It takes the backup itself through
SQLite's online backup API, rewrites `brief.source_prd`, sweeps unreachable assets, and
prints the resulting revision / blocks / asset bytes / top-level count.

`--dry-run` extracts and resolves the project without writing anything — use it to find
out what a re-run would do. An unchanged `import_id` is reported as
`extract : unchanged` — a cheap "did my change actually do anything?".

Two things it relies on, both easy to get wrong by hand:

- **`backend/.env` must be loaded before importing `app.*`.** `app/database/database.py`
  builds the engine at *module import* time (`_settings = Settings.from_env()`), so a late
  load points the whole run at the wrong database. The script parses `.env` itself and lets
  an already-exported shell variable win.
- **Run it from `backend/`.** `DATABASE_URL=sqlite:///data/gateway.db` is relative.

**Rehearse on a copy first when the change is non-trivial.** Both `DATABASE_URL` and
`PRD_ASSET_ROOT` can be overridden per-invocation, so a full write-path drill can run
against a throwaway database with zero impact on real data:

```bash
cp data/gateway.db.bak-before-<x> data/_scratch.db
DATABASE_URL="sqlite:///data/_scratch.db" PRD_ASSET_ROOT="$(pwd)/data/_scratch-assets" \
  ./.venv/Scripts/python.exe ../.workbuddy/skills/.../reimport_prd_source.py \
  --project 313f1c46 --source data/imported-prd/<import_id>.docx --label scratch-drill
../.workbuddy/skills/.../check_prd_import.py --db data/_scratch.db --asset-root data/_scratch-assets
```

Verified end-to-end this way: a rev-10 snapshot went to rev 11 with 236 blocks,
30 assets / 2,041,347 bytes, 0 orphans, 13 top-level blocks, and the checker green.

### Step 3 — verify

```bash
../.workbuddy/skills/firstflight-prd-import/scripts/check_prd_import.py     # require exit 0
```

Reference document end state: document `prd_554c188cd67543558b3b21adb19a2e10`, project
`313f1c46…`, **revision 11, 236 blocks, 13 top-level blocks, 30 assets / 2,041,347 bytes,
`removed=0`, 0 orphans**.

### Step 4 — record

Write the outcome and the storage root into `.workbuddy/memory/`.

## Symptom → rule

| The user says | Suspect |
|---------------|---------|
| "目录/分块不是按原文件分的", tree is flat, parents wrong | Rule 1, then Rule 2 |
| a chapter vanished, its sections moved up | Rule 2 |
| "表格变成了管道符/不好读" | Rule 3 |
| phantom chapters named like code comments | Rule 4 |
| "导入后的图片全部丢失" | Rule 5 — check `import_id` first |
| images render for some blocks only | Rule 5, `assets=assets` not passed |
| 401/404 from the import test | environment: `.env` sets `FIRSTFLIGHT_IDENTITY_MODE=auth` |

## Traps

- **`identical text: False` is usually a false alarm.** Comparing two *layouts'* title
  metadata (which legitimately differs) reads like data loss. Always diff against
  **`brief.source_prd.content`**, not against another rendering.
- **Emphasis stripping is order-sensitive.** `plain_title()` must strip `**`/`*`/`` ` ``
  **before** `_TITLE_MARKUP`; otherwise the opening `**` is consumed and a lone `**`
  survives in the title. Headings get `emphasis=False` from
  `_paragraph_markdown(paragraph, images, emphasis=level is None)`.
- **`MAX_IMPORTED_PRD_CHARACTERS = 400_000`** in `schemas/workflow.py`. The reference
  document is ~306k raw / ~256k normalised, so 300k was too tight.
- **`docx_service.py` is not the round-trip parser.** It has table→markdown and
  image→asset logic, but `_parse()` requires firstFlight's own Block markers and rejects a
  foreign DOCX. Its table converter has the same "only the first line gets `|`" flaw as the
  original bug — reuse the *asset service*, not that parser.
- **Re-import needs the backend restarted** if you changed anything under `app/` —
  uvicorn runs without `--reload` and `Settings.from_env()` reads once per process.
- `pytest tests/unit` baseline: 1 pre-existing failure
  (`test_ai_sentence_stream.py`). `pytest tests/integration/test_prd_source_import.py`
  needs `FIRSTFLIGHT_IDENTITY_MODE=legacy` or it 401s.
