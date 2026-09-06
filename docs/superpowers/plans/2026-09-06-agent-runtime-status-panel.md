# Agent Runtime Status Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a workspace-wide, project-grouped live Agent status panel above the audit trail.

**Architecture:** A new read-only session endpoint projects `AgentSession` plus its latest `AgentCall` into a safe runtime DTO. A focused React panel polls every two seconds only while the audit tab is mounted, preserves the last successful snapshot for projects whose refresh fails, and renders summary counts plus per-project Agent rows.

**Tech Stack:** FastAPI, SQLAlchemy, Pydantic, pytest, React 19, TypeScript, Vitest, Testing Library, CSS.

## Global Constraints

- Count one started Agent per persistent `AgentSession` that has at least one `AgentCall`.
- Expose only `running`, `completed`, or `error`; derive the status from the latest call ordered by `(started_at, id)`.
- Treat `RESULT_READY`, `SUCCEEDED`, `COMPLETED`, and `NO_CHANGE` as completed; treat `FAILED` and `AMBIGUOUS` as error; treat all other statuses as running.
- Cover all loaded workspace projects and group Agent rows by project.
- Poll every 2 seconds only while the audit Tab is active; abort work when the Tab unmounts or the project list changes.
- Preserve each failed project's last successful snapshot and display a non-blocking stale-data warning.
- Never expose Agent request bodies, response bodies, raw errors, stdout, stderr, or stack traces.
- Do not add Agent controls, WebSocket/SSE transport, scheduling changes, or workflow state mutations.

---

## File Structure

- `backend/app/services/query_service.py`: define the safe runtime read model, status/summary mapping, and project-scoped aggregation query.
- `backend/app/api/sessions.py`: expose the read-only runtime endpoint through the existing session router and error mapping.
- `backend/tests/integration/test_sessions_api.py`: verify aggregation, status mapping, project isolation, ordering, and response redaction at the HTTP boundary.
- `frontend/aios-main/src/api/dto.ts`: define the public TypeScript runtime DTO.
- `frontend/aios-main/src/api/sessions.ts`: add the typed runtime API request.
- `frontend/aios-main/src/api/AgentRuntimePanel.tsx`: own audit-only polling, stale snapshot retention, summary calculation, grouping, and presentation.
- `frontend/aios-main/src/api/AgentRuntimePanel.test.tsx`: verify rendering, polling lifecycle, partial failure behavior, and empty state.
- `frontend/aios-main/src/api/ApiWorkspace.tsx`: mount the runtime panel above `AuditTrail` only in the audit pane.
- `frontend/aios-main/src/api/workspace.test.tsx`: prove the workspace passes all loaded projects to the panel endpoint and keeps the audit trail usable.
- `frontend/aios-main/src/index.css`: style the summary metrics, project groups, Agent rows, statuses, empty state, and warning consistently with the existing FirstFlight workspace.

---

### Task 1: Add the project-scoped Agent runtime read API

**Files:**
- Modify: `backend/app/services/query_service.py:11-320`
- Modify: `backend/app/api/sessions.py:12-180`
- Test: `backend/tests/integration/test_sessions_api.py`

**Interfaces:**
- Consumes: `AgentSession`, `AgentCall`, and the existing `QueryService._project(db, session_id)` ownership check.
- Produces: `AgentRuntimeRead` and `QueryService.agent_runtime(session_id) -> list[AgentRuntimeRead]`.
- Produces: `GET /sessions/{session_id}/agents/runtime` returning `list[AgentRuntimeRead]`.

- [ ] **Step 1: Write failing HTTP tests for aggregation, mapping, redaction, and project isolation**

Import `AgentSession`, `datetime`, `timedelta`, `timezone`, `pytest`, and `QueryService`, then add one test that creates two projects, several sessions/calls, and an Agent with no calls:

```python
def test_agent_runtime_returns_latest_safe_status_per_started_agent(session_factory):
    with session_factory() as db:
        project = Project(
            id="runtime-project",
            session_id="runtime-session",
            brief={"final_objective": "Runtime dashboard"},
            final_approver="owner-1",
            project_manager_ids=["owner-1"],
            root_owner_ids=["owner-1"],
        )
        other = Project(
            id="other-project",
            session_id="other-session",
            brief={"final_objective": "Other"},
            final_approver="owner-1",
            project_manager_ids=["owner-1"],
            root_owner_ids=["owner-1"],
        )
        started = datetime(2026, 9, 6, 2, 0, tzinfo=timezone.utc)
        db.add_all([
            project,
            other,
            AgentSession(id="agent-running", project_id=project.id, role="PM", provider="codex", model="gpt-test", purpose="Plan work", created_at=started),
            AgentSession(id="agent-error", project_id=project.id, role="Reviewer", provider="codex", model=None, purpose="Review work", created_at=started + timedelta(seconds=1)),
            AgentSession(id="agent-unused", project_id=project.id, role="Unused", provider=None, model=None, purpose=None, created_at=started + timedelta(seconds=2)),
            AgentSession(id="agent-other", project_id=other.id, role="Other", provider=None, model=None, purpose=None, created_at=started),
            AgentCall(id="run-old", project_id=project.id, agent_session_id="agent-running", operation="analyze_brief", request={"secret": "do-not-return"}, response={"secret": "do-not-return"}, status="SUCCEEDED", started_at=started, completed_at=started + timedelta(seconds=10)),
            AgentCall(id="run-new", project_id=project.id, agent_session_id="agent-running", operation="decompose_spec", request={"secret": "do-not-return"}, status="PENDING", started_at=started + timedelta(minutes=1)),
            AgentCall(id="error-call", project_id=project.id, agent_session_id="agent-error", operation="review_spec", request={}, status="FAILED", error="private traceback", started_at=started + timedelta(minutes=2), completed_at=started + timedelta(minutes=3)),
            AgentCall(id="other-call", project_id=other.id, agent_session_id="agent-other", operation="generate_spec", request={}, status="RESULT_READY", started_at=started),
        ])
        db.commit()

    with TestClient(create_app(session_factory=session_factory)) as client:
        response = client.get("/sessions/runtime-session/agents/runtime")

    assert response.status_code == 200
    assert response.json() == [
        {
            "agent_session_id": "agent-running",
            "project_id": "runtime-project",
            "role": "PM",
            "provider": "codex",
            "model": "gpt-test",
            "purpose": "Plan work",
            "status": "running",
            "current_operation": "decompose_spec",
            "current_summary": "Decomposing the approved specification",
            "current_call_id": "run-new",
            "started_at": "2026-09-06T02:01:00Z",
            "completed_at": None,
            "call_count": 2,
        },
        {
            "agent_session_id": "agent-error",
            "project_id": "runtime-project",
            "role": "Reviewer",
            "provider": "codex",
            "model": None,
            "purpose": "Review work",
            "status": "error",
            "current_operation": "review_spec",
            "current_summary": "Reviewing specification quality",
            "current_call_id": "error-call",
            "started_at": "2026-09-06T02:02:00Z",
            "completed_at": "2026-09-06T02:03:00Z",
            "call_count": 1,
        },
    ]
    serialized = response.text
    assert "do-not-return" not in serialized
    assert "private traceback" not in serialized
    assert "agent-unused" not in serialized
    assert "agent-other" not in serialized
```

Add a parameterized status contract test against `QueryService._agent_runtime_status`:

```python
@pytest.mark.parametrize(
    ("stored", "public"),
    [
        ("PENDING", "running"),
        ("PREPARING", "running"),
        ("RESULT_READY", "completed"),
        ("SUCCEEDED", "completed"),
        ("COMPLETED", "completed"),
        ("NO_CHANGE", "completed"),
        ("FAILED", "error"),
        ("AMBIGUOUS", "error"),
    ],
)
def test_agent_runtime_status_mapping_is_stable(stored, public):
    assert QueryService._agent_runtime_status(stored) == public
```

