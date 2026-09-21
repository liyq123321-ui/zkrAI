---
name: firstflight-gateway-db
description: Safely inspect and delete records in the firstFlight SQLite gateway database (backend/data/gateway.db) — resolving project/work-item graphs, scanning every table/column for references, following second-hop links such as prd_documents.id, taking a consistent pre-change snapshot, deleting inside one transaction, auditing for orphaned rows, and verifying afterwards. Use whenever a task asks to clean up / remove / 清理 / 删除 projects, root tasks, work items, or their child records (tasks, versions, comments, dependencies, agent_specs, work_item_runs, prd_blocks, prd_block_versions, prd_agent_runs), to make a project disappear from the UI's 当前任务 dropdown, or to restore a gateway-before-*.db snapshot.
agent_created: true
---

# firstFlight Gateway DB maintenance

## Overview

The firstFlight backend persists everything to one SQLite file: `backend/data/gateway.db`
(`DATABASE_URL=sqlite:///data/gateway.db`). It has 35 tables with **no foreign-key
constraints at all** (`PRAGMA foreign_keys=0`), so nothing cascades and nothing stops a
half-deletion. Every destructive request must therefore resolve its own dependency graph,
and must leave a restorable snapshot behind.

## When to use

- "把这几个任务/项目从数据库里删掉" — delete work items, plan trees, or whole projects.
- "下拉框里那个项目还在 / 页面这几项没清理干净" — a project shell survived an earlier
  work-item-only cleanup. The 当前任务 dropdown is built from the **`projects` table**
  (`query_service.sessions()`), so only a `project-domain` sweep removes it.
- "清理测试数据 / 恢复某个账号 / 回滚" — restore from a `gateway-before-*` snapshot.
- Any request naming a row id and asking for something to be removed.

## Non-negotiable rules

1. **Read before write.** Resolve the exact target id set and enumerate every referencing
   row first. Never delete on a first guess about the schema.
2. **Sweep every hop, not just the first.** A value scan on project/session ids misses
   children that key off a *parent row's own id* — `prd_*` hangs off `prd_documents.id`.
   Always finish with the orphan audit (§ Verifying) before declaring success.
3. **Snapshot first, always.** Use `sqlite3.Connection.backup()` (never a plain file copy —
   the backend may be writing concurrently and a copy can be torn).
4. **One transaction.** All deletes in a single `with conn:` block, then commit.
5. **Confirm when scope is ambiguous.** If the boundary could mean "just these rows" or
   "these rows plus their project", ask before deleting. Show the concrete row counts.
6. **Never auto-delete synthetic marker values.** Some columns hold intentional
   non-id markers (see traps below). Rows carrying them often belong to *live* projects.
7. **Verify after.** Re-run the reference scan, run the orphan audit, and run
   `PRAGMA integrity_check`.
8. **Report the backup path** so the user can restore.

## Schema traps

### Trap 1 — `work_items` is not a `parent_id` tree

| node | `kind` | `parent_id` | `local_key` (varies) |
|------|--------|-------------|----------------------|
| root task | `ROOT` | `NULL` | `root` |
| milestone | `MILESTONE` | **`NULL`** | `M1`, `ms-requirements`, … |
| task | `TASK` | → the milestone's id | `T1`, `gate-design`, … |

Milestones hang off nothing. Recursing on `parent_id` from the root returns **zero**
children and silently produces a wrong answer. A plan tree is scoped by `project_id`
(or equivalently `session_id`); each project holds exactly one `ROOT`.

Rely on `kind` and `project_id` only. `local_key` is free-form and differs per
generated plan, so never match on it.

### Trap 2 — the PRD family is a second hop

`prd_blocks`, `prd_block_versions`, `prd_block_comments`, `prd_agent_runs`,
`prd_document_artifacts` and `prd_document_assets` all key off **`prd_documents.id`**
(`document_id`), *not* off `project_id` or `session_id`. A sweep that only looks for
project/session/work-item ids will leave hundreds of these rows behind. The script
resolves a `prd_document` seed set for exactly this reason, and `orphans` catches the
case where the parent was already deleted by an earlier, narrower sweep.

### Trap 3 — synthetic markers look like dangling references

These are **intentional** values written by the readonly import paths, and their rows
belong to live projects — never delete them:

```
spec_versions.generator_agent_session_id   = 'codex-readonly-prd-import-20260911'
prd_prototypes.generator_agent_session_id  = 'codex-readonly-prototype-map-20260911'
prd_prototypes.generator_call_id           = 'readonly-prototype-map:<project-id>:v5'
```

Real ids are UUIDs, or `prd_<32 hex>` for `prd_documents`. The `orphans` command flags
non-UUID values as `[synthetic marker — do NOT delete]` and **refuses to delete without an
explicit `--link`**, so a blanket "clean the orphans" can never wipe live data.

## Workflow

### 1. Preview (read-only)

```bash
BACKEND/.venv/Scripts/python.exe .workbuddy/skills/firstflight-gateway-db/scripts/gateway_db.py \
  scope --scope project-domain --project-id <uuid>[,<uuid>...]
```

Prints the resolved seed sets, the full reference map (table × column × row count across
**all** tables), the deletion plan, and the current orphan audit. Use `--id` instead of
`--project-id` for explicit work-item ids, and omit `--scope` for the default
`work-item-domain`.

Do not hand-pick a few column names. The scan must walk every table and every column,
because the real foreign keys are not the obvious ones — `work_item_dependencies` uses
`from_work_item_id` / `to_work_item_id`, which is easy to miss.

### 2. Decide the boundary

Two common scopes, both legitimate — they must be distinguished explicitly:

- **work-item-domain** — `work_items` + `tasks` / `versions` / `comments_index` /
  `work_item_dependencies` / `agent_specs` / `work_item_runs`. Keeps the project row,
  PRD documents, spec versions, sessions and audit history. **The project stays visible in
  the 当前任务 dropdown.**
- **project-domain** — additionally `projects`, `conversations`, `agent_sessions`,
  `agent_calls`, `agent_runtime_events`, `audit_events`, `spec_versions`, `spec_reviews`,
  `sdlc_route_decisions`, `prd_documents`, `prd_prototypes`, `clarification_*`,
  `artifacts`, `intake_analysis_claims`, `command_attempts`, `processed_commands`, and the
  second-hop `prd_*` rows. This makes the project disappear from the UI entirely.

Present the counts and let the user choose. See `references/schema.md` for the table map.

### 3. Delete

```bash
BACKEND/.venv/Scripts/python.exe .workbuddy/skills/firstflight-gateway-db/scripts/gateway_db.py \
  delete --scope project-domain --project-id <uuid>[,...] --reason "delete-projects-3orphan" --yes
```

Prints the row plan, writes `gateway-before-<reason>-<YYYYMMDD-HHMMSS>.db`, deletes in one
transaction, then re-scans, runs the orphan audit and `integrity_check`. Without `--yes`
the command only reports what it would do.

### 4. Verify

Expected: `leftover references: none`, `orphan check: none` (or only synthetic markers, in
which case `cleanable: 0`), `integrity_check: ok`, and the preserved-table counts unchanged.
Confirm the UI list itself by calling the same query the dropdown uses:

```bash
BACKEND/.venv/Scripts/python.exe -c "
import sys; sys.path.insert(0,'.')
from app.database.database import create_engine_for_url, make_session_factory
from app.services.query_service import QueryService
qs = QueryService(make_session_factory(create_engine_for_url('sqlite:///data/gateway.db')))
print([s.title for s in qs.sessions()])"
```

Then refresh the page (SQLite reads are live; no backend restart needed).

## Cleaning leftovers from an earlier sweep

```bash
gateway_db.py orphans                                   # report only
gateway_db.py orphans --delete --yes --link prd_blocks.document_id --link prd_block_versions.document_id
```

`--delete` requires an explicit `--link <table.column>` for every relation to clean;
without it the command refuses, naming the available links. Snapshot is taken first.

## Restoring a snapshot

```bash
gateway_db.py restore --backup <name.db>
```

Copies the snapshot over `gateway.db` after first snapshotting the current state, so a
mistaken restore is itself reversible.

## Environment notes

- Always run the repo's interpreter: `backend/.venv/Scripts/python.exe` (has the deps).
- In Git Bash, prefix commands with `export PATH="/usr/bin:/bin:/c/Windows/System32:$PATH"`.
- Passing `/c/Users/...` paths to a Windows `python.exe` yields `C:\c\Users\...`. Write
  absolute paths as `C:/Users/...` instead.
- Backup files are ~69 MB each and accumulate; mention them rather than deleting silently.
