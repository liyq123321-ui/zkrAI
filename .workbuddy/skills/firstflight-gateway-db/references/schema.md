# gateway.db schema map

`backend/data/gateway.db` — 35 tables, **no foreign-key constraints**
(`PRAGMA foreign_keys = 0`). Nothing cascades; every deletion must be explicit.
Connection string: `DATABASE_URL=sqlite:///data/gateway.db`.

## Work-item plan structure

`work_items` columns that matter:

```
id  session_id  parent_id  type  department  title  description  spec
status  project_id  local_key  kind  executable  objective  scope  exclusions
inputs  outputs  acceptance_criteria  required_skills  responsible_role
suggested_assignee  summary
```

`kind` ∈ {`ROOT`, `MILESTONE`, `TASK`}. `local_key` is free-form and generated per
plan — it is **not** a stable enum. Observed so far:

- `root` / `M1..Mn` / `T1..Tn` (project `73ccaabe-…`)
- `root` / `ms-requirements`, `ms-design`, … / `task-design-work`, `gate-design`, … (project `350bf723-…`)

Never match on `local_key`; use `kind` + `project_id`.

**`parent_id` is `NULL` for both `ROOT` and every `MILESTONE`.** Only `TASK` rows
point at a milestone. The plan tree is therefore scoped by `project_id` /
`session_id`, not by walking `parent_id`. Each project has exactly one `ROOT`.

Verified example (project `73ccaabe-7647-441d-9697-0228f45dfb87`, 8 rows — since deleted):

```
root   ROOT       ab3755ea…  parent=None
M1     MILESTONE  10171075…  parent=None
M2     MILESTONE  6bf35d7c…  parent=None
M3     MILESTONE  c78b3f79…  parent=None
T1     TASK       ad3346f5…  parent=10171075
T2     TASK       ea88aeb0…  parent=6bf35d7c
T3     TASK       3960b8b6…  parent=c78b3f79
T4     TASK       feaee66c…  parent=c78b3f79
```

## Tables referencing a work-item id

| table | column(s) | meaning |
|-------|-----------|---------|
| `work_items` | `id`, `parent_id` | the plan rows themselves |
| `tasks` | `wi` | PM rewrite task records (base/new version, findings, receipts) |
| `versions` | `wi` | per-work-item version history |
| `comments_index` | `wi` | review comment index |
| `work_item_dependencies` | `from_work_item_id`, `to_work_item_id` | dependency edges |
| `agent_specs` | `work_item_id` | agent specs |
| `work_item_runs` | `work_item_id` | run ledger |

`work_item_dependencies` uses two differently-named columns — a scan that only looks
at `work_item_id` will miss every edge. Always scan all columns of all tables.

## Project-scoped tables

Columns holding `project_id` (or `session_id`) — the wider "whole project" scope:

| table | key column |
|-------|-----------|
| `projects` | `id`; also carries `session_id` (the project row itself) |
| `conversations` | **`id`** — the conversation id *equals* the project's `session_id`; there is no `session_id` column here |
| `agent_sessions` | `project_id` |
| `agent_calls` | `project_id` |
| `agent_runtime_events` | `project_id` |
| `audit_events` | `project_id`, `session_id` |
| `spec_versions` | `project_id` |
| `spec_reviews` | `project_id` |
| `sdlc_route_decisions` | `project_id` |
| `prd_documents` | `project_id`, `session_id` |
| `prd_prototypes` | `project_id` |
| `clarification_requests` | `project_id` |
| `clarification_responses` | `project_id` |
| `intake_analysis_claims` | `project_id` |
| `artifacts` | `project_id` |
| `work_item_dependencies` | `project_id` |
| `command_attempts` | `project_id`, `session_id` |
| `processed_commands` | `session_id` (no `project_id`) |

`projects.current_spec_version_id` points into `spec_versions` — deleting spec
versions while keeping the project leaves a dangling pointer. Prefer keeping
`spec_versions` unless the whole project is being removed.