- [ ] **Step 2: Run the new backend tests and confirm they fail**

Run:

```bash
cd backend
pytest tests/integration/test_sessions_api.py::test_agent_runtime_returns_latest_safe_status_per_started_agent tests/integration/test_sessions_api.py::test_agent_runtime_status_mapping_is_stable -q
```

Expected: collection or assertion failure because `AgentRuntimeRead`, `_agent_runtime_status`, and the route do not exist.

- [ ] **Step 3: Implement the safe read model and shared operation summary mapping**

Import `AgentSession`, define the summary map once, and add the read model:

```python
AGENT_OPERATION_SUMMARIES = {
    "analyze_brief": "Analyzing project brief completeness",
    "generate_spec": "Generating a project specification",
    "review_spec": "Reviewing specification quality",
    "decompose_spec": "Decomposing the approved specification",
    "plan_task": "Defining task implementation steps and interfaces",
    "review_breakdown": "Reviewing work-item decomposition",
    "rewrite_prd": "Applying authoritative PRD review comments",
    "restore_spec": "Restoring historical specification content",
}


class AgentRuntimeRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_session_id: str
    project_id: str
    role: str
    provider: str | None
    model: str | None
    purpose: str | None
    status: str
    current_operation: str
    current_summary: str
    current_call_id: str
    started_at: datetime
    completed_at: datetime | None
    call_count: int
```

Implement the project-scoped aggregation without loading or returning sensitive call fields:

```python
def agent_runtime(self, session_id: str) -> list[AgentRuntimeRead]:
    with self._session_factory() as db:
        project = self._project(db, session_id)
        sessions = (
            db.query(AgentSession)
            .filter_by(project_id=project.id)
            .order_by(AgentSession.created_at, AgentSession.id)
            .all()
        )
        calls = (
            db.query(AgentCall)
            .filter_by(project_id=project.id)
            .order_by(AgentCall.started_at, AgentCall.id)
            .all()
        )
        calls_by_session: dict[str, list[AgentCall]] = {}
        for call in calls:
            calls_by_session.setdefault(call.agent_session_id, []).append(call)
        result: list[AgentRuntimeRead] = []
        for agent in sessions:
            agent_calls = calls_by_session.get(agent.id, [])
            if not agent_calls:
                continue
            latest = agent_calls[-1]
            result.append(AgentRuntimeRead(
                agent_session_id=agent.id,
                project_id=project.id,
                role=agent.role,
                provider=agent.provider,
                model=agent.model,
                purpose=agent.purpose,
                status=self._agent_runtime_status(latest.status),
                current_operation=latest.operation,
                current_summary=self._agent_operation_summary(latest.operation),
                current_call_id=latest.id,
                started_at=latest.started_at,
                completed_at=latest.completed_at,
                call_count=len(agent_calls),
            ))
        return result

@staticmethod
def _agent_runtime_status(status: str) -> str:
    if status in {"FAILED", "AMBIGUOUS"}:
        return "error"
    if status in {"RESULT_READY", "SUCCEEDED", "COMPLETED", "NO_CHANGE"}:
        return "completed"
    return "running"

@staticmethod
def _agent_operation_summary(operation: str) -> str:
    return AGENT_OPERATION_SUMMARIES.get(operation, "Processing an Agent stage")
```

Replace the local `summaries` dictionary inside `_agent_trace_read` with `cls._agent_operation_summary(call.operation)` so the audit trail and runtime endpoint use identical copy.

- [ ] **Step 4: Add the FastAPI route**

Import `AgentRuntimeRead` from `query_service` and add the route before the parameterized `/{session_id}/agent-specs/{agent_spec_id}` route:

```python
@router.get("/{session_id}/agents/runtime", response_model=list[AgentRuntimeRead])
def list_agent_runtime(session_id: str) -> list[AgentRuntimeRead]:
    try:
        return queries.agent_runtime(session_id)
    except Exception as error:
        raise http_error(error) from error
```

