# Root Summary and UUID Copy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist AI-generated root summaries of at most 20 characters, backfill existing roots, make Kanban UUIDs copyable, improve root-filter labels, and place a structured project-requirement header above PRD review.

**Architecture:** Extend the existing PM brief-analysis output with an optional summary, validate it independently so summary defects cannot break intake, and persist it on the root `WorkItem`. Expose the field through the existing work-item API, use focused React components for UUID copying and root detail, and provide an idempotent backend backfill command that reuses PM analysis while applying only the summary.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy, Pydantic, pytest, React 19, TypeScript, Vitest, Testing Library, Vite.

## Global Constraints

- Root summaries are trimmed, single-line, non-empty, and at most 20 Unicode characters.
- Normal project creation reuses the existing PM analysis and adds no second AI call.
- Missing or invalid summaries never block intake, listing, filtering, or PRD review.
- Existing non-empty summaries are never overwritten by backfill.
- Original root titles and objectives are never shortened or overwritten.
- UUID copy writes the raw UUID without the visual `#` prefix and never opens the card.
- Root filter options display `summary（UUID）`, falling back to `original title（UUID）`.
- Root detail order is UUID, summary, original detail, then the existing PRD review panel.

---

### Task 1: Persist and expose root summaries

**Files:**
- Modify: `backend/app/database/models.py`
- Modify: `backend/app/database/database.py`
- Modify: `backend/app/services/query_service.py`
- Modify: `backend/tests/unit/test_database_schema.py`
- Modify: `backend/tests/integration/test_sessions_api.py`
- Modify: `frontend/aios-main/src/api/dto.ts`

**Interfaces:**
- Produces: nullable `WorkItem.summary: str | None` database field.
- Produces: nullable `WorkItemRead.summary` and `WorkItemDto.summary` API fields.
- Consumes: existing SQLite `_migrate_sqlite_work_items()` forward migration.

- [ ] **Step 1: Add failing schema and API projection tests**

Extend the legacy work-item migration test with:

```python
columns = {item["name"] for item in inspect(engine).get_columns("work_items")}
assert "summary" in columns
with engine.connect() as connection:
    legacy = connection.execute(
        text("SELECT title, description, department, summary FROM work_items WHERE id='legacy-wi'")
    ).one()
assert legacy == ("Legacy", "keep me", "ops", None)
```

In the sessions API test fixture, persist a root with `summary="知识问答助手"` and assert:

```python
response = client.get("/sessions/session-1/work-items")
assert response.status_code == 200
root = next(item for item in response.json() if item["kind"] == "ROOT")
assert root["summary"] == "知识问答助手"
```

- [ ] **Step 2: Run the focused backend tests and verify RED**

Run:

```bash
backend/.venv/bin/pytest backend/tests/unit/test_database_schema.py backend/tests/integration/test_sessions_api.py -q
```

Expected: failures because `work_items.summary` and `WorkItemRead.summary` do not exist.

- [ ] **Step 3: Add the database field, migration, and read projections**

Add to `WorkItem`:

```python
summary = Column(String, nullable=True)
```

Add `"summary": "VARCHAR"` to `_migrate_sqlite_work_items()` additions. Add `summary: str | None` to `WorkItemRead`, pass `summary=item.summary` in `_work_item_read()`, and add this field to the frontend DTO:

```ts
summary: string | null;
```

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run the Task 1 pytest command again. Expected: both files pass.

- [ ] **Step 5: Commit Task 1**

```bash
git add backend/app/database/models.py backend/app/database/database.py backend/app/services/query_service.py backend/tests/unit/test_database_schema.py backend/tests/integration/test_sessions_api.py frontend/aios-main/src/api/dto.ts
git commit -m "feat: persist root work item summaries"
```

---

### Task 2: Generate summaries through PM analysis and persist them safely

**Files:**
- Modify: `backend/app/domain/types.py`
- Modify: `backend/prompts/nodes/pm_analyze.txt`
- Modify: `backend/app/services/project_service.py`
- Modify: `backend/app/services/command_service.py`
- Modify: `backend/tests/integration/test_project_intake.py`
- Modify: `backend/tests/integration/test_command_service.py`
- Modify: `backend/tests/unit/test_agent_gateway.py`

**Interfaces:**
- Produces: `ProjectBriefUpdates.summary: str | None`, normalized to a valid value or `None`.
- Produces: `ActionScopedUnitOfWork.update_root_summary(summary: str) -> None`.
- Consumes: the existing `AgentGateway.analyze_brief()` call and `pm_analyze` node.

- [ ] **Step 1: Add failing summary normalization tests**

Add parameterized tests around `ProjectBriefUpdates`:

```python
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  本地温度换算器  ", "本地温度换算器"),
        ("", None),
        ("第一行\n第二行", None),
        ("超过二十个字符的项目摘要必须被忽略不能影响项目需求创建", None),
    ],
)
def test_project_summary_is_isolated_from_brief_validation(raw, expected):
    updates = ProjectBriefUpdates(summary=raw)
    assert updates.summary == expected
```

Also assert `updates.apply_to(brief)` does not insert a `summary` key into the formal Project Brief.

- [ ] **Step 2: Add failing project-intake persistence tests**

Make the scripted PM result include:

```python
ClarificationAnalysis(
    ready_for_spec=True,
    questions=[],
    assumptions=[],
    brief_updates={"summary": "本地温度换算器"},
)
```

After `create_session()`, assert the root row has that summary. Add a clarification-command test whose second PM result returns `{"summary": "温度换算交付"}` and assert the same root is updated atomically with the clarification response.

- [ ] **Step 3: Run focused tests and verify RED**

Run:

```bash
backend/.venv/bin/pytest backend/tests/integration/test_project_intake.py backend/tests/integration/test_command_service.py backend/tests/unit/test_agent_gateway.py -q
```

Expected: failures because `ProjectBriefUpdates.summary` and root-summary persistence are missing.

- [ ] **Step 4: Implement isolated summary normalization**

Add an optional field and before-validator to `ProjectBriefUpdates`:

```python
summary: str | None = None

@field_validator("summary", mode="before")
@classmethod
def normalize_summary(cls, value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized or "\n" in normalized or "\r" in normalized or len(normalized) > 20:
        return None
    return normalized
```

Exclude `summary` from `apply_to()` so it remains a WorkItem presentation field:

```python
return {
    **brief,
    **self.model_dump(mode="json", exclude_none=True, exclude={"summary"}),
}
```

- [ ] **Step 5: Persist valid summaries in both PM materialization paths**

In initial `_run_analysis()`, apply `analysis.brief_updates.summary` to the project's `ROOT` WorkItem when non-null. Add `ActionScopedUnitOfWork.update_root_summary()` for MESSAGE materialization and record its permitted root ID so `_enforce_guard()` allows only the `summary` attribute of that exact existing root row to change.

Call it alongside `uow.update_project_brief()`:

```python
uow.update_project_brief(analysis.brief_updates)
if analysis.brief_updates.summary is not None:
    uow.update_root_summary(analysis.brief_updates.summary)
```

- [ ] **Step 6: Require a concise summary in the PM prompt**

Append this exact policy to `pm_analyze.txt`:

```text
Always return brief_updates.summary as a concise, single-line project name derived
only from the confirmed brief. It must be non-empty and no longer than 20 Unicode
characters. Refresh it when a confirmed objective change makes the old name stale.
```

Extend the agent-gateway assertion so the structured schema and prompt include `summary` and the 20-character constraint.

- [ ] **Step 7: Run focused tests and verify GREEN**

Run the Task 2 pytest command again. Expected: all selected tests pass.

- [ ] **Step 8: Commit Task 2**

```bash
git add backend/app/domain/types.py backend/prompts/nodes/pm_analyze.txt backend/app/services/project_service.py backend/app/services/command_service.py backend/tests/integration/test_project_intake.py backend/tests/integration/test_command_service.py backend/tests/unit/test_agent_gateway.py
git commit -m "feat: generate root summaries during intake"
```

---

### Task 3: Backfill existing project summaries

**Files:**
- Create: `backend/app/services/project_summary_backfill.py`
- Create: `backend/app/scripts/__init__.py`
- Create: `backend/app/scripts/backfill_root_summaries.py`
- Create: `backend/tests/unit/test_project_summary_backfill.py`

**Interfaces:**
- Produces: `SummaryBackfillResult(updated: list[str], skipped: list[str], failed: dict[str, str])`.
- Produces: `ProjectSummaryBackfill.run() -> SummaryBackfillResult` asynchronous service.
- Produces: `python -m app.scripts.backfill_root_summaries` idempotent command.
- Consumes: `ProjectService._analysis_payload()`, `AgentGateway.analyze_brief()`, and validated `brief_updates.summary`.

- [ ] **Step 1: Add failing backfill service tests**

Create three projects: one missing summary, one with `summary="已有摘要"`, and one whose scripted agent fails. Assert:

```python
result = await ProjectSummaryBackfill(session_factory, agent).run()
assert result.updated == [missing_project.id]
assert result.skipped == [existing_project.id]
assert list(result.failed) == [failing_project.id]
with session_factory() as db:
    assert db.get(WorkItem, missing_root.id).summary == "补齐后的摘要"
    assert db.get(WorkItem, existing_root.id).summary == "已有摘要"
```

