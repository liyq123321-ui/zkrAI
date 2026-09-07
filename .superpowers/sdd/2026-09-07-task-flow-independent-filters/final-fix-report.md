# Task Flow Independent Filters: Final Fix Report

## Scope and starting state

- Worktree: `/Users/tangtang/Desktop/zkrAI/.worktrees/task-flow-independent-filters`
- Starting HEAD: `55d72f3788b1616ff547b76b17674b9f2f3f531d`
- Baseline: `npm test` passed with 10 files and 100 tests.
- Scope remained frontend-only: no dependencies, backend APIs, persistence, schemas, or unrelated refactors were added.

## Finding-to-fix map

### Important 1: mutation-insensitive filter coverage

- Rebuilt the pure-filter fixture around one matching target plus one decoy for each individual dimension: wrong root, wrong kind, wrong agent, and wrong search text. Each decoy matches all of the other active filters.
- Expanded the tab-state integration test to set and restore search, Root selection, kind, and agent independently in both Kanban and Task Flow.
- Added integration coverage for invalid agent normalization in both filter states after refreshed WorkItems change the available-agent list, while preserving each tab's search value.
- Added assertions for the filtered flow node count and for removal of the filtered-out project's task swimlane while retaining the matching project's swimlane.
- Existing partial-edge coverage remains active in `TaskDependencyGraph.test.tsx` and was included in the focused GREEN run.

Mutation evidence for the combined unit assertion (temporary production mutations were restored immediately):

- Remove Root clause -> received `['task-a', 'wrong-root']`; test failed.
- Remove kind clause -> received `['task-a', 'wrong-kind']`; test failed.
- Remove agent clause -> received `['task-a', 'wrong-agent']`; test failed.
- Remove search clause -> received `['task-a', 'wrong-search']`; test failed.

### Important 2: misleading filtered-state copy

- Traced the root cause to hierarchy empty messages being based only on `flowVisibleWorkItems` and the task graph hardcoding `等待后端生成子任务` whenever its filtered project list is empty.
- Hierarchy lanes now consult loaded `displayWorkItems`: an existing-but-filtered kind displays `当前筛选条件已隐藏此类节点`, while a genuinely absent kind retains `等待后端生成`.
- `TaskDependencyGraph` now accepts an optional empty message, preserving its existing default while allowing `ApiWorkspace` to distinguish hidden tasks from backend absence.
- Integration coverage checks both the filtered Root/task messages and the genuine missing-Milestone waiting message.

### Important 3: architecture statement under the wrong component

- Removed the duplicate `筛选投影与边裁剪` bullet from the legacy `src/components/TaskFlowDiagram.tsx` section.
- Kept the already-correct API-mode contract in the top `数据库共享看板与主任务多选` section.

### Minor 1: announce dynamic empty results

- Added `role="status"` and `aria-live="polite"` to `没有符合当前筛选条件的任务`.
- Replaced the text-only integration check with a role-based accessible assertion.

### Minor 2: visible high-contrast keyboard focus

- Added a layout-neutral `:focus-visible` rule for shared filter inputs and selects: `2px solid #1668dc` with a `2px` offset.
- Calculated contrast against white: `5.19:1`, above the requested `3:1` threshold.

## Files changed

- `frontend/aios-main/src/api/workItemFilters.test.ts`
- `frontend/aios-main/src/api/workspace.test.tsx`
- `frontend/aios-main/src/api/ApiWorkspace.tsx`
- `frontend/aios-main/src/api/TaskDependencyGraph.tsx`
- `frontend/aios-main/src/index.css`
- `frontend/aios-main/CODEX_ARCHITECTURE_AND_API_SPEC.md`
- `.superpowers/sdd/2026-09-07-task-flow-independent-filters/final-fix-report.md`

## RED/GREEN evidence

### RED

Command:

```text
npm test -- src/api/workspace.test.tsx -t "distinguishes backend-empty"
```

Result: exit 1; 1 failed, 48 skipped. The failure was the intended missing behavior: the Root lane contained `等待后端生成` and could not find `当前筛选条件已隐藏此类节点`.

The preceding combined focused RED also failed the role-based empty-result assertion with `Unable to find an accessible element with the role "status"`.

The four unit mutation checks each exited 1 and exposed the corresponding dedicated decoy, as listed under Important 1.

### GREEN

Command:

```text
npm test -- src/api/workItemFilters.test.ts src/api/workspace.test.tsx src/api/TaskDependencyGraph.test.tsx -t "dedicated decoy|keeps Kanban|normalizes invalid agents|filters flow nodes|distinguishes backend-empty|filters the flow map|does not draw an edge"
```

Result: exit 0; 3 test files passed, 7 tests passed, 46 skipped.

## Full verification

All commands were run from `frontend/aios-main` unless noted.

### `npm test`

```text
> react-example@0.0.0 test
> vitest run

Test Files  10 passed (10)
Tests       102 passed (102)
Duration    7.60s
```

Exit: 0.

### `npm run lint`

```text
> react-example@0.0.0 lint
> tsc --noEmit
```

Exit: 0.

The first lint attempt exposed test-only `Element`/`HTMLElement` typing at the new `closest()` bindings. The bindings were narrowed with `closest<HTMLElement>()`, after which the full test and lint gates were rerun on the corrected tree and passed.

### `npm run build`

```text
> react-example@0.0.0 build
> vite build

vite v6.4.3 building for production...
✓ 1950 modules transformed.
dist/index.html                   0.99 kB | gzip:   0.44 kB
dist/assets/index-NwD6eSlV.css   90.37 kB | gzip:  17.43 kB
dist/assets/index-DCard9WE.js   471.96 kB | gzip: 145.56 kB
✓ built in 1.47s
```

Exit: 0.

### `git diff --check`

Run from the worktree root after finalizing this report.

```text
(no output)
```

Exit: 0.

## Self-review

- Re-read all five findings and the approved design/plan against the final diff.
- Confirmed the combined unit fixture is mutation-sensitive to removal of every filter clause.
- Confirmed the filtered-state copy is selected from loaded backend data, not from the filtered projection.
- Confirmed `TaskDependencyGraph` preserves its original default for all existing callers.
- Confirmed the focus rule changes only paint and does not affect layout.
- Confirmed the legacy architecture section no longer claims API-mode filtering behavior.
- Confirmed no backend, persistence, dependency, or schema changes are present.

## Concerns

- No known functional concerns. The `:focus-visible` rule was source-reviewed and contrast-calculated; jsdom does not provide meaningful visual pseudo-class rendering, so it was not screenshot-tested in a real browser.
