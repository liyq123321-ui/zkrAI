# Milestone 子任务监控 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Milestone 看板卡片中显示直属子任务的紧凑状态汇总，并在 Milestone 详情中用自上而下的依赖图监控任务状态和打开子 WorkItem。

**Architecture:** 新建纯函数模块负责 Milestone 直属任务筛选、状态统计和依赖拓扑分层；新建专属 React 组件负责汇总文案及详情图渲染。`ApiWorkspace` 只负责传入同项目 WorkItem、根据选中类型切换详情组件，以及沿用 `selectedWorkItemId` 完成子任务详情跳转。

**Tech Stack:** React 19、TypeScript 5.8、Vitest 4、Testing Library、SVG、现有 CSS 设计系统

## Global Constraints

- 监控范围仅包含 `kind === "TASK"` 且 `parent_id` 等于当前 Milestone ID 的 WorkItem。
- 不递归纳入其他层级，不展示其他 Milestone 或其他项目的任务。
- 前端只读取 WorkItem、状态和依赖字段，不修改任务状态或依赖关系。
- 同层任务横向并列，依赖层级自上而下排列。
- 外部依赖不生成节点或连线。
- 循环依赖任务进入最后的异常层，并显示异常提示。
- 子任务节点必须支持鼠标、Enter 和空格打开对应 WorkItem 详情。
- 不修改全局 `TaskDependencyGraph` 的布局和行为。

---

### Task 1: Milestone 监控数据模型

**Files:**
- Create: `frontend/aios-main/src/api/milestoneTaskMonitoring.ts`
- Test: `frontend/aios-main/src/api/milestoneTaskMonitoring.test.ts`

**Interfaces:**
- Consumes: `WorkItemDto` from `./dto` and `workItemLane(status)` from `./workflowUi`.
- Produces: `MilestoneTaskStats`, `MilestoneTaskLayer`, `milestoneChildTasks(milestoneId, workItems)`, `milestoneTaskStats(tasks)`, `milestoneSummaryLabel(stats)`, and `milestoneTaskLayers(tasks)`.

- [ ] **Step 1: Write failing tests for scope and compact statistics**

Create `milestoneTaskMonitoring.test.ts` with this local `item()` factory and these assertions:

```ts
import { describe, expect, it } from 'vitest';
import type { WorkItemDto } from './dto';
import {
  milestoneChildTasks,
  milestoneSummaryLabel,
  milestoneTaskStats,
} from './milestoneTaskMonitoring';

function item(id: string, parentId: string | null, status = 'todo', kind: WorkItemDto['kind'] = 'TASK'): WorkItemDto {
  return {
    id, parent_id: parentId, kind, status, title: id, description: null,
    objective: null, scope: [], exclusions: [], outputs: [], acceptance_criteria: [],
    required_skills: [], responsible_role: null, suggested_assignee: null,
    dependency_work_item_ids: [],
  };
}

describe('milestone task monitoring model', () => {
  it('selects only direct TASK children of the milestone', () => {
    const result = milestoneChildTasks('milestone-a', [
      item('direct', 'milestone-a'),
      item('other', 'milestone-b'),
      item('nested', 'direct'),
      item('child-milestone', 'milestone-a', 'todo', 'MILESTONE'),
    ]);
    expect(result.map(({ id }) => id)).toEqual(['direct']);
  });

  it('builds compact summaries for active, complete, and empty milestones', () => {
    const active = milestoneTaskStats([
      item('todo', 'm'), item('running', 'm', 'in_progress'),
      item('blocked', 'm', 'blocked'), item('done', 'm', 'completed'),
    ]);
    expect(active).toEqual({ total: 4, todo: 1, inProgress: 1, blocked: 1, done: 1 });
    expect(milestoneSummaryLabel(active)).toBe('4 个子任务 · 1 进行中 · 1 受阻');
    expect(milestoneSummaryLabel(milestoneTaskStats([item('done', 'm', 'done')]))).toBe('1 个子任务 · 全部完成');
    expect(milestoneSummaryLabel(milestoneTaskStats([]))).toBe('暂无子任务');
  });
});
```

- [ ] **Step 2: Run the model test and verify RED**

