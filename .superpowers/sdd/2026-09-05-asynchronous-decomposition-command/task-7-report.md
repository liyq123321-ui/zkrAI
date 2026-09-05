# Task 7 Report — Pre-Accepted STALE_STATE Recovery

## Outcome

The final merge blocker is fixed. A `STALE_STATE` returned before a decomposition
job has been accepted now clears the failed pending command identity, immediately
refreshes the target Session, and shows the fixed public recovery message:

```text
状态已被其他操作更新。页面已刷新，请确认最新状态后重新提交。
```

The next user click therefore reads the refreshed Session state, sends the new
`expected_state_version`, generates a new command ID, and can transition into
the asynchronous decomposition observer.

## Implementation

`submitDecomposition` now exposes an `onAccepted` boundary callback. It invokes
the callback only after a valid accepted-job response has been received and its
command ID has been persisted, or when the submission joins an already active
observer. Entering `submitDecomposition` alone does not transfer recovery
ownership.

`confirmPrdAndDecompose` and the sidebar decomposition entry point use that
marker to distinguish the two recovery paths:

- Before acceptance, `STALE_STATE` clears pending command identities, refreshes
  the target Session once, and displays the fixed public message.
- After acceptance, durable `STALE_STATE` remains owned by
  `observeDecomposition`, which already removes the recovery key, clears the
  observer/pending identity, refreshes the Session, and emits a sanitized error.
  The caller does not perform a second refresh.

The public recovery text is now a shared constant across workspace stale-state
handlers, preventing raw backend comparison details from reaching the UI.

## TDD evidence

### RED

The two browser-level regressions were added before production changes and run
with:

```text
cd frontend/aios-main
npm test -- src/api/workspace.test.tsx -t "refreshes after synchronous approval|refreshes and rotates identity"
```

Result: 2 failed, 39 skipped. Both failures observed only one Session state GET
instead of the expected two. The first reproduced a synchronous `approve` 409
after another client advanced state; the second reproduced an immediate
`convert_to_work_item` POST 409 before any 202 response. The failure reason was
the missing immediate recovery refresh, not test setup or syntax.

### GREEN

After the minimal accepted-boundary marker and pre-accepted recovery were added:

```text
npm test -- src/api/workspace.test.tsx -t \
  "refreshes after synchronous approval|refreshes and rotates identity|recovers a stale decomposition"
```

Result: 3 passed, 38 skipped.

The regressions assert all required observable behavior:

- The synchronous stale approval attempt sends only `approve`; no decomposition
  POST or EventSource is created in that attempt.
- A Session GET occurs immediately after the 409.
- The retry uses a different command ID and the concurrently advanced
  `state_version`, then receives an accepted job and starts observation.
- An immediate decomposition POST 409 before 202 follows the same recovery path
  and rotates both identity and expected version on retry.
- Backend-only comparison details are absent from rendered output.
- The existing durable stale regression observes exactly two Session state GETs
  total (initial load plus observer-owned recovery), proving the caller does not
  double-refresh.

Focused workspace and observer verification:

```text
npm test -- src/api/workspace.test.tsx src/api/commandJobs.test.ts
```

Result: 2 files, 55 tests passed.

## Full verification

```text
cd frontend/aios-main && npm test
```

Result: 8 files, 82 tests passed.

```text
cd frontend/aios-main && npm run lint
```

Result: `tsc --noEmit` passed.

```text
cd frontend/aios-main && npm run build
```

Result: Vite 6.4.3 production build passed; 1,947 modules transformed.

`git diff --check` passed. No backend file changed, so backend verification was
not required for this follow-up.

## Final scoped re-review

The read-only review covered `e8a176e..a369608`, the surrounding workspace
control flow, both decomposition callers, asynchronous races, and the regression
tests. It reported 0 Critical, 0 Important, and 0 Minor findings and concluded
the change is ready to merge. Independent reviewer verification passed the full
workspace test file (41/41), TypeScript checking, and `git diff --check` while
the worktree remained clean.

## Commits

Implementation commit: `a369608` (`fix: recover pre-accepted stale decomposition`).