- [ ] **Step 5: Run the focused and full session API tests**

Run:

```bash
cd backend
pytest tests/integration/test_sessions_api.py -q
```

Expected: all tests pass, including the new runtime endpoint coverage.

- [ ] **Step 6: Commit the backend contract**

```bash
git add backend/app/services/query_service.py backend/app/api/sessions.py backend/tests/integration/test_sessions_api.py
git commit -m "feat: expose agent runtime status"
```

---

### Task 2: Build the runtime panel and its polling lifecycle

**Files:**
- Modify: `frontend/aios-main/src/api/dto.ts:106-123`
- Modify: `frontend/aios-main/src/api/sessions.ts:1-92`
- Create: `frontend/aios-main/src/api/AgentRuntimePanel.tsx`
- Create: `frontend/aios-main/src/api/AgentRuntimePanel.test.tsx`

**Interfaces:**
- Consumes: `GET /sessions/{session_id}/agents/runtime` from Task 1.
- Produces: `AgentRuntimeDto` and `listAgentRuntime(sessionId, signal)`.
- Produces: `<AgentRuntimePanel projects: Array<{ sessionId: string; title: string }>>`.

- [ ] **Step 1: Write failing component tests for summary, grouping, and empty state**

Create `AgentRuntimePanel.test.tsx` with jsdom, mocked fetch, and a deterministic runtime sample:

```tsx
// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import { AgentRuntimePanel } from './AgentRuntimePanel';

const runtime = [
  {agent_session_id:'agent-pm',project_id:'project-1',role:'PM Agent',provider:'codex',model:'gpt-test',purpose:'拆解需求',status:'running',current_operation:'decompose_spec',current_summary:'Decomposing the approved specification',current_call_id:'call-2',started_at:'2026-09-06T02:01:00Z',completed_at:null,call_count:2},
  {agent_session_id:'agent-reviewer',project_id:'project-1',role:'Reviewer Agent',provider:'codex',model:null,purpose:'审核方案',status:'error',current_operation:'review_spec',current_summary:'Reviewing specification quality',current_call_id:'call-3',started_at:'2026-09-06T02:02:00Z',completed_at:'2026-09-06T02:03:00Z',call_count:1},
];

beforeEach(() => vi.stubGlobal('fetch', vi.fn(async () =>
  new Response(JSON.stringify(runtime), {status: 200}))));
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });

describe('AgentRuntimePanel', () => {
  it('summarizes and groups the latest Agent runtime snapshot', async () => {
    render(<AgentRuntimePanel projects={[{sessionId:'session-1', title:'知识问答'}]} />);
    const panel = await screen.findByRole('region', {name:'Agent 实时运行状态'});
    expect(within(panel).getByText('已启动')).toBeTruthy();
    expect(within(panel).getByText('2')).toBeTruthy();
    expect(within(panel).getByText('知识问答')).toBeTruthy();
    expect(within(panel).getByText('PM Agent')).toBeTruthy();
    expect(within(panel).getByText('运行中')).toBeTruthy();
    expect(within(panel).getByText('Reviewer Agent')).toBeTruthy();
    expect(within(panel).getByText('异常')).toBeTruthy();
    expect(within(panel).getByText('拆解已批准的项目规格')).toBeTruthy();
  });

  it('renders an explicit empty state', async () => {
    vi.mocked(fetch).mockResolvedValue(new Response('[]', {status: 200}));
    render(<AgentRuntimePanel projects={[{sessionId:'session-1', title:'空项目'}]} />);
    expect(await screen.findByText('暂无已启动 Agent')).toBeTruthy();
  });
});
```

Use unique metric labels or `getAllByText` where repeated status text would make an assertion ambiguous.

- [ ] **Step 2: Write failing polling and partial-failure tests**

Add tests using fake timers to prove immediate load, delayed refresh, unmount cancellation, and stale retention:

```tsx
it('polls after two seconds and stops after unmount', async () => {
  vi.useFakeTimers();
  const view = render(<AgentRuntimePanel projects={[{sessionId:'session-1', title:'知识问答'}]} />);
  await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
  await vi.advanceTimersByTimeAsync(1999);
  expect(fetch).toHaveBeenCalledTimes(1);
  await vi.advanceTimersByTimeAsync(1);
  await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
  view.unmount();
  await vi.advanceTimersByTimeAsync(2000);
  expect(fetch).toHaveBeenCalledTimes(2);
});

it('keeps the previous project snapshot when only that project refresh fails', async () => {
  vi.useFakeTimers();
  const fetchMock = vi.mocked(fetch);
  fetchMock
    .mockResolvedValueOnce(new Response(JSON.stringify(runtime), {status:200}))
    .mockResolvedValueOnce(new Response('[]', {status:200}));
  render(<AgentRuntimePanel projects={[
    {sessionId:'session-1', title:'知识问答'},
    {sessionId:'session-2', title:'空项目'},
  ]} />);
  expect(await screen.findByText('PM Agent')).toBeTruthy();
  fetchMock
    .mockResolvedValueOnce(new Response(JSON.stringify({detail:{code:'TEMPORARILY_UNAVAILABLE',message:'retry'}}), {status:503}))
    .mockResolvedValueOnce(new Response('[]', {status:200}));
  await vi.advanceTimersByTimeAsync(2000);
  await waitFor(() => expect(screen.getByText(/知识问答.*最近一次成功快照/)).toBeTruthy());
  expect(screen.getByText('PM Agent')).toBeTruthy();
});
```

Also capture each request's `AbortSignal` and assert it becomes aborted after unmount.

- [ ] **Step 3: Run the component tests and confirm they fail**

Run:

```bash
cd frontend/aios-main
npm test -- src/api/AgentRuntimePanel.test.tsx
```

Expected: fail because the component and DTO/API functions do not exist.

- [ ] **Step 4: Add the DTO and typed API function**

Add to `dto.ts`:

```ts
export type AgentRuntimeStatus = 'running' | 'completed' | 'error';

export interface AgentRuntimeDto {
  agent_session_id: string;
  project_id: string;
  role: string;
  provider: string | null;
  model: string | null;
  purpose: string | null;
  status: AgentRuntimeStatus;
  current_operation: string;
  current_summary: string;
  current_call_id: string;
  started_at: string;
  completed_at: string | null;
  call_count: number;
}
```

Import the type in `sessions.ts` and add:

```ts
export const listAgentRuntime = (sessionId: string, signal?: AbortSignal) =>
  apiClient.request<AgentRuntimeDto[]>(`/sessions/${sessionId}/agents/runtime`, { signal });
```

- [ ] **Step 5: Implement the panel with recursive polling and per-project snapshot retention**

Create `AgentRuntimePanel.tsx`. Keep network lifecycle inside this component so `ApiWorkspace` only decides whether the panel is mounted. The essential state and effect are:

```tsx
const POLL_INTERVAL_MS = 2_000;
type RuntimeProject = { sessionId: string; title: string };

export function AgentRuntimePanel({ projects }: { projects: RuntimeProject[] }) {
  const [snapshots, setSnapshots] = useState<Record<string, AgentRuntimeDto[]>>({});
  const [failedIds, setFailedIds] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let disposed = false;
    let timer: number | undefined;
    const controllers = new Set<AbortController>();

    const refresh = async () => {
      const settled = await Promise.allSettled(projects.map(async (project) => {
        const controller = new AbortController();
        controllers.add(controller);
        try {
          return {project, agents: await listAgentRuntime(project.sessionId, controller.signal)};
        } finally {
          controllers.delete(controller);
        }
      }));
      if (disposed) return;
      const failures: string[] = [];
      setSnapshots((previous) => {
        const next = {...previous};
        settled.forEach((result, index) => {
          if (result.status === 'fulfilled') next[result.value.project.sessionId] = result.value.agents;
          else failures.push(projects[index].sessionId);
        });
        return next;
      });
      setFailedIds(failures);
      setLoading(false);
      timer = window.setTimeout(refresh, POLL_INTERVAL_MS);
    };

    void refresh();
    return () => {
      disposed = true;
      if (timer !== undefined) window.clearTimeout(timer);
      controllers.forEach((controller) => controller.abort());
    };
  }, [projects]);
```

