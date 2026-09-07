# Task Flow Independent Filters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Kanban-style filter bar to the task flow tab while preserving separate filter state for Kanban and task flow.

**Architecture:** Extract a pure WorkItem filtering function and a controlled filter-controls component, then let `ApiWorkspace` own one filter state object per tab. Build the task DAG projects from the flow-filtered WorkItems so hidden nodes and their dependency edges disappear together.

**Tech Stack:** React 19, TypeScript 5.8, Vitest 4, Testing Library, Vite 6, existing CSS and `RootTaskFilter`.

## Global Constraints

- Start execution from the current commit based on `origin/main` commit `417e702`.
- Kanban and task flow keep independent search, root-task, type, and agent filters.
- Do not add backend APIs, dependencies, local-storage persistence, or WorkItem schema changes.
- Preserve current Kanban defaults, task-detail navigation, and the complete task flow when all flow filters are at their defaults.
- Draw a dependency edge only when both endpoint tasks remain visible.
- Follow red-green-refactor: every production behavior change starts with a failing test.

## File Structure

- Create `frontend/aios-main/src/api/workItemFilters.ts`: filter state types, defaults, and the pure WorkItem filtering function.
- Create `frontend/aios-main/src/api/workItemFilters.test.ts`: focused unit tests for filter intersection semantics.
- Create `frontend/aios-main/src/api/WorkItemFilterControls.tsx`: controlled search, root, type, and agent controls shared by both tabs.
- Modify `frontend/aios-main/src/api/ApiWorkspace.tsx`: own independent tab state, derive visible collections, render both filter bars, and omit empty flow project lanes.
- Modify `frontend/aios-main/src/api/workspace.test.tsx`: integration tests for independent state, all flow filters, empty results, and detail navigation.
- Create `frontend/aios-main/src/api/TaskDependencyGraph.test.tsx`: prove filtered task sets do not render partial dependency edges.
- Modify `frontend/aios-main/src/index.css`: generalize the current toolbar styling and add the flow empty-result presentation.
- Modify `frontend/aios-main/CODEX_ARCHITECTURE_AND_API_SPEC.md`: document shared controls with independent per-tab state.

---

### Task 1: Pure WorkItem Filter Semantics

**Files:**
- Create: `frontend/aios-main/src/api/workItemFilters.ts`
- Create: `frontend/aios-main/src/api/workItemFilters.test.ts`

**Interfaces:**
- Consumes: `WorkItemDto` from `./dto` and a `ReadonlyMap<string, string>` mapping each WorkItem ID to its project root ID.
- Produces: `WorkItemFilterState`, `defaultWorkItemFilters()`, and `filterWorkItems(items, state, rootIdByItem, assigneeForItem): WorkItemDto[]`.

- [ ] **Step 1: Write failing tests for defaults and intersection semantics**

```ts
// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import type { WorkItemDto } from './dto';
import { defaultWorkItemFilters, filterWorkItems } from './workItemFilters';

const items = [
  { id: 'root-a', kind: 'ROOT', title: '知识问答', parent_id: null, dependency_work_item_ids: [], suggested_assignee: 'Owner' },
  { id: 'task-a', kind: 'TASK', title: '构建检索流程', objective: '接入向量检索', parent_id: 'root-a', dependency_work_item_ids: [], suggested_assignee: 'AI Agent' },
  { id: 'task-b', kind: 'TASK', title: '客服接口', objective: '处理客户工单', parent_id: 'root-b', dependency_work_item_ids: [], suggested_assignee: 'Backend Agent' },
] as WorkItemDto[];

const roots = new Map([['root-a', 'root-a'], ['task-a', 'root-a'], ['task-b', 'root-b']]);
const assignee = (item: WorkItemDto) => item.suggested_assignee || '待分配 Agent';

describe('filterWorkItems', () => {
  it('shows every WorkItem with default filters', () => {
    expect(filterWorkItems(items, defaultWorkItemFilters(), roots, assignee).map((item) => item.id))
      .toEqual(['root-a', 'task-a', 'task-b']);
  });

  it('intersects root, kind, assignee, and text filters', () => {
    expect(filterWorkItems(items, {
      search: '向量', selectedRootIds: ['root-a'], kind: 'TASK', agent: 'AI Agent',
    }, roots, assignee).map((item) => item.id)).toEqual(['task-a']);
  });
});
```

