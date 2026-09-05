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