Run: `cd frontend/aios-main && npm test -- src/api/milestoneTaskMonitoring.test.ts`

Expected: FAIL because `./milestoneTaskMonitoring` does not exist.

- [ ] **Step 3: Implement scope and statistics helpers**

Create `milestoneTaskMonitoring.ts` with these exact public types and functions:

```ts
import type { WorkItemDto } from './dto';
import { workItemLane } from './workflowUi';

export interface MilestoneTaskStats {
  total: number;
  todo: number;
  inProgress: number;
  blocked: number;
  done: number;
}

export interface MilestoneTaskLayer {
  depth: number;
  tasks: WorkItemDto[];
  cycle: boolean;
}

export function milestoneChildTasks(milestoneId: string, workItems: WorkItemDto[]): WorkItemDto[] {
  return workItems.filter((item) => item.kind === 'TASK' && item.parent_id === milestoneId);
}

export function milestoneTaskStats(tasks: WorkItemDto[]): MilestoneTaskStats {
  return tasks.reduce<MilestoneTaskStats>((stats, task) => {
    stats.total += 1;
    if (task.status === 'blocked' || workItemLane(task.status) === 'failed') stats.blocked += 1;
    else if (workItemLane(task.status) === 'done') stats.done += 1;
    else if (workItemLane(task.status) === 'in_progress') stats.inProgress += 1;
    else stats.todo += 1;
    return stats;
  }, { total: 0, todo: 0, inProgress: 0, blocked: 0, done: 0 });
}

export function milestoneSummaryLabel(stats: MilestoneTaskStats): string {
  if (stats.total === 0) return '暂无子任务';
  if (stats.done === stats.total) return `${stats.total} 个子任务 · 全部完成`;
  const parts = [`${stats.total} 个子任务`];
  if (stats.inProgress > 0) parts.push(`${stats.inProgress} 进行中`);
  if (stats.blocked > 0) parts.push(`${stats.blocked} 受阻`);
  return parts.join(' · ');
}
```

- [ ] **Step 4: Run the model test and verify GREEN**

Run: `cd frontend/aios-main && npm test -- src/api/milestoneTaskMonitoring.test.ts`

Expected: PASS for direct-child filtering and all summary variants.

- [ ] **Step 5: Write failing tests for vertical dependency layers and cycles**

Extend the same test file:

```ts
import { milestoneTaskLayers } from './milestoneTaskMonitoring';

it('places parallel tasks together and dependent tasks below them', () => {
  const a = item('a', 'm');
  const b = item('b', 'm');
  const c = { ...item('c', 'm'), dependency_work_item_ids: ['a', 'b', 'outside'] };
  const d = { ...item('d', 'm'), dependency_work_item_ids: ['c'] };
  expect(milestoneTaskLayers([a, b, c, d])).toEqual([
    { depth: 0, tasks: [a, b], cycle: false },
    { depth: 1, tasks: [c], cycle: false },
    { depth: 2, tasks: [d], cycle: false },
  ]);
});

it('places cyclic tasks in a final anomaly layer', () => {
  const a = { ...item('a', 'm'), dependency_work_item_ids: ['b'] };
  const b = { ...item('b', 'm'), dependency_work_item_ids: ['a'] };
  expect(milestoneTaskLayers([a, b])).toEqual([
    { depth: 0, tasks: [a, b], cycle: true },
  ]);
});
```

- [ ] **Step 6: Run the layer tests and verify RED**

Run: `cd frontend/aios-main && npm test -- src/api/milestoneTaskMonitoring.test.ts`

Expected: FAIL because `milestoneTaskLayers` is not exported.

- [ ] **Step 7: Implement Kahn-style layering with longest dependency depth**

Add `milestoneTaskLayers(tasks)` to the model. The implementation filters external dependencies, assigns the longest valid predecessor depth, preserves input order inside layers, and places unresolved cyclic tasks in the final anomaly layer:

```ts
export function milestoneTaskLayers(tasks: WorkItemDto[]): MilestoneTaskLayer[] {
  const taskIds = new Set(tasks.map(({ id }) => id));
  const dependencies = new Map(tasks.map((task) => [
    task.id,
    task.dependency_work_item_ids.filter((id) => taskIds.has(id)),
  ]));
  const unresolved = new Set(tasks.map(({ id }) => id));
  const depths = new Map<string, number>();

  while (unresolved.size > 0) {
    const ready = tasks.filter((task) => unresolved.has(task.id)
      && (dependencies.get(task.id) ?? []).every((id) => depths.has(id)));
    if (ready.length === 0) break;
    for (const task of ready) {
      const parents = dependencies.get(task.id) ?? [];
      depths.set(task.id, parents.length === 0
        ? 0
        : Math.max(...parents.map((id) => depths.get(id) ?? 0)) + 1);
      unresolved.delete(task.id);
    }
  }

  const grouped = new Map<number, WorkItemDto[]>();
  for (const task of tasks) {
    const depth = depths.get(task.id);
    if (depth === undefined) continue;
    grouped.set(depth, [...(grouped.get(depth) ?? []), task]);
  }
  const layers = [...grouped.entries()]
    .sort(([left], [right]) => left - right)
    .map(([depth, layerTasks]) => ({ depth, tasks: layerTasks, cycle: false }));
  if (unresolved.size > 0) {
    layers.push({
      depth: layers.length === 0 ? 0 : layers[layers.length - 1].depth + 1,
      tasks: tasks.filter((task) => unresolved.has(task.id)),
      cycle: true,
    });
  }
  return layers;
}
```

- [ ] **Step 8: Run model tests and TypeScript check**

Run: `cd frontend/aios-main && npm test -- src/api/milestoneTaskMonitoring.test.ts && npm run lint`

Expected: all model tests PASS and TypeScript reports no errors.

- [ ] **Step 9: Commit the model**

```bash
git add frontend/aios-main/src/api/milestoneTaskMonitoring.ts frontend/aios-main/src/api/milestoneTaskMonitoring.test.ts
git commit -m "feat: model milestone task monitoring"
```

### Task 2: Milestone 状态汇总与垂直监控图组件

**Files:**
- Create: `frontend/aios-main/src/api/MilestoneTaskMonitor.tsx`
- Create: `frontend/aios-main/src/api/MilestoneTaskMonitor.test.tsx`
- Modify: `frontend/aios-main/src/index.css`

**Interfaces:**
- Consumes: helpers and types from `./milestoneTaskMonitoring`, `WorkItemDto`, and existing `workItemLane`, `workItemStatusLabel`.
- Produces: `MilestoneTaskSummary({ milestoneId, workItems })` and `MilestoneTaskMonitor({ milestone, workItems, onOpenWorkItem })`.

- [ ] **Step 1: Write failing component tests**

Create `MilestoneTaskMonitor.test.tsx` using jsdom. Cover:

```tsx
// @vitest-environment jsdom
import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { WorkItemDto } from './dto';
import { MilestoneTaskMonitor, MilestoneTaskSummary } from './MilestoneTaskMonitor';

function task(id: string, parentId: string, status = 'todo', dependencies: string[] = []): WorkItemDto {
  return {
    id, parent_id: parentId, kind: 'TASK', title: id, description: null,
    objective: null, status, scope: [], exclusions: [], outputs: [],
    acceptance_criteria: [], required_skills: [], responsible_role: '开发工程师',
    suggested_assignee: 'Agent A', dependency_work_item_ids: dependencies,
  };
}

function milestone(id: string): WorkItemDto {
  return {
    id, parent_id: 'root', kind: 'MILESTONE', title: '检索能力交付',
    description: null, objective: '完成检索链路', status: 'in_progress', scope: [],
    exclusions: [], outputs: [], acceptance_criteria: [], required_skills: [],
    responsible_role: '项目经理', suggested_assignee: 'PM Agent',
    dependency_work_item_ids: [],
  };
}

function cyclicTasks(): WorkItemDto[] {
  return [task('a', 'm', 'todo', ['b']), task('b', 'm', 'todo', ['a'])];
}

it('renders the compact summary for direct milestone children', () => {
  render(<MilestoneTaskSummary milestoneId="m" workItems={[
    task('a', 'm', 'in_progress'), task('b', 'm', 'blocked'), task('x', 'other', 'done'),
  ]} />);
  expect(screen.getByText('2 个子任务 · 1 进行中 · 1 受阻')).toBeTruthy();
});

it('renders vertical layers, valid edges, statuses, and task navigation', () => {
  const onOpenWorkItem = vi.fn();
  const { container } = render(<MilestoneTaskMonitor
    milestone={milestone('m')}
    workItems={[
      task('a', 'm', 'completed'),
      task('b', 'm', 'in_progress'),
      task('c', 'm', 'blocked', ['a', 'b', 'outside']),
      task('x', 'other', 'todo'),
    ]}
    onOpenWorkItem={onOpenWorkItem}
  />);
  expect(screen.getByRole('group', { name: '执行层 1' })).toBeTruthy();
  expect(screen.getByRole('group', { name: '执行层 2' })).toBeTruthy();
  expect(container.querySelector('[data-milestone-edge="a->c"]')).toBeTruthy();
  expect(container.querySelector('[data-milestone-edge="b->c"]')).toBeTruthy();
  expect(container.querySelector('[data-milestone-edge="outside->c"]')).toBeNull();
  expect(screen.getByText('已完成')).toBeTruthy();
  expect(screen.getByText('进行中')).toBeTruthy();
  expect(screen.getByText('受阻')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: /打开子任务：c/ }));
  expect(onOpenWorkItem).toHaveBeenCalledWith('c');
});

it('shows empty and cyclic-dependency messages', () => {
  const { rerender } = render(<MilestoneTaskMonitor milestone={milestone('m')} workItems={[]} onOpenWorkItem={vi.fn()} />);
  expect(screen.getByText('该里程碑暂无子任务')).toBeTruthy();
  rerender(<MilestoneTaskMonitor milestone={milestone('m')} workItems={cyclicTasks()} onOpenWorkItem={vi.fn()} />);
  expect(screen.getByText('检测到循环依赖，以下任务无法确定执行顺序。')).toBeTruthy();
});
```

- [ ] **Step 2: Run component tests and verify RED**

Run: `cd frontend/aios-main && npm test -- src/api/MilestoneTaskMonitor.test.tsx`

Expected: FAIL because `MilestoneTaskMonitor.tsx` does not exist.

- [ ] **Step 3: Implement summary and detail shell**

Create the component with these exact public props:

```ts
export function MilestoneTaskSummary({ milestoneId, workItems }: {
  milestoneId: string;
  workItems: WorkItemDto[];
}): JSX.Element;

export function MilestoneTaskMonitor({ milestone, workItems, onOpenWorkItem }: {
  milestone: WorkItemDto;
  workItems: WorkItemDto[];
  onOpenWorkItem: (workItemId: string) => void;
}): JSX.Element;
```

`MilestoneTaskSummary` uses `milestoneChildTasks`, `milestoneTaskStats`, and `milestoneSummaryLabel`. Apply `is-blocked` when `blocked > 0`, `is-complete` when `total > 0 && done === total`, and include `aria-label="子任务状态：<summary>"`.

The detail shell renders a title bar, objective, four overview cells (总任务、已完成、进行中、受阻), and the empty state before implementing the graph.

- [ ] **Step 4: Implement the vertical graph and node click behavior**

Use `useRef`, `useLayoutEffect`, `useEffect`, `useCallback`, and `ResizeObserver` following the existing `TaskDependencyGraph` measurement pattern. Register each node button by ID. For every local dependency, measure from the source button bottom-center to the target button top-center and draw a cubic path:

```ts
const middleY = (edge.y1 + edge.y2) / 2;
const d = `M ${edge.x1} ${edge.y1} C ${edge.x1} ${middleY}, ${edge.x2} ${middleY}, ${edge.x2} ${edge.y2}`;
```

Render each `MilestoneTaskLayer` as `role="group"` with one-based accessible names (`执行层 1`, `执行层 2`). Render `cycle` layers with `aria-label="依赖异常"` and the exact warning text from the test. Node buttons call `onOpenWorkItem(task.id)` and use `aria-label={`打开子任务：${task.title || task.id}`}`.