- [ ] **Step 2: Run the focused test and verify the missing-module failure**

Run: `npm test -- src/api/workItemFilters.test.ts`

Expected: FAIL because `./workItemFilters` does not exist.

- [ ] **Step 3: Implement the minimal filter module**

```ts
import type { WorkItemDto } from './dto';

export type WorkItemKindFilter = 'ALL' | NonNullable<WorkItemDto['kind']>;
export type WorkItemFilterState = {
  search: string;
  selectedRootIds: string[] | null;
  kind: WorkItemKindFilter;
  agent: string;
};

export function defaultWorkItemFilters(): WorkItemFilterState {
  return { search: '', selectedRootIds: null, kind: 'ALL', agent: 'ALL' };
}

export function filterWorkItems(
  items: WorkItemDto[],
  filters: WorkItemFilterState,
  rootIdByItem: ReadonlyMap<string, string>,
  assigneeForItem: (item: WorkItemDto) => string,
): WorkItemDto[] {
  const query = filters.search.trim().toLowerCase();
  return items.filter((item) => {
    const assignee = assigneeForItem(item);
    if (filters.selectedRootIds !== null && !filters.selectedRootIds.includes(rootIdByItem.get(item.id) || '')) return false;
    if (filters.kind !== 'ALL' && item.kind !== filters.kind) return false;
    if (filters.agent !== 'ALL' && assignee !== filters.agent) return false;
    return !query || [item.id, item.title, item.objective, item.description, assignee]
      .filter(Boolean).some((value) => String(value).toLowerCase().includes(query));
  });
}
```

- [ ] **Step 4: Run the focused test and verify it passes**

Run: `npm test -- src/api/workItemFilters.test.ts`

Expected: 2 tests PASS.

- [ ] **Step 5: Commit the pure filtering unit**

```bash
git add frontend/aios-main/src/api/workItemFilters.ts frontend/aios-main/src/api/workItemFilters.test.ts
git commit -m "refactor: extract work item filtering"
```

---

### Task 2: Shared Controlled Filter Bar and Independent Tab State

**Files:**
- Create: `frontend/aios-main/src/api/WorkItemFilterControls.tsx`
- Modify: `frontend/aios-main/src/api/ApiWorkspace.tsx`
- Modify: `frontend/aios-main/src/api/workspace.test.tsx`
- Modify: `frontend/aios-main/src/index.css`

**Interfaces:**
- Consumes: `WorkItemFilterState` and `WorkItemKindFilter` from `./workItemFilters`, plus `RootTaskFilter`.
- Produces: `WorkItemFilterControls({ value, roots, agents, onChange })`, a controlled component that never stores business filter state.

- [ ] **Step 1: Write a failing integration test for independent state**

```ts
it('keeps Kanban and task flow filters independent across tab switches', async () => {
  localStorage.setItem('firstflight.active-session-id', 'session-1');
  render(<ApiWorkspace />);

  const kanbanSearch = await screen.findByRole('textbox', { name: '搜索工单' });
  fireEvent.change(kanbanSearch, { target: { value: '问答' } });
  fireEvent.click(screen.getByRole('button', { name: /DAG Flow Map/ }));

  const flowSearch = screen.getByRole('textbox', { name: '搜索流转图工单' });
  expect((flowSearch as HTMLInputElement).value).toBe('');
  fireEvent.change(flowSearch, { target: { value: '检索' } });

  fireEvent.click(screen.getByRole('button', { name: /Kanban Board/ }));
  expect((screen.getByRole('textbox', { name: '搜索工单' }) as HTMLInputElement).value).toBe('问答');
  fireEvent.click(screen.getByRole('button', { name: /DAG Flow Map/ }));
  expect((screen.getByRole('textbox', { name: '搜索流转图工单' }) as HTMLInputElement).value).toBe('检索');
});
```

