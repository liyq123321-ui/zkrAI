# Agent Runtime Activity Feed Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (\`- [ ]\`) syntax for tracking.

**Goal:** Add a Codex-style, collapsible live activity feed to every Agent shown in the Audit Trail runtime panel.

**Architecture:** A bounded \`AgentActivity\` timeline belongs to one \`AgentCall\`. A typed progress bridge receives service milestones and sanitized Codex JSONL events, persists safe activity data, and the existing runtime endpoint projects the latest call’s feed for the current two-second React polling UI.

**Tech Stack:** FastAPI, SQLAlchemy, Pydantic, pytest, React 19, TypeScript, Vitest, Testing Library, CSS.

## Global Constraints

- Preserve all current runtime-endpoint fields and status mappings.
- Persist and return only fixed activity kinds/titles, workspace-relative paths, tool categories, generated summaries, and text snippets of at most 1,000 characters.
- Never persist or render prompts, hidden reasoning, raw command strings or arguments, raw tool output, file bodies, environment variables, secrets, or stack traces.
- Unknown JSONL data falls back to a fixed generic activity without retaining the source payload.
- Paths must resolve inside the configured workspace; keep only newest 40 activities per call.
- Keep two-second polling, stale-snapshot retention, grouping, and metrics unchanged.
- Running feeds auto-expand; terminal feeds start collapsed. Do not add WebSocket/SSE, controls, or scheduler changes.

---

## File Structure

- \`backend/app/database/models.py\`: durable call-scoped activity model.
- \`backend/app/database/database.py\`: idempotent SQLite migration.
- \`backend/app/services/agent_activities.py\`: sanitization, persistence, and safe reads.
- \`backend/app/agents/progress.py\`: typed progress event and Agent-call binding.
- \`backend/app/agents/codex.py\`: safe JSONL mapping.
- \`backend/app/services/command_jobs.py\`: activity persistence with current command status updates.
- \`backend/app/services/{project_service,spec_service,decomposition_service,pm_agent}.py\`: bind each Agent invocation to its existing call.
- \`backend/app/services/query_service.py\`: runtime projection.
- \`backend/tests/unit/test_agent_activities.py\`, \`test_agent_gateway.py\`, \`test_database_schema.py\`: unit contracts.
- \`backend/tests/integration/test_command_jobs.py\`, \`test_sessions_api.py\`: integration and API contracts.
- \`frontend/aios-main/src/api/dto.ts\`, \`AgentRuntimePanel.tsx\`, \`AgentRuntimePanel.test.tsx\`, \`index.css\`: client contract, activity feed, tests, styles.

### Task 1: Add a bounded, safe activity store

**Files:**
- Modify: \`backend/app/database/models.py:95-108\`
- Modify: \`backend/app/database/database.py:20-39\`
- Create: \`backend/app/services/agent_activities.py\`
- Create: \`backend/tests/unit/test_agent_activities.py\`
- Modify: \`backend/tests/unit/test_database_schema.py\`

**Interfaces:**
- Produces \`AgentActivity(id, project_id, agent_call_id, sequence, status, kind, title, details, created_at, completed_at)\`.
- Produces \`AgentActivityInput(kind, title, details, status="completed")\`.
- Produces \`AgentActivityService.record(agent_call_id, input)\` and \`list_for_call_ids(call_ids) -> dict[str, list[AgentActivityRead]]\`.

- [ ] **Step 1: Write failing sanitizer and schema tests**

Create tests with the following assertions:

\`\`\`python
def test_activity_service_normalizes_paths_and_truncates_snippets(session_factory, tmp_path):
    service = AgentActivityService(session_factory, workspace_root=tmp_path)
    (tmp_path / "src").mkdir()
    service.record("call-1", AgentActivityInput(
        kind="file_write", title="正在修改文件",
        details={"path": str(tmp_path / "src" / "App.tsx"), "snippet": "x" * 1_500},
    ))
    assert service.list_for_call_ids(["call-1"])["call-1"][0].details == {
        "path": "src/App.tsx", "snippet": "x" * 1_000,
    }

def test_activity_service_discards_unsafe_data(session_factory, tmp_path):
    service = AgentActivityService(session_factory, workspace_root=tmp_path)
    service.record("call-1", AgentActivityInput(
        kind="tool", title="正在调用工作区工具",
        details={"tool": "shell", "command": "cat .env", "output": "secret", "path": "/etc/passwd"},
    ))
    activity = service.list_for_call_ids(["call-1"])["call-1"][0]
    assert activity.details == {"tool": "shell"}
    assert "secret" not in repr(activity)

def test_activity_service_retains_only_the_newest_forty(session_factory, tmp_path):
    service = AgentActivityService(session_factory, workspace_root=tmp_path)
    for index in range(41):
        service.record("call-1", AgentActivityInput(kind="workflow", title=f"步骤 {index}", details={}))
    activities = service.list_for_call_ids(["call-1"])["call-1"]
    assert len(activities) == 40
    assert (activities[0].title, activities[-1].title) == ("步骤 40", "步骤 1")
\`\`\`

Extend \`test_mvp_schema_contains_authoritative_records\` to require \`agent_activities\`. Test \`init_database()\` twice against legacy SQLite with \`agent_calls\`, asserting the table’s nine columns and unique \`(agent_call_id, sequence)\` constraint.

- [ ] **Step 2: Run test to verify it fails**

Run: \`cd backend && pytest tests/unit/test_agent_activities.py tests/unit/test_database_schema.py::test_mvp_schema_contains_authoritative_records -q\`

Expected: collection fails because the model and service do not exist.

- [ ] **Step 3: Implement the store**

Add after \`AgentCall\`:

\`\`\`python
class AgentActivity(Base):
    __tablename__ = "agent_activities"
    __table_args__ = (
        UniqueConstraint("agent_call_id", "sequence", name="uq_agent_activity_call_sequence"),
        Index("ix_agent_activity_call_sequence", "agent_call_id", "sequence"),
    )
    id = Column(String, primary_key=True)
    project_id = Column(String, nullable=False, index=True)
    agent_call_id = Column(String, nullable=False, index=True)
    sequence = Column(Integer, nullable=False)
    status = Column(String, nullable=False, default="completed")
    kind = Column(String, nullable=False)
    title = Column(String, nullable=False)
    details = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    completed_at = Column(DateTime(timezone=True), nullable=True)
\`\`\`

Call \`_migrate_sqlite_agent_activities()\` before \`Base.metadata.create_all()\`. Existing databases get missing nullable columns and \`CREATE INDEX IF NOT EXISTS\`; fresh databases use metadata. Admit only \`tool\`, \`path\`, \`summary\`, \`snippet\`; tool values are \`shell\`, \`read_file\`, \`write_file\`, \`search\`, \`patch\`, \`validation\`. Resolve paths under the workspace, truncate strings, allocate the next sequence transactionally, and delete rows below the newest 40.

- [ ] **Step 4: Run test to verify it passes**

Run: \`cd backend && pytest tests/unit/test_agent_activities.py tests/unit/test_database_schema.py -q\`

Expected: PASS.

- [ ] **Step 5: Commit**

\`\`\`bash
git add backend/app/database/models.py backend/app/database/database.py backend/app/services/agent_activities.py backend/tests/unit/test_agent_activities.py backend/tests/unit/test_database_schema.py
git commit -m "feat: persist safe agent activities"
\`\`\`

### Task 2: Bridge safe activity events to the active Agent call

**Files:**
- Modify: \`backend/app/agents/progress.py:1-39\`
- Modify: \`backend/app/agents/codex.py:285-367\`
- Modify: \`backend/app/services/command_jobs.py:184-257\`
- Modify: \`backend/app/services/project_service.py:270-285,629-655\`
- Modify: \`backend/app/services/spec_service.py:321-336,589-605,824-840,944-965,1132-1140\`
- Modify: \`backend/app/services/decomposition_service.py:461-545,658-757,1142-1150\`
- Modify: \`backend/app/services/pm_agent.py:519-530,613-625\`
- Modify: \`backend/tests/unit/test_agent_gateway.py\`
- Create: \`backend/tests/integration/test_command_jobs.py\`

**Interfaces:**
- Produces \`AgentProgress(stage, message, agent_call_id, activity)\`.
- Produces \`bind_agent_call(agent_call_id)\`; child async tasks retain its context.
- Keeps \`report_agent_progress(stage, message, *, activity=None)\` backward compatible for existing callers.

- [ ] **Step 1: Write failing bridge tests**

Add this JSONL mapping contract:

\`\`\`python
def test_jsonl_tool_item_becomes_safe_activity(tmp_path, monkeypatch):
    runner = CodexStructuredRunner(_settings(tmp_path, _write_fake_codex(tmp_path / "fake-codex")))
    reported = []
    monkeypatch.setattr(codex_module, "report_agent_progress", reported.append)
    runner._report_jsonl_progress(json.dumps({
        "type": "item.started",
        "item": {"type": "command_execution", "command": "python private.py --token nope"},
    }).encode())
    assert reported[0].activity.kind == "tool"
    assert reported[0].activity.details == {"tool": "shell"}
    assert "private.py" not in repr(reported[0])
\`\`\`

Create \`test_command_jobs.py::test_bound_progress_creates_activity_and_keeps_command_snapshot\`: create one processing \`CommandJob\` and one pending \`AgentCall\`; bind it; report \`AgentActivityInput(kind="validation", title="正在校验结构化结果", details={})\`; assert both job progress fields update and the activity service returns the new record.

- [ ] **Step 2: Run tests to verify they fail**

Run: \`cd backend && pytest tests/unit/test_agent_gateway.py::test_jsonl_tool_item_becomes_safe_activity tests/integration/test_command_jobs.py::test_bound_progress_creates_activity_and_keeps_command_snapshot -q\`

Expected: FAIL because activity context/persistence is missing.

- [ ] **Step 3: Implement typed progress and mapping**

Use an immutable event and call-ID \`ContextVar\`:

\`\`\`python
@dataclass(frozen=True)
class AgentProgress:
    stage: str
    message: str
    agent_call_id: str | None = None
    activity: AgentActivityInput | None = None

@contextmanager
def bind_agent_call(agent_call_id: str) -> Iterator[None]:
    token = _agent_call_id.set(agent_call_id)
    try:
        yield
    finally:
        _agent_call_id.reset(token)
\`\`\`

The command-job reporter updates its existing snapshot, then calls \`AgentActivityService.record\` only for a bound event with activity. Lifecycle events use fixed titles. Map only \`command_execution\`, \`file_read\`, \`file_write\`, \`search\`, \`apply_patch\`, and validation item types; take a path only from a dedicated \`item.path\`, never command/output/free text. Unknown events become \`正在处理任务上下文\`.

Wrap every listed \`await self._agent.<operation>(...)\`, including the \`asyncio.create_task\` call, in \`bind_agent_call(call_id)\`. Add explicit safe activities to the existing decomposition milestones while the producing/reviewer/planning call is bound; never attach one to a terminal call.

- [ ] **Step 4: Run tests to verify they pass**

Run: \`cd backend && pytest tests/unit/test_agent_gateway.py tests/integration/test_command_jobs.py -q\`

Expected: PASS including existing inactivity and command-job SSE coverage.

- [ ] **Step 5: Commit**

\`\`\`bash
git add backend/app/agents/progress.py backend/app/agents/codex.py backend/app/services/command_jobs.py backend/app/services/project_service.py backend/app/services/spec_service.py backend/app/services/decomposition_service.py backend/app/services/pm_agent.py backend/tests/unit/test_agent_gateway.py backend/tests/integration/test_command_jobs.py
git commit -m "feat: record live agent activities"
\`\`\`

### Task 3: Return only the latest call’s safe activity feed

**Files:**
- Modify: \`backend/app/services/query_service.py:145-165,311-394\`
- Modify: \`backend/tests/integration/test_sessions_api.py:625-865\`

**Interfaces:** Extends \`AgentRuntimeRead\` with \`activities: list[AgentActivityRead]\`, \`activity_count: int\`, and \`last_activity_at: datetime | None\`.

- [ ] **Step 1: Write failing HTTP test**

Add one safe activity to \`run-new\` and one to \`run-old\` in the existing runtime fixture. Assert the running Agent returns only \`run-new\` activity with count 1 and its timestamp. Add a wrong-project activity with \`agent_call_id="run-new"\` and assert it is excluded. Assert serialized response contains none of the fixture request, response, error, command, or secret values.

- [ ] **Step 2: Run test to verify it fails**

Run: \`cd backend && pytest tests/integration/test_sessions_api.py::test_agent_runtime_returns_latest_safe_status_per_started_agent -q\`

Expected: FAIL because activity fields are absent.

- [ ] **Step 3: Extend the query**

Define \`AgentActivityRead\` with only \`id\`, \`sequence\`, \`status\`, \`kind\`, \`title\`, \`details\`, \`created_at\`, and \`completed_at\`. After the current latest-call window query, bulk-select safe activity columns by both project and call IDs, ordered by call then descending sequence. Build the activity map separately so joins cannot multiply the existing call count.

- [ ] **Step 4: Run API tests**

Run: \`cd backend && pytest tests/integration/test_sessions_api.py -q\`

Expected: PASS.

- [ ] **Step 5: Commit**

\`\`\`bash
git add backend/app/services/query_service.py backend/tests/integration/test_sessions_api.py
git commit -m "feat: expose agent runtime activities"
\`\`\`

### Task 4: Render the Codex-style collapsible feed

**Files:**
- Modify: \`frontend/aios-main/src/api/dto.ts:121-139\`
- Modify: \`frontend/aios-main/src/api/AgentRuntimePanel.tsx:1-150\`
- Modify: \`frontend/aios-main/src/api/AgentRuntimePanel.test.tsx:1-190\`
- Modify: \`frontend/aios-main/src/index.css\` near \`.ff-agent-runtime\`

**Interfaces:** Adds \`AgentActivityDto\`, \`activities\`, \`activity_count\`, and \`last_activity_at\` to \`AgentRuntimeDto\`, plus \`ActivityFeed({ agent })\`.

- [ ] **Step 1: Write failing UI tests**

Use this running fixture:

\`\`\`ts
{ id:'activity-2', sequence:2, status:'running', kind:'tool', title:'正在调用工作区工具', details:{ tool:'shell', path:'backend/app/services/query_service.py' }, created_at:'2026-09-06T02:01:05Z', completed_at:null }
\`\`\`

Verify it renders with \`data-activity-status="running"\` and safe path by default. Give Writer Agent two activities; assert \`查看 Writer Agent 的 2 个活动\` initially hides details and reveals them after \`userEvent.click\`. Add malformed details \`{ command:'cat .env', path:'src/App.tsx' }\` and assert command text never renders.

- [ ] **Step 2: Run test to verify it fails**

Run: \`cd frontend/aios-main && npm test -- --run src/api/AgentRuntimePanel.test.tsx\`

Expected: FAIL because no activity DTO or disclosure UI exists.

- [ ] **Step 3: Implement accessible defensive rendering**

Render only \`tool\`, \`path\`, \`summary\`, and \`snippet\` in fixed order as plain text; never use \`dangerouslySetInnerHTML\`. A button with \`aria-expanded\` controls the feed. Running feeds start open and use \`aria-live="polite"\`; terminal feeds start closed with exact count button text. Keep state by \`agent_session_id\`, retaining an opened feed during polling. Preserve current operation/summary when no activities exist. Show \`刚刚更新\` or a formatted \`last_activity_at\`.

Add a vertical rail, active animation, details panel, disclosure button, and \`prefers-reduced-motion\` CSS fallback.

- [ ] **Step 4: Run frontend tests and build**

\`\`\`bash
cd frontend/aios-main && npm test -- --run src/api/AgentRuntimePanel.test.tsx src/api/workspace.test.tsx
cd frontend/aios-main && npm run build
\`\`\`

Expected: both commands exit 0.

- [ ] **Step 5: Commit**

\`\`\`bash
git add frontend/aios-main/src/api/dto.ts frontend/aios-main/src/api/AgentRuntimePanel.tsx frontend/aios-main/src/api/AgentRuntimePanel.test.tsx frontend/aios-main/src/index.css frontend/aios-main/src/api/workspace.test.tsx
git commit -m "feat: show collapsible agent activity feeds"
\`\`\`

### Task 5: Release verification

**Files:** Modify: \`CHANGELOG.md\`.

- [ ] **Step 1: Add release note**

Under unreleased \`Changed\`, add: \`审计记录的 Agent 实时状态现在显示可折叠的活动流；运行中的 Agent 自动展开当前活动。\`

- [ ] **Step 2: Run complete verification**

\`\`\`bash
cd backend && pytest tests/unit/test_agent_activities.py tests/unit/test_agent_gateway.py tests/unit/test_database_schema.py tests/integration/test_command_jobs.py tests/integration/test_sessions_api.py -q
cd frontend/aios-main && npm test -- --run src/api/AgentRuntimePanel.test.tsx src/api/workspace.test.tsx
cd frontend/aios-main && npm run build
git diff --check
\`\`\`

Expected: every command exits 0 and the diff check produces no output.

- [ ] **Step 3: Commit release note**

\`\`\`bash
git add CHANGELOG.md
git commit -m "docs: note agent activity feed"
\`\`\`

## Plan Self-Review

- Spec coverage: Task 1 creates bounded safe data; Task 2 connects every live source to the correct call; Task 3 exposes only safe latest-call activities; Task 4 delivers the expandable view; Task 5 verifies and documents the release.
- Placeholder scan: every task contains explicit files, interfaces, assertions, commands, and commits.
- Type consistency: \`AgentActivityInput\` flows through \`AgentProgress\` to \`AgentActivityService\`, becomes \`AgentActivityRead\`, then \`AgentActivityDto\`.