Avoid an unstable array dependency by having the caller memoize `projects`. Render and count only keys present in the current `projects` prop, so removed projects cannot remain visible from an old snapshot.

Map operation copy to Chinese in the component using a complete known-operation record plus a generic fallback:

```ts
const operationLabels: Record<string, string> = {
  analyze_brief: '分析项目需求完整性',
  generate_spec: '生成项目规格',
  review_spec: '审核项目规格质量',
  decompose_spec: '拆解已批准的项目规格',
  plan_task: '生成任务实施步骤与接口',
  review_breakdown: '审核任务拆解结果',
  rewrite_prd: '根据审核批注修订 PRD',
  restore_spec: '恢复历史项目规格',
};
```

Use semantic markup:

```tsx
<section className="ff-agent-runtime" aria-label="Agent 实时运行状态">
  <header className="ff-agent-runtime-header">...</header>
  <dl className="ff-agent-runtime-metrics">...</dl>
  {failedIds.length > 0 && <div role="status" className="ff-agent-runtime-warning">...</div>}
  <div className="ff-agent-runtime-projects">...</div>
</section>
```

For a running Agent, show elapsed time from `Date.now() - Date.parse(started_at)`; for a completed/error Agent, show `displayTime(completed_at)`. Recalculate on every successful or failed polling cycle. Use `displayTime` for timestamps and do not render `current_call_id` as the primary label.

- [ ] **Step 6: Run the component tests**

Run:

```bash
cd frontend/aios-main
npm test -- src/api/AgentRuntimePanel.test.tsx
```

Expected: all Agent runtime panel tests pass.

- [ ] **Step 7: Commit the focused panel implementation**

```bash
git add frontend/aios-main/src/api/dto.ts frontend/aios-main/src/api/sessions.ts frontend/aios-main/src/api/AgentRuntimePanel.tsx frontend/aios-main/src/api/AgentRuntimePanel.test.tsx
git commit -m "feat: add live agent runtime panel"
```

---

### Task 3: Mount and style the panel in the audit workspace

**Files:**
- Modify: `frontend/aios-main/src/api/ApiWorkspace.tsx:1-260,991-995`
- Modify: `frontend/aios-main/src/api/workspace.test.tsx`
- Modify: `frontend/aios-main/src/index.css:1838-1844`

**Interfaces:**
- Consumes: `<AgentRuntimePanel projects>` from Task 2 and existing `projectTitle(project)`.
- Produces: an audit Tab that mounts one polling panel above the existing `<AuditTrail>`.

- [ ] **Step 1: Extend the workspace fixture and write a failing integration test**

Add a runtime payload to the shared fetch fixture:

```ts
'/sessions/session-1/agents/runtime':[
  {agent_session_id:'agent-pm',project_id:'project-1',role:'PM Agent',provider:'codex',model:'gpt-test',purpose:'拆解需求',status:'running',current_operation:'decompose_spec',current_summary:'Decomposing the approved specification',current_call_id:'runtime-call-1',started_at:'2026-09-06T02:01:00Z',completed_at:null,call_count:2},
],
```

Add:

```tsx
it('shows live Agent status above the audit records only while the audit tab is active', async () => {
  localStorage.setItem('firstflight.active-session-id','session-1');
  render(<ApiWorkspace />);
  expect(screen.queryByRole('region', {name:'Agent 实时运行状态'})).toBeNull();

  fireEvent.click(await screen.findByRole('button', {name:'打开审计记录'}));
  const panel = await screen.findByRole('region', {name:'Agent 实时运行状态'});
  const auditSummary = screen.getByText('安全审计摘要');
  expect(panel.compareDocumentPosition(auditSummary) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(within(panel).getByText('PM Agent')).toBeTruthy();
  expect(fetchSpy.mock.calls.some(([input]) => String(input).endsWith('/sessions/session-1/agents/runtime'))).toBe(true);

  fireEvent.click(screen.getByRole('button', {name:/Kanban Board/}));
  expect(screen.queryByRole('region', {name:'Agent 实时运行状态'})).toBeNull();
});
```

