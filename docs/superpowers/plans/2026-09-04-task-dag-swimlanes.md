# Task DAG Swimlanes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Render child WorkItems as project-scoped, depth-layered dependency graphs in the Tasks section of DAG Flow Map.

**Architecture:** Extract task graph rendering and DOM edge measurement into a focused `TaskDependencyGraph` component. `ApiWorkspace` continues to aggregate database projects and passes project title, task arrays, and the existing WorkItem-open callback; the graph component groups by supplied projects, places tasks by backend `graph_depth`, and draws only same-project dependency arrows.

**Tech Stack:** React 19, TypeScript 5.8, Vitest, Testing Library, CSS, SVG.

## Global Constraints

- Root and Milestones retain their current hierarchy display.
- Tasks are grouped into independent project swimlanes.
- The frontend reads `graph_depth` and `dependency_work_item_ids` without deriving workflow rules.
- Missing and cross-project dependency targets do not produce edges.
- Task nodes remain keyboard-accessible buttons that open WorkItem details.
- No backend endpoint or persistence behavior changes.

---

### Task 1: Task dependency graph component

**Files:**
- Create: `frontend/aios-main/src/api/TaskDependencyGraph.tsx`
- Create: `frontend/aios-main/src/api/TaskDependencyGraph.test.tsx`
- Modify: `frontend/aios-main/src/index.css`

**Interfaces:**
- Consumes: `WorkItemDto` and `projects: Array<{ id: string; title: string; tasks: WorkItemDto[] }>`.
- Produces: `TaskDependencyGraph({ projects, onOpenWorkItem })`, where `onOpenWorkItem: (workItemId: string) => void`.

- [x] **Step 1: Write the failing component test**

```tsx
render(<TaskDependencyGraph projects={[
  {id:'project-a', title:'知识问答', tasks:[
    task('a', 0, []), task('b', 1, ['a']), task('c', 2, ['b']),
  ]},
  {id:'project-b', title:'客服助手', tasks:[task('x', 0, ['a'])]},
]} onOpenWorkItem={onOpen} />);
expect(screen.getByRole('region', {name:'知识问答任务依赖图'})).toBeTruthy();
expect(container.querySelectorAll('[data-dependency-edge]')).toHaveLength(2);
fireEvent.click(screen.getByRole('button', {name:/任务 b/}));
expect(onOpen).toHaveBeenCalledWith('b');
```

- [x] **Step 2: Run the focused test and confirm the missing component failure**

Run: `npm test -- src/api/TaskDependencyGraph.test.tsx`

Expected: FAIL because `./TaskDependencyGraph` does not exist.

- [x] **Step 3: Implement grouping, depth columns, edge measurement, and resize handling**

```tsx
export type TaskDagProject = { id: string; title: string; tasks: WorkItemDto[] };

export function TaskDependencyGraph({projects, onOpenWorkItem}: {
  projects: TaskDagProject[];
  onOpenWorkItem: (workItemId: string) => void;
}) {
  return <section className="ff-task-dag" aria-label="子任务依赖图">
    {projects.map((project) => (
      <TaskProjectSwimlane key={project.id} project={project} onOpenWorkItem={onOpenWorkItem} />
    ))}
  </section>;
}
```

`TaskProjectSwimlane` must sort numeric depth columns, render same-depth tasks vertically, retain one DOM ref per task ID, and create SVG paths only when both endpoints belong to `project.tasks`. Each path carries `data-dependency-edge="source->target"` and an arrow marker. A `ResizeObserver`, with a window resize fallback, recalculates coordinates after layout changes.

- [x] **Step 4: Add graph layout styles**

Add `.ff-task-dag`, `.ff-task-dag-project`, `.ff-task-dag-depths`, `.ff-task-dag-depth`, `.ff-task-dag-node`, `.ff-task-dag-edges`, and `.ff-task-dag-edge` styles. Depth columns are horizontal, nodes within a depth are vertical, project graphs scroll within the existing flow canvas, and arrows remain behind nodes.

- [x] **Step 5: Run the focused test**

Run: `npm test -- src/api/TaskDependencyGraph.test.tsx`

Expected: PASS, including project isolation, two valid dependency edges, and node click behavior.

### Task 2: Workspace integration and regression verification

**Files:**
- Modify: `frontend/aios-main/src/api/ApiWorkspace.tsx`
- Modify: `frontend/aios-main/src/api/workspace.test.tsx`

**Interfaces:**
- Consumes: `TaskDependencyGraph` and `TaskDagProject` from Task 1.
- Produces: DAG Flow Map with existing Root/Milestone lanes followed by the Tasks graph.

- [x] **Step 1: Write the failing workspace integration test**

```tsx
fireEvent.click(await screen.findByRole('button', {name:'DAG Flow Map (任务流转图)'}));
expect(screen.getByRole('region', {name:'知识问答任务依赖图'})).toBeTruthy();
expect(screen.getByRole('button', {name:/构建检索流程/})).toBeTruthy();
```

- [x] **Step 2: Run the focused workspace test and confirm failure**

Run: `npm test -- src/api/workspace.test.tsx -t "renders task dependencies as project swimlanes"`

Expected: FAIL because the current Tasks lane is a vertical list without a project graph region.

- [x] **Step 3: Build project graph input and replace the Tasks lane**

```tsx
const taskDagProjects = useMemo(() => projectList.map((project) => ({
  id: project.state.session_id,
  title: projectTitle(project),
  tasks: displayWorkItems.filter((item) =>
    item.kind === 'TASK' && workItemProjects.get(item.id)?.state.session_id === project.state.session_id),
})).filter((project) => project.tasks.length > 0), [displayWorkItems, projectList, workItemProjects]);
```

Render Root and Milestones with the existing lane markup, then render:

```tsx
<TaskDependencyGraph projects={taskDagProjects} onOpenWorkItem={setSelectedWorkItemId} />
```

Remove the page-level task edge refs/effects because edge measurement now belongs to the extracted component.

- [x] **Step 4: Run focused and complete verification**

Run:

```bash
npm test -- src/api/TaskDependencyGraph.test.tsx src/api/workspace.test.tsx
npm test
npm run lint
npm run build
```

Expected: all tests pass, TypeScript emits no errors, and Vite creates the production bundle.

- [x] **Step 5: Review the browser behavior**

At `http://127.0.0.1:3001/`, open DAG Flow Map and confirm each project has a separate Tasks swimlane, arrows point left-to-right, scrolling reaches the deepest task, and clicking a task opens its detail dialog.