- [ ] **Step 5: Add focused CSS for the summary and responsive vertical graph**

Add `.ff-milestone-summary`, `.ff-milestone-monitor`, `.ff-milestone-overview`, `.ff-milestone-graph`, `.ff-milestone-layer`, `.ff-milestone-layer-nodes`, `.ff-milestone-node`, `.ff-milestone-edge`, and status modifier classes to `index.css`.

Required visual behavior:

- summary is a single compact row and uses warning/complete color modifiers;
- graph is `position: relative` with an absolute full-size SVG beneath `z-index: 1` nodes;
- layers use vertical stacking; nodes within a layer use a wrapping horizontal flex row;
- node minimum width is `220px` and status remains readable;
- the graph container scrolls horizontally below `900px`;
- edge stroke and arrow marker use the existing muted blue-gray palette;
- blocked nodes have a red accent, active nodes blue, completed nodes green, and todo nodes neutral.

- [ ] **Step 6: Run component and model tests**

Run: `cd frontend/aios-main && npm test -- src/api/MilestoneTaskMonitor.test.tsx src/api/milestoneTaskMonitoring.test.ts`

Expected: all tests PASS with no React warnings.

- [ ] **Step 7: Run TypeScript check**

Run: `cd frontend/aios-main && npm run lint`

Expected: no TypeScript errors.

- [ ] **Step 8: Commit the component**

```bash
git add frontend/aios-main/src/api/MilestoneTaskMonitor.tsx frontend/aios-main/src/api/MilestoneTaskMonitor.test.tsx frontend/aios-main/src/index.css
git commit -m "feat: add milestone task monitor"
```

### Task 3: 接入 Kanban 卡片和 WorkItem 详情导航

**Files:**
- Modify: `frontend/aios-main/src/api/ApiWorkspace.tsx`
- Modify: `frontend/aios-main/src/api/workspace.test.tsx`

**Interfaces:**
- Consumes: `MilestoneTaskSummary` and `MilestoneTaskMonitor` from Task 2.
- Produces: Milestone card summaries and Milestone-specific details integrated with the existing `selectedWorkItemId` navigation.

- [ ] **Step 1: Extend workspace fixtures with a Milestone and scoped tasks**

In the primary `/sessions/session-1/work-items` fixture, add:

```ts
{ id:'milestone-1', parent_id:'root-1', kind:'MILESTONE', title:'检索能力交付', status:'in_progress', dependency_work_item_ids:[] },
{ id:'milestone-task-a', parent_id:'milestone-1', kind:'TASK', title:'实现向量检索', status:'completed', suggested_assignee:'AI Agent', dependency_work_item_ids:[] },
{ id:'milestone-task-b', parent_id:'milestone-1', kind:'TASK', title:'接入问答接口', status:'in_progress', suggested_assignee:'Backend Agent', dependency_work_item_ids:['milestone-task-a'] },
```

Keep every fixture object compatible with `WorkItemDto`; nullable optional content fields may follow the existing concise fixture style.

- [ ] **Step 2: Write failing integration tests for card summary and navigation**

Add one integration test that renders `ApiWorkspace`, waits for the Kanban board, and asserts:

```ts
const milestoneCard = await screen.findByText('检索能力交付');
const card = milestoneCard.closest('[role="button"]')!;
expect(within(card).getByText('2 个子任务 · 1 进行中')).toBeTruthy();

fireEvent.click(card);
expect(await screen.findByRole('heading', { name: '子任务执行监控' })).toBeTruthy();
expect(screen.getByRole('button', { name: '打开子任务：接入问答接口' })).toBeTruthy();

fireEvent.click(screen.getByRole('button', { name: '打开子任务：接入问答接口' }));
expect(await screen.findByRole('heading', { name: '接入问答接口' })).toBeTruthy();
expect(screen.queryByRole('heading', { name: '子任务执行监控' })).toBeNull();
```

- [ ] **Step 3: Run the integration test and verify RED**

Run: `cd frontend/aios-main && npm test -- src/api/workspace.test.tsx`

Expected: FAIL because the Milestone card has no summary and Milestone still opens `AgentSpecDetail`.