- [ ] **Step 2: Run the integration test and verify the missing-flow-control failure**

Run: `npm test -- src/api/workspace.test.tsx -t "keeps Kanban and task flow filters independent"`

Expected: FAIL because no textbox named `搜索流转图工单` exists.

- [ ] **Step 3: Create the controlled shared component**

```tsx
import { Search } from 'lucide-react';
import { RootTaskFilter } from './RootTaskFilter';
import type { WorkItemFilterState, WorkItemKindFilter } from './workItemFilters';

export function WorkItemFilterControls({ value, roots, agents, searchLabel, onChange }: {
  value: WorkItemFilterState;
  roots: Array<{ id: string; title: string }>;
  agents: string[];
  searchLabel: string;
  onChange: (value: WorkItemFilterState) => void;
}) {
  const patch = (next: Partial<WorkItemFilterState>) => onChange({ ...value, ...next });
  return <>
    <label className="ff-search-box">
      <Search aria-hidden="true" />
      <input aria-label={searchLabel} value={value.search} onChange={(event) => patch({ search: event.target.value })} placeholder="搜索工单 ID、标题或 Agent…" />
    </label>
    <RootTaskFilter roots={roots} selectedIds={value.selectedRootIds} onChange={(selectedRootIds) => patch({ selectedRootIds })} />
    <label className="ff-filter-label">类型：
      <select aria-label={`${searchLabel}类型`} value={value.kind} onChange={(event) => patch({ kind: event.target.value as WorkItemKindFilter })}>
        <option value="ALL">全部类型</option><option value="ROOT">项目需求</option>
        <option value="MILESTONE">里程碑</option><option value="TASK">子任务</option>
      </select>
    </label>
    <label className="ff-filter-label">执行者：
      <select aria-label={`${searchLabel}执行者`} value={value.agent} onChange={(event) => patch({ agent: event.target.value })}>
        <option value="ALL">所有 Agent</option>
        {agents.map((agent) => <option key={agent}>{agent}</option>)}
      </select>
    </label>
  </>;
}
```

- [ ] **Step 4: Replace the Kanban controls and add separate flow state**

In `ApiWorkspace`, replace the four scalar Kanban state hooks with two state objects:

```ts
const [kanbanFilters, setKanbanFilters] = useState(defaultWorkItemFilters);
const [flowFilters, setFlowFilters] = useState(defaultWorkItemFilters);
```

Render `WorkItemFilterControls` inside the existing Kanban toolbar with `searchLabel="搜索工单"`, and render a second instance below `.ff-flow-toolbar` with `searchLabel="搜索流转图工单"`. Pass `setKanbanFilters` and `setFlowFilters` respectively. Update assignee-preview changes to reset only `kanbanFilters.agent` to `ALL`, preserving existing Kanban behavior.

Normalize each tab's agent selection when `availableAgents` changes so a removed agent falls back to `ALL` without altering the other three fields:

```ts
useEffect(() => {
  const keepValidAgent = (current: WorkItemFilterState) => current.agent === 'ALL' || availableAgents.includes(current.agent)
    ? current : { ...current, agent: 'ALL' };
  setKanbanFilters(keepValidAgent);
  setFlowFilters(keepValidAgent);
}, [availableAgents]);
```

- [ ] **Step 5: Generalize toolbar styling without changing layout**

Use `.ff-filter-toolbar` for the shared flex, spacing, input, and select rules. Keep `.ff-board-toolbar` only for Kanban-specific placement, and give the task-flow instance `className="ff-filter-toolbar ff-flow-filter-toolbar"` with the same white background and border.

- [ ] **Step 6: Run the independent-state test and existing root-filter test**

Run: `npm test -- src/api/workspace.test.tsx -t "keeps Kanban and task flow filters independent|loads all database projects"`

Expected: both tests PASS.