Run the service twice and assert the second run does not call the agent for either populated root.

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
backend/.venv/bin/pytest backend/tests/unit/test_project_summary_backfill.py -q
```

Expected: import failure because the backfill service does not exist.

- [ ] **Step 3: Implement the idempotent service**

Implement a frozen result dataclass and a service that sorts roots by project ID, skips non-empty summaries, builds the existing PM analysis payload, awaits the agent outside a database transaction, and then conditionally updates the same root only if it is still blank. Invalid/missing returned summaries are recorded in `failed`; each successful root commits independently.

The backfill must ignore `questions`, `assumptions`, and all non-summary `brief_updates`.

- [ ] **Step 4: Add the executable command**

The module command must initialize the configured database, construct `CodexAgentGateway`, run the service, print JSON, and return nonzero if any project failed:

```python
async def run() -> int:
    settings = Settings.from_env()
    init_database(engine)
    result = await ProjectSummaryBackfill(
        SessionLocal, CodexAgentGateway(settings=settings)
    ).run()
    print(json.dumps(asdict(result), ensure_ascii=False, sort_keys=True))
    return 1 if result.failed else 0

if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
```

- [ ] **Step 5: Run the focused test and verify GREEN**

Run the Task 3 pytest command again. Expected: all backfill cases pass.

- [ ] **Step 6: Commit Task 3**

```bash
git add backend/app/services/project_summary_backfill.py backend/app/scripts/__init__.py backend/app/scripts/backfill_root_summaries.py backend/tests/unit/test_project_summary_backfill.py
git commit -m "feat: backfill missing root summaries"
```

---

### Task 4: Add copyable UUIDs and summary-based board/filter labels

**Files:**
- Create: `frontend/aios-main/src/api/CopyableWorkItemId.tsx`
- Create: `frontend/aios-main/src/api/CopyableWorkItemId.test.tsx`
- Modify: `frontend/aios-main/src/api/ApiWorkspace.tsx`
- Modify: `frontend/aios-main/src/api/RootTaskFilter.tsx`
- Modify: `frontend/aios-main/src/api/workspace.test.tsx`
- Modify: `frontend/aios-main/src/index.css`

**Interfaces:**
- Produces: `CopyableWorkItemId({ id, onResult }: { id: string; onResult: (message: string) => void })`.
- Consumes: root `WorkItemDto.summary` from Task 1.
- Produces: root-filter entries shaped as `{ id, title }`, where `title` is exactly `summary（UUID）` with title fallback.

- [ ] **Step 1: Add failing UUID-copy component tests**

Render the component with a mocked clipboard and assert:

```ts
fireEvent.click(screen.getByRole('button', { name: '复制工单 UUID：root-1' }));
await waitFor(() => expect(writeText).toHaveBeenCalledWith('root-1'));
expect(onResult).toHaveBeenCalledWith('已复制 UUID：root-1');
```

Reject `writeText()` in a second test and assert `onResult('UUID 复制失败')`. Use a parent click spy and assert neither UUID path triggers it.

- [ ] **Step 2: Add failing workspace display/filter tests**

Give the root fixture `summary: '知识问答助手'`. Assert the ROOT lane card heading is `知识问答助手`, its body still contains the original title, and the filter checkbox is named `知识问答助手（root-1）`. Assert milestone and task headings remain unchanged.

- [ ] **Step 3: Run focused frontend tests and verify RED**

Run:

```bash
cd frontend/aios-main && npm test -- --run src/api/CopyableWorkItemId.test.tsx src/api/workspace.test.tsx
```

Expected: component import and summary/filter assertions fail.

- [ ] **Step 4: Implement the reusable copy control**

Use a native button with `stopPropagation()` on click and keyboard events, await `navigator.clipboard.writeText(id)`, call `onResult` with the exact success/failure strings, and render `#{id}`. Add hover and `:focus-visible` styles while retaining the current compact monospace UUID appearance.

- [ ] **Step 5: Integrate summary and clipboard feedback in the board**

In `ApiWorkspace`, derive filter roots from each loaded project's root item:

```ts
const root = project.resources.workItems.find((item) => item.kind === 'ROOT');
const name = root?.summary || root?.title || project.title;
return root ? [{ id: root.id, title: `${name}（${root.id}）` }] : [];
```

Render root headings with `item.summary || item.title || item.id`, replace each board UUID span with `CopyableWorkItemId`, and add a workspace `role="status"` region for the latest clipboard result. Keep the original title/objective in the card paragraph.

- [ ] **Step 6: Run focused frontend tests and verify GREEN**

Run the Task 4 frontend command again. Expected: all selected tests pass.

- [ ] **Step 7: Commit Task 4**