- [ ] **Step 2: Run the workspace test and confirm it fails**

Run:

```bash
cd frontend/aios-main
npm test -- src/api/workspace.test.tsx
```

Expected: fail because `ApiWorkspace` does not mount `AgentRuntimePanel`.

- [ ] **Step 3: Memoize project descriptors and mount the panel only for the active audit Tab**

Import `AgentRuntimePanel`, create stable descriptors, and conditionally mount it:

```tsx
const runtimeProjects = useMemo(() => projectList.map((project) => ({
  sessionId: project.state.session_id,
  title: projectTitle(project),
})), [projectList]);
```

```tsx
<div className="ff-audit-scroll">
  {activeTab === 'audit' && error && <div role="alert" className="ff-page-alert ff-page-alert-error">...</div>}
  {activeTab === 'audit' && <AgentRuntimePanel projects={runtimeProjects} />}
  <AuditTrail events={allResources.events} specs={allResources.specs} />
</div>
```

Conditional mounting is required: leaving the audit Tab must unmount the component and invoke its abort/timeout cleanup.

- [ ] **Step 4: Add responsive workspace-native styles**

Add focused `ff-agent-runtime-*` rules adjacent to `.ff-audit-scroll`:

```css
.ff-agent-runtime {
  margin-bottom: 14px;
  overflow: hidden;
  border: 1px solid #cbd6e1;
  border-radius: 10px;
  background: #fff;
  color: #263244;
}

.ff-agent-runtime-metrics {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
}

.ff-agent-runtime-projects,
.ff-agent-runtime-agent-list {
  display: grid;
  gap: 10px;
}

.ff-agent-runtime-agent {
  display: grid;
  grid-template-columns: minmax(150px, .8fr) minmax(220px, 1.4fr) auto;
  gap: 12px;
  align-items: center;
}

@media (max-width: 760px) {
  .ff-agent-runtime-metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .ff-agent-runtime-agent { grid-template-columns: 1fr; }
}
```

Add status modifiers for `.is-running`, `.is-completed`, and `.is-error`, a subdued warning, and compact typography matching the surrounding workspace. Do not reuse the dark Tailwind audit card as the runtime panel's primary theme; the surrounding application shell is light.

- [ ] **Step 5: Run frontend component, workspace, type, and build checks**

Run:

```bash
cd frontend/aios-main
npm test -- src/api/AgentRuntimePanel.test.tsx src/api/workspace.test.tsx
npm run lint
npm run build
```

Expected: all tests pass, TypeScript reports no errors, and Vite builds successfully.

- [ ] **Step 6: Run the complete backend suite**

Run:

```bash
cd backend
pytest -q
```

Expected: the complete backend suite passes.

- [ ] **Step 7: Review the final diff for scope and sensitive fields**

Run:

```bash
git diff --check
git diff -- backend/app/services/query_service.py backend/app/api/sessions.py frontend/aios-main/src/api/AgentRuntimePanel.tsx frontend/aios-main/src/api/ApiWorkspace.tsx
rg -n "request|response|error|stderr|stdout|traceback" frontend/aios-main/src/api/AgentRuntimePanel.tsx
```

Expected: no whitespace errors; the panel consumes only the safe DTO and does not render sensitive fields.

- [ ] **Step 8: Commit the audit workspace integration**

```bash
git add frontend/aios-main/src/api/ApiWorkspace.tsx frontend/aios-main/src/api/workspace.test.tsx frontend/aios-main/src/index.css
git commit -m "feat: show agent runtime in audit tab"
```
