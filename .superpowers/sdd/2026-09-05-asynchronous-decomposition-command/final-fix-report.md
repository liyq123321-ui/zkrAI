# Final Fix Report — Asynchronous Decomposition Command

## Outcome

All three Important findings and both Minor findings from the whole-branch review
are addressed. `convert_to_work_item` remains the only asynchronous Session
command; synchronous commands retain their completed `200 CommandResult`
contract and legacy command-ID compatibility.

## Finding resolution

### Important 1 — durable `STALE_STATE` recovery

- Durable `STALE_STATE` snapshots now reject the observer completion with an
  actionable `ApiError` carrying status `409`, rather than looking like a status
  `0` transport failure.
- The workspace removes the unusable saved job ID, clears its observer ownership
  and pending command identity, refreshes the current Session, and reports a
  fixed public recovery message.
- The next submission is generated with a new command ID and the refreshed
  `state_version`. A two-client/concurrent-state browser regression verifies the
  old and new POST bodies, the replaced EventSource URL, and the absence of the
  durable internal diagnostic string in the UI.
- A coordinator regression verifies that a real `StaleState` exception persists
  only the classified public `STALE_STATE` code/message and does not expose the
  exception's internal path or comparison detail.

### Important 2 — URL-safe asynchronous command IDs

- `SessionCommandRequest` conditionally validates only
  `convert_to_work_item` IDs against
  `^[A-Za-z0-9](?:[A-Za-z0-9._~-]{0,254})$`. This accepts frontend UUIDs and
  URL-unreserved IDs while rejecting query, fragment, slash, whitespace, and
  Unicode path hazards.
- The conditional constraint is also emitted in the runtime OpenAPI schema with
  `if`/`then`, so synchronous command IDs remain backward compatible.
- A `202` response now includes `Location` equal to `status_url`; that response
  header is documented in OpenAPI. The existing accepted-job test follows the
  returned URL and confirms it is usable.
- `docs/openapi.json` was regenerated from `create_app().openapi()`, not edited by
  hand. The backend README now documents the asynchronous ID and `Location`
  contract.

### Important 3 — terminal reconciliation lifecycle

- Each decomposition observation owns an `AbortController` covering both job
  observation and terminal resource reconciliation.
- Project switch and unmount abort those browser-owned lifecycles and close SSE
  and polling without sending any backend cancellation.
- `refreshResources` receives the lifecycle signal. Checks before the state
  application and after the refresh suppress late task selection, recovery-key
  removal, and completion/error effects after cancellation.
- Controller ownership is tracked separately from observer ownership so a stale
  terminal observer can be discarded before its Session refresh while that
  refresh remains cancellable.
- Regressions cover terminal delivery followed by a delayed refresh, immediate
  project switch, late success, late error, and unmount. In every cancellation
  case the recovery key remains for the next visit.

### Minor A — immediate reload status read

Reload recovery opts into one immediate durable status GET through the same
runtime validator and monotonic reconciliation path used by later polling and
SSE. The recursive non-overlapping loop schedules its next read 5,000 ms after
the immediate request completes. Tests verify an immediate first call, no overlap
while it is unresolved, no duplicate observer, and exactly one later poll.

### Minor B — complete command-result validation

The observer now validates every required `SessionStateDto` field it consumes:
Session/project identity, phase, integer version, nullable current Spec fields,
known legal actions, next action, clarification-question array shape, and review
finding array shape. A partial terminal state with a high `status_version` is
rejected and cannot poison the monotonic cursor; a subsequent lower valid
terminal snapshot still completes the job.

## TDD evidence

### RED

The regressions were written and observed failing before their corresponding
production changes:

- `commandJobs.test.ts`: the immediate-poll assertion received 0 calls and the
  durable stale assertion received status `0` instead of `409` (2 failures).
- After adding only immediate polling, the partial Session object was accepted
  and advanced the observer (`onStatus` was called once), isolating the runtime
  validation gap.
- `workspace.test.tsx`: the concurrent stale test retained the first command ID
  in local storage instead of clearing it.
- The lifecycle group failed all three regressions: late success selected the
  old task, late error rendered `NETWORK_ERROR` in the new project, and unmount
  removed the recovery key.
- The backend URL contract selection produced 7 failures: missing conditional
  OpenAPI schema, missing `Location`, and `202` responses for all five unsafe ID
  classes. The synchronous legacy-ID control already passed.
- A final OpenAPI-header RED run failed on a missing documented `Location`
  response header.

These failures also provide mutation evidence: restoring status `0`, accepting a
record-only Session state, delaying the first recovery GET, omitting the
post-refresh cancellation check, retaining the stale saved ID, or removing the
conditional ID validator is detected by a focused regression.

### GREEN

Focused verification after the minimal fixes:

```text
frontend: npm test -- src/api/commandJobs.test.ts
14 passed

frontend: npm test -- src/api/workspace.test.tsx src/api/commandJobs.test.ts
2 files, 53 passed

backend: pytest tests/integration/test_command_jobs.py tests/integration/test_sessions_api.py
45 passed, 2 allowed baseline failures

backend URL/OpenAPI focus
8 passed, 28 deselected

frontend typecheck
tsc --noEmit passed
```

## Final verification

Fresh commands were run on the final implementation tree:

```text
cd backend && .venv/bin/pytest -q
621 passed, 2 failed, 2 warnings
```

The only failures are the two explicitly accepted clarification-projection
baseline failures:

- `test_spec_clarification_projection_ignores_stale_intake_rounds_and_matches_commands`
- `test_spec_clarification_followups_stay_bound_until_ready`

```text
cd frontend/aios-main && npm test
8 files, 80 passed

cd frontend/aios-main && npm run lint
tsc --noEmit passed

cd frontend/aios-main && npm run build
Vite 6.4.3; 1,947 modules transformed; production build passed
```

OpenAPI snapshot verification:

```text
saved docs/openapi.json == create_app().openapi()
OpenAPI snapshot matches runtime schema
```

`git diff --check` produced no output. The production build created no tracked
artifact. No database, log, environment, credential, or unrelated source file is
part of this fix.

## Unresolved items

No review finding remains unresolved. The two named backend baseline failures are
unchanged and remain outside this task's authorized scope.
