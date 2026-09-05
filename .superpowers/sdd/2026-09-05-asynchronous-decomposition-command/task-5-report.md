# Task 5 Report — Background Decomposition Workspace Integration

## Summary

`convert_to_work_item` now uses the existing durable command-job observer in
the workspace. The workspace persists the job ID per Session, observes it by
SSE plus the observer's non-overlapping five-second poll, reconciles durable
success exactly once, and resumes an in-flight job after reload. Other command
actions remain on the existing synchronous `submitCommand` path.

Browser observation is closed on component unmount and when the user switches
projects; this stops the local EventSource and polling only, never the backend
job. A terminal success refreshes that Session's resources once and selects its
first task. Durable failure retains the saved command identity for a retry and
displays the public error.

## TDD Evidence

### RED

Added workspace behavior coverage for accepted-job submission and five-second
polling, reload recovery without re-posting, durable error handling, unmount
cleanup, and project-switch cleanup. The first focused run was:

```text
cd frontend/aios-main && npm test -- src/api/workspace.test.tsx -t "polls its job every five seconds"
```

It failed as intended after posting the accepted command: the workspace did not
render the `正在后台拆解子 WorkItem` progress state because the prior temporary
guard threw instead of starting observation.

### GREEN

After the minimal integration:

```text
cd frontend/aios-main && npm test -- src/api/workspace.test.tsx src/api/commandJobs.test.ts && npm run lint
```

Result: 2 test files, 44 tests passed; `tsc --noEmit` passed. `git diff --check`
also produced no output.

## Files

- `frontend/aios-main/src/api/ApiWorkspace.tsx`
- `frontend/aios-main/src/api/workspace.test.tsx`
- `.superpowers/sdd/2026-09-05-asynchronous-decomposition-command/task-5-report.md`

## Caveats

The durable failure keeps its local command ID deliberately, so a retry can
reuse the same backend identity. Observer closure on navigation intentionally
does not cancel the backend job.

## Commit

`170b5be feat: run task decomposition in background`

## Fix Round 1

### Root cause and RED

Review found that the alternate sidebar decomposition action bypassed the
background helper, while reload recovery performed a one-shot status GET before
it reserved an observer. The existing reload regression expected that immediate
GET. After changing the recovery contract to reserve direct observer ownership,
that focused test failed at the old immediate-request assertion, proving the
test was exercising the recovery boundary rather than a timing typo.

### Fix and GREEN

- All `convert_to_work_item` paths now enter `submitDecomposition`.
- Active observers are keyed by Session and command ID, so restoring or
  re-entering the same job reuses the same completion path rather than creating
  another browser observer.
- Reload constructs the durable accepted-job identity directly; SSE starts
  immediately and the observer owns the retrying five-second poll.
- Terminal state updates no longer overwrite a newer in-memory Session state;
  refresh runs before the stored recovery key is removed.

Fresh verification:

```text
cd frontend/aios-main && npm test && npm run lint
```

Result: 8 test files / 71 tests passed; `tsc --noEmit` passed, and
`git diff --check` was clean.

## Fix Round 2

Reconciliation now uses a render-independent latest-project ref for the
state-version comparison. A per-job in-progress set prevents duplicate terminal
reconciliation, while the completed set and local-storage cleanup occur only
after `refreshResources` succeeds; a failed refresh therefore remains
recoverable on a later observation attempt.

Focused verification: `npm test -- src/api/workspace.test.tsx src/api/commandJobs.test.ts`
(44 tests passed) and `npm run lint` (`tsc --noEmit` passed).

## Fix Round 3

The monotonic Session-state check now runs inside `useWorkspaceProjects`'s
functional `setProjects` transition. This makes the comparison atomic with
React's queued project updates, so an older terminal command state cannot be
committed after a newer state queued in the same render cycle.

## Fix Round 4

Closed the remaining test-evidence gaps without changing production behavior:

- A hook-backed workspace harness queues Session version 9 and then reconciles
  an older terminal version 7 in the same click batch. The rendered state stays
  at `9:COMPLETE`, exercising the functional updater ordering rather than
  comparing two separately rendered snapshots.
- A reload-restored succeeded job now has explicit refresh-recovery coverage.
  Its first terminal reconciliation gets a 503 while refreshing Session
  resources; the saved command ID remains and the error is visible. Switching
  projects away and back replays the same terminal job, performs a second
  reconciliation refresh successfully, and only then removes local storage.

Mutation/regression evidence:

```text
# Remove the state-version guard from the functional project update
npm test -- src/api/workspace.test.tsx -t "older terminal result"
FAIL: expected 9:COMPLETE, received 7:AGENT_SPECS_READY

# Mark a decomposition reconciliation complete before refreshResources
npm test -- src/api/workspace.test.tsx -t "failed resource refresh"
FAIL: expected 3 Session-state GETs, received 2 (the retry was suppressed)
```

The production code was restored after both mutations. Fresh GREEN evidence:

```text
npm test -- src/api/workspace.test.tsx src/api/commandJobs.test.ts
2 files / 46 tests passed

npm test
8 files / 73 tests passed

npm run lint
tsc --noEmit passed
```