- [ ] **Step 7: Commit the shared controls and tab state**

```bash
git add frontend/aios-main/src/api/WorkItemFilterControls.tsx frontend/aios-main/src/api/ApiWorkspace.tsx frontend/aios-main/src/api/workspace.test.tsx frontend/aios-main/src/index.css
git commit -m "feat: add independent task flow filter controls"
```

---

### Task 3: Apply Flow Filters to Nodes, Swimlanes, and Dependency Edges

**Files:**
- Modify: `frontend/aios-main/src/api/ApiWorkspace.tsx`
- Modify: `frontend/aios-main/src/api/workspace.test.tsx`
- Create: `frontend/aios-main/src/api/TaskDependencyGraph.test.tsx`
- Modify: `frontend/aios-main/src/index.css`

**Interfaces:**
- Consumes: `filterWorkItems`, `flowFilters`, `displayWorkItems`, and the WorkItem-to-root map created in `ApiWorkspace`.
- Produces: `flowVisibleWorkItems` and `taskDagProjects` containing only projects with at least one visible task.

- [ ] **Step 1: Write failing integration tests for flow filtering and empty results**

Add a second project using the existing `sessionCatalog` and `resourceOverrides` helpers, then assert:

```ts
it('filters flow nodes by root, kind, agent, and search and shows an empty result', async () => {
  localStorage.setItem('firstflight.active-session-id', 'session-1');
  render(<ApiWorkspace />);
  fireEvent.click(await screen.findByRole('button', { name: /DAG Flow Map/ }));

  fireEvent.change(screen.getByRole('combobox', { name: '搜索流转图工单类型' }), { target: { value: 'TASK' } });
  fireEvent.change(screen.getByRole('combobox', { name: '搜索流转图工单执行者' }), { target: { value: 'AI Agent' } });
  fireEvent.change(screen.getByRole('textbox', { name: '搜索流转图工单' }), { target: { value: '检索' } });

  expect(screen.getByRole('button', { name: /构建检索流程/ })).toBeTruthy();
  expect(screen.queryByRole('button', { name: /实现问答 API/ })).toBeNull();
  expect(screen.queryByRole('button', { name: /知识问答.*Owner/ })).toBeNull();

  fireEvent.change(screen.getByRole('textbox', { name: '搜索流转图工单' }), { target: { value: '不存在的工单' } });
  expect(screen.getByText('没有符合当前筛选条件的任务')).toBeTruthy();
});
```

Extend the multi-project fixture and use the flow `RootTaskFilter` to verify selecting one root hides every Root, Milestone, and Task belonging to the other project.

- [ ] **Step 2: Run the flow-filter integration tests and verify visible nodes remain unfiltered**

Run: `npm test -- src/api/workspace.test.tsx -t "filters flow nodes|filters the flow map to selected roots"`

Expected: FAIL because flow nodes still use `displayWorkItems` and unfiltered `taskDagProjects`.

- [ ] **Step 3: Derive separate visible WorkItem collections**

Create `rootIdByItem` once from `workItemProjects`, then compute:

```ts
const kanbanVisibleWorkItems = filterWorkItems(displayWorkItems, kanbanFilters, rootIdByItem, assigneeLabel);
const flowVisibleWorkItems = filterWorkItems(displayWorkItems, flowFilters, rootIdByItem, assigneeLabel);
```

Use `kanbanVisibleWorkItems` for Kanban columns. Use `flowVisibleWorkItems` for Root and Milestone lanes. Build each `TaskDagProject.tasks` from visible TASK items and finish with `.filter((project) => project.tasks.length > 0)`.

- [ ] **Step 4: Render the filtered count and explicit empty state**

Change the flow toolbar count to `${flowVisibleWorkItems.length} NODES`. When `flowVisibleWorkItems.length === 0`, render:

```tsx
<div className="ff-flow-filter-empty">没有符合当前筛选条件的任务</div>
```

Otherwise render the current `.ff-flow-graph`. Determine each hierarchy lane's empty state from `flowVisibleWorkItems`, not `displayWorkItems`.

