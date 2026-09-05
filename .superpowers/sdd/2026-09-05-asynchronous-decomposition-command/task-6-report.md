# Task 6 Report — Asynchronous Decomposition Documentation and Verification

## Documentation changes

`backend/readme.md` now documents `convert_to_work_item` as the one
asynchronous Session command. It provides the required `curl` examples for
submitting the command, reading its durable job snapshot, and opening its SSE
event stream. The README states that the server returns `202 Accepted` after
the command job is durable; records the `pending`, `processing`, `succeeded`,
and `failed` job states; identifies SSE as the live channel; and records the
frontend's independent 5,000 ms polling fallback.

The restart behavior is also documented precisely: startup changes unfinished
jobs to `PROCESS_INTERRUPTED` after their in-memory runner is lost, and an
identical command ID resubmission resets and reschedules that stored job.
Synchronous commands continue to return their completed `200` CommandResult.

## Verification

Commands were run from the paths shown below after the documentation edit.

```text
cd backend && .venv/bin/pytest -q
```

Result: exit 1; 613 passed, 2 failed, 2 warnings in 22.11 s. The only failures
were the established baseline failures:

- `test_spec_clarification_projection_ignores_stale_intake_rounds_and_matches_commands`
- `test_spec_clarification_followups_stay_bound_until_ready`

Both fail with the pre-existing empty `outstanding_questions` projection
(`IndexError` in `backend/tests/integration/test_sessions_api.py`). No other
test failed, so this run exactly matches the supplied baseline and does not
introduce a backend regression. The warnings are existing FastAPI/Starlette
TestClient deprecation warnings.

```text
cd frontend/aios-main && npm test && npm run lint && npm run build
```

Result: exit 0. Vitest: 8 files and 73 tests passed. `npm run lint` completed
`tsc --noEmit` with no errors. Vite 6.4.3 completed the production build after
transforming 1,947 modules.

```text
git diff --check
git status --short
git diff --stat origin/main...HEAD
```

`git diff --check` produced no output. Before the documentation commit,
`git status --short` showed only `M backend/readme.md`. The range stat contained
the previously committed Task 1–5 implementation files plus this scoped README
addition; no database, log, `.env`, credential, or generated frontend build
artifact was staged.

## Caveat

The backend suite cannot be reported fully green until the two named
clarification-projection baseline failures are resolved in their own scope.
Task 6 did not alter those tests or their implementation.

## Commit

Documentation commit: `75f82c9967a47a7ced9e18caffe6dd7b7bf93b7e`
(`docs: explain asynchronous decomposition commands`).