- [ ] **Step 4: Add the Milestone card summary**

Import `MilestoneTaskSummary` and render it only inside `item.kind === 'MILESTONE'` cards:

```tsx
{item.kind === 'MILESTONE' && (
  <MilestoneTaskSummary
    milestoneId={item.id}
    workItems={project.resources.workItems}
  />
)}
```

Use project-local resources so the summary cannot count another project's task even if IDs are malformed.

- [ ] **Step 5: Split Milestone and TASK detail rendering**

Replace the broad `selectedWorkItem.kind !== 'ROOT'` branch with two explicit branches:

```tsx
{selectedWorkItem.kind === 'MILESTONE' && (
  <MilestoneTaskMonitor
    key={selectedWorkItem.id}
    milestone={selectedWorkItem}
    workItems={selectedResources.workItems}
    onOpenWorkItem={setSelectedWorkItemId}
  />
)}
{selectedWorkItem.kind === 'TASK' && (
  <AgentSpecDetail
    key={previewKey(selectedWorkItem.id)}
    item={selectedWorkItem}
    agentSpecs={selectedAgentSpecs}
    sourceSpecs={selectedResources.specs}
    employees={employees}
    workItems={allResources.workItems}
    onOpenWorkItem={setSelectedWorkItemId}
    preview={workItemPreviews[previewKey(selectedWorkItem.id)]}
    onPreviewChange={(patch) => {
      const key = previewKey(selectedWorkItem.id);
      setWorkItemPreviews((previous) => ({
        ...previous,
        [key]: { draft: '', ...previous[key], ...patch },
      }));
      if (patch.assigneeId !== undefined) {
        setKanbanFilters((current) => ({ ...current, agent: 'ALL' }));
      }
    }}
  />
)}
```

Set the dialog title to `里程碑详情` for Milestones, retain `PRD 审核` for Root, and use `任务详情` for TASK.

- [ ] **Step 6: Run targeted tests and verify GREEN**

Run: `cd frontend/aios-main && npm test -- src/api/workspace.test.tsx src/api/MilestoneTaskMonitor.test.tsx src/api/milestoneTaskMonitoring.test.ts`

Expected: card summary, Milestone detail, graph nodes, and TASK detail navigation tests all PASS.

- [ ] **Step 7: Run the full frontend verification suite**

Run: `cd frontend/aios-main && npm test && npm run lint && npm run build`

Expected: all Vitest tests PASS, TypeScript reports no errors, and Vite produces a successful production build.

- [ ] **Step 8: Perform focused browser verification**

Run the existing frontend development server and verify at desktop and narrow widths:

1. Milestone card summary fits on one compact row or wraps without overlapping controls.
2. Two dependency-free tasks appear in the same horizontal layer.
3. A dependent task appears below its prerequisites with arrowheads pointing downward.
4. Completed, active, blocked, and todo statuses remain distinguishable by text and styling.
5. Clicking a node changes the open dialog from Milestone detail to the selected TASK detail and resets scroll position.

- [ ] **Step 9: Commit the workspace integration**

```bash
git add frontend/aios-main/src/api/ApiWorkspace.tsx frontend/aios-main/src/api/workspace.test.tsx
git commit -m "feat: surface milestone task status"
```

### Task 4: Final regression review

**Files:**
- Verify only; modify only files already listed above if a regression is found.

**Interfaces:**
- Consumes: completed Tasks 1–3.
- Produces: a verified implementation ready for integration.

- [ ] **Step 1: Review the final diff against the design**

Run: `git diff HEAD~3 -- frontend/aios-main/src/api frontend/aios-main/src/index.css`

Verify that direct-child scoping, compact summary, vertical graph, external-edge filtering, cycle handling, status copy, and navigation are all present, and that `TaskDependencyGraph.tsx` is unchanged.

- [ ] **Step 2: Re-run clean verification**

Run: `cd frontend/aios-main && npm test && npm run lint && npm run build`

Expected: all commands exit with code 0.

- [ ] **Step 3: Check repository status**

Run: `git status --short`

Expected: no uncommitted implementation changes. If build output is ignored, it must not appear in status.