## The PRD family — a second hop

These tables have **no `project_id`/`session_id`**; they key off `prd_documents.id`
via `document_id`, and off `prd_blocks.id` / `prd_agent_runs.id` in the third hop:

| table | parent link |
|-------|-------------|
| `prd_blocks` | `document_id` → `prd_documents.id`; `parent_id` → `prd_blocks.id` |
| `prd_block_versions` | `document_id` → `prd_documents.id`; `block_id` → `prd_blocks.id`; `run_id` → `prd_agent_runs.id` |
| `prd_block_comments` | `document_id` → `prd_documents.id`; `block_id` → `prd_blocks.id` |
| `prd_agent_runs` | `document_id` → `prd_documents.id`; `block_id` → `prd_blocks.id` |
| `prd_document_artifacts` | `document_id` → `prd_documents.id` |
| `prd_document_assets` | `document_id` → `prd_documents.id`; `block_id` → `prd_blocks.id` |

Observed scale for one deleted project: 153 `prd_block_versions`, 71 `prd_agent_runs`,
27 `prd_blocks`, 2 `prd_block_comments` — **253 rows a project-id-only sweep misses.**
Resolve the `prd_documents` ids first, then delete by `document_id`.

## Synthetic marker values (never delete)

Some columns hold intentional non-id markers instead of a real foreign key. Their rows
belong to **live** projects; a naive "parent row is missing" check reports them as
orphans and deleting them destroys good data.

| column | observed value |
|--------|----------------|
| `spec_versions.generator_agent_session_id` | `codex-readonly-prd-import-20260911` |
| `prd_prototypes.generator_agent_session_id` | `codex-readonly-prototype-map-20260911` |
| `prd_prototypes.generator_call_id` | `readonly-prototype-map:<project-id>:v5` |

Real ids are UUIDs, or `prd_<32 hex>` for `prd_documents`. `scripts/gateway_db.py orphans`
flags non-UUID values as `[synthetic marker — do NOT delete]` and requires an explicit
`--link` before deleting, so these can never be swept by accident.

## Backup naming convention

`gateway-before-<reason>-<YYYYMMDD-HHMMSS>.db`, written next to `gateway.db`.
Existing examples: `gateway-before-delete-temperature-e3f4-20260907-122210.db`,
`gateway-before-prd-v2-repair-20260904.db`,
`gateway-before-prototype-mapping-20260911-150107.db`,
`gateway-before-readonly-prd-import-20260911-1444.db`,
`gateway-before-delete-workitems-3roots-20260921-135956.db`,
`gateway-before-delete-projects-3orphan-20260921-140947.db`,
`gateway-before-clean-prd-orphans-3projects-20260921-141140.db`.

Each is ~69 MB. Use `sqlite3.Connection.backup()` rather than a file copy so the
snapshot stays consistent while the backend is writing.

## UI wiring (where a project id surfaces)

- `GET /sessions` → `app/services/query_service.py::sessions()` iterates the **`projects`**
  table and titles each entry via `concise_project_title(brief.title or root_work_item.title
  or brief.final_objective, session_id)`. **A project with a deleted ROOT and an empty
  `brief.title` therefore renders as a bare UUID in the 当前任务 dropdown** — that is the
  tell-tale sign of a project shell left behind by a work-item-only cleanup.
- The TopLevelGraph / SDLC tab reads `GET /sessions`, so removing the `projects` row is
  what clears the list.

## Misc

- `agent_sessions` has no `status` column — check `PRAGMA table_info` before guessing.
- `projects` has no `name` column; the human-readable brief is in `projects.brief` (JSON).
- `messages` is currently empty; conversation content is not persisted there.
- `users` / `agent_calls` etc. use integer ids for actors; UUID comparisons against
  them are always empty, so the all-column scan stays cheap and precise.