```bash
git add frontend/aios-main/src/api/CopyableWorkItemId.tsx frontend/aios-main/src/api/CopyableWorkItemId.test.tsx frontend/aios-main/src/api/ApiWorkspace.tsx frontend/aios-main/src/api/RootTaskFilter.tsx frontend/aios-main/src/api/workspace.test.tsx frontend/aios-main/src/index.css
git commit -m "feat: copy work item ids and show root summaries"
```

---

### Task 5: Add the structured project-requirement detail header

**Files:**
- Create: `frontend/aios-main/src/api/RootWorkItemDetail.tsx`
- Create: `frontend/aios-main/src/api/RootWorkItemDetail.test.tsx`
- Modify: `frontend/aios-main/src/api/ApiWorkspace.tsx`
- Modify: `frontend/aios-main/src/api/workspace.test.tsx`
- Modify: `frontend/aios-main/src/index.css`

**Interfaces:**
- Produces: `RootWorkItemDetail({ item, onCopyResult, children })`.
- Consumes: `CopyableWorkItemId` from Task 4 and the existing `PrdReviewPanel` as `children`.

- [ ] **Step 1: Add a failing component-order test**

Render a root with summary and a sentinel PRD child, then assert the labels and DOM order:

```ts
expect(screen.getByRole('heading', { name: '本地温度换算器' })).toBeTruthy();
expect(screen.getByText('交付一个仅在本机运行的单页温度换算器。')).toBeTruthy();
const article = screen.getByRole('article', { name: '项目需求信息' });
const text = article.textContent ?? '';
expect(text.indexOf('#root-1')).toBeLessThan(text.indexOf('本地温度换算器'));
expect(text.indexOf('本地温度换算器')).toBeLessThan(text.indexOf('详情'));
expect(text.indexOf('详情')).toBeLessThan(text.indexOf('PRD 审核占位'));
```

Add an integration assertion that opening the root card yields a dialog named `项目需求详情`, not `PRD 审核`.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
cd frontend/aios-main && npm test -- --run src/api/RootWorkItemDetail.test.tsx src/api/workspace.test.tsx
```

Expected: component import and dialog-name assertions fail.

- [ ] **Step 3: Implement `RootWorkItemDetail`**

Render an article containing, in order: the copyable UUID, `item.summary || item.title || item.id` as the main heading, a labeled `详情` block using `item.title || item.objective || item.description || '未提供项目需求详情'`, then the child PRD content. Style the top block consistently with the Milestone detail title bar without copying its dependency-monitor layout.

- [ ] **Step 4: Integrate the wrapper around both PRD states**

Change the root dialog title to `项目需求详情`. For a present spec, put the existing `PrdReviewPanel` inside `RootWorkItemDetail`. For a missing spec, put the existing `当前任务尚未生成 PRD。` state inside the same wrapper, so the UUID, summary, and original detail are always visible.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run the Task 5 frontend command again. Expected: component and workspace tests pass.

- [ ] **Step 6: Commit Task 5**

```bash
git add frontend/aios-main/src/api/RootWorkItemDetail.tsx frontend/aios-main/src/api/RootWorkItemDetail.test.tsx frontend/aios-main/src/api/ApiWorkspace.tsx frontend/aios-main/src/api/workspace.test.tsx frontend/aios-main/src/index.css
git commit -m "feat: add project requirement details"
```

---

### Task 6: Verify, backfill the local database, and preview

**Files:**
- No production files are modified by this verification task.

**Interfaces:**
- Consumes: all Task 1–5 deliverables.
- Produces: populated summaries for the current local database and a browser-visible UI verification.

- [ ] **Step 1: Run the complete backend suite**

```bash
cd backend && .venv/bin/pytest -q
```

Expected: all backend tests pass.

- [ ] **Step 2: Run complete frontend verification**

```bash
cd frontend/aios-main && npm test && npm run lint && npm run build
```

Expected: all tests pass, TypeScript reports no errors, and Vite produces `dist/`.

- [ ] **Step 3: Run static diff checks**

```bash
git diff --check
git status --short
```

Expected: no whitespace errors and only intentional files before the final commit.

- [ ] **Step 4: Back up and migrate the current database**

Resolve the configured SQLite path from backend settings, copy that single database file to a timestamped sibling backup, start the updated backend once so `init_database()` adds the nullable column, and retain the backup path in the delivery notes.

- [ ] **Step 5: Run the AI backfill command**

```bash
cd backend && .venv/bin/python -m app.scripts.backfill_root_summaries
```

Expected: JSON lists the current missing root project IDs under `updated`, preserves any IDs under `skipped`, and has an empty `failed` object. If failures exist, keep successful rows, report the failed IDs, and leave title fallbacks visible.

- [ ] **Step 6: Restart and inspect the local preview**

Open `http://localhost:3002/`, verify a root card uses its summary, copy UUID without opening the card, inspect `summary（UUID）` in the root filter, and open a root to verify UUID → summary → full detail → PRD review ordering.