- [ ] **Step 5: Run the flow-filter integration tests and verify they pass**

Run: `npm test -- src/api/workspace.test.tsx -t "filters flow nodes|filters the flow map to selected roots|renders task dependencies"`

Expected: all selected tests PASS, including opening a visible node's detail dialog.

- [ ] **Step 6: Write a failing TaskDependencyGraph test for partial edges**

```tsx
// @vitest-environment jsdom
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { TaskDependencyGraph } from './TaskDependencyGraph';

describe('TaskDependencyGraph', () => {
  it('does not draw an edge when its dependency endpoint is filtered out', () => {
    const { container } = render(<TaskDependencyGraph projects={[{
      id: 'project-1', title: '知识问答', tasks: [{
        id: 'task-running', kind: 'TASK', title: '构建检索流程', parent_id: 'root-1',
        dependency_work_item_ids: ['task-hidden'], graph_depth: 1,
      }],
    }]} onOpenWorkItem={vi.fn()} />);
    expect(screen.getByRole('button', { name: /构建检索流程/ })).toBeTruthy();
    expect(container.querySelector('[data-dependency-edge="task-hidden->task-running"]')).toBeNull();
  });
});
```

- [ ] **Step 7: Run the graph test and verify its red-green validity**

Run: `npm test -- src/api/TaskDependencyGraph.test.tsx`

If the test passes immediately because endpoint filtering already exists, temporarily replace `if (!taskIds.has(dependencyId)) continue;` with an unconditional path creation, run the test to observe the expected failure, then restore the guard and rerun to PASS. Do not keep the temporary mutation.

- [ ] **Step 8: Add empty-state styling and commit flow behavior**

Add `.ff-flow-filter-empty` as a centered dashed panel using the existing muted colors inside `.ff-flow-canvas`, then commit:

```bash
git add frontend/aios-main/src/api/ApiWorkspace.tsx frontend/aios-main/src/api/workspace.test.tsx frontend/aios-main/src/api/TaskDependencyGraph.test.tsx frontend/aios-main/src/index.css
git commit -m "feat: filter task flow nodes and dependencies"
```

---

### Task 4: Documentation and Full Verification

**Files:**
- Modify: `frontend/aios-main/CODEX_ARCHITECTURE_AND_API_SPEC.md`

**Interfaces:**
- Consumes: the implemented filter state and rendering behavior.
- Produces: architecture documentation matching the shipped UI behavior.

- [ ] **Step 1: Update the architecture contract**

Replace the sentence stating that root filtering only controls Kanban with a concise description that Kanban and task flow use the same four controls but own independent state. Document that task-flow filtering removes hidden nodes, empty project lanes, and dependency edges with a hidden endpoint.

- [ ] **Step 2: Run formatting and source checks**

Run: `git diff --check`

Expected: exit 0 with no whitespace errors.

- [ ] **Step 3: Run the complete frontend test suite**

Run: `npm test`

Expected: all Vitest files and tests PASS with 0 failures.

- [ ] **Step 4: Run the TypeScript check**

Run: `npm run lint`

Expected: `tsc --noEmit` exits 0.

- [ ] **Step 5: Run the production build**

Run: `npm run build`

Expected: Vite exits 0 and writes the production bundle to `dist/`.

- [ ] **Step 6: Review the final diff against the specification**

Run: `git diff origin/main...HEAD -- frontend/aios-main docs/superpowers`

Verify that the diff contains only the design, plan, filter implementation, tests, styles, and architecture documentation described above.

- [ ] **Step 7: Commit documentation and any final verified adjustments**

```bash
git add frontend/aios-main/CODEX_ARCHITECTURE_AND_API_SPEC.md
git commit -m "docs: describe independent task flow filters"
```

- [ ] **Step 8: Record final repository state for handoff**

Run: `git status --short --branch && git log --oneline --decorate -6`

Expected: clean feature branch with the design, plan, implementation, tests, and documentation commits ahead of `origin/main`.
