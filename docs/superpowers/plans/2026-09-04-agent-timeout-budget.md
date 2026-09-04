# Agent Timeout Budget Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Raise the backend per-Agent timeout to 2000 seconds and keep frontend request budgets long enough for one- and two-Agent workflows to finish.

**Architecture:** The backend remains the authoritative per-process timeout through `Settings.codex_timeout_seconds`. The frontend keeps action-based budgets, with 75 seconds of overhead per possible backend Agent call: 2075 seconds for one Agent and 4150 seconds for two Agents. Failed calls without a persisted structured response remain non-resumable and must be retried as new commands.

**Tech Stack:** Python 3, FastAPI settings, pytest, TypeScript, React, Vitest

## Global Constraints

- Backend per-Agent timeout is exactly 2000 seconds.
- Frontend single-Agent timeout is exactly 2,075,000 milliseconds.
- Frontend double-Agent timeout is exactly 4,150,000 milliseconds.
- Standard non-Agent command timeout remains exactly 130,000 milliseconds.
- Do not materialize or resume a failed `AgentCall` whose `response` is empty.
- Keep the existing `AGENT_UNAVAILABLE` and `REQUEST_TIMEOUT` public error semantics.

---

### Task 1: Backend Agent Timeout Configuration

**Files:**
- Modify: `backend/tests/unit/test_config.py`
- Modify: `backend/app/config.py:8-49`
- Modify: `backend/.env.example:1-7`

**Interfaces:**
- Consumes: `Settings.from_env() -> Settings` and the optional `CODEX_TIMEOUT_SECONDS` process environment variable.
- Produces: `Settings.codex_timeout_seconds: int` with a default value of `2000` while preserving explicit environment overrides.

- [ ] **Step 1: Write failing tests for both backend default paths**

Append these tests to `backend/tests/unit/test_config.py`:

```python
def test_settings_default_codex_timeout_is_2000_seconds(tmp_path: Path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'gateway.db'}",
        codex_binary="codex",
        codex_home=tmp_path / "codex-home",
        codex_cwd=tmp_path,
    )

    assert settings.codex_timeout_seconds == 2000


def test_settings_environment_fallback_uses_2000_second_codex_timeout(monkeypatch):
    monkeypatch.setattr(config_module, "load_dotenv", lambda _path, *, override: None)
    monkeypatch.delenv("CODEX_TIMEOUT_SECONDS", raising=False)

    assert Settings.from_env().codex_timeout_seconds == 2000
```

- [ ] **Step 2: Run the backend tests and verify RED**

Run:

```bash
cd backend
.venv/bin/pytest tests/unit/test_config.py -q
```

Expected: the two new assertions fail because both current defaults are `900`.

- [ ] **Step 3: Implement the backend default**

In `backend/app/config.py`, change both backend defaults:

```python
codex_timeout_seconds: int = 2000
```

and:

```python
codex_timeout_seconds=int(os.getenv("CODEX_TIMEOUT_SECONDS", "2000")),
```

In `backend/.env.example`, change the documented setting to:

```dotenv
CODEX_TIMEOUT_SECONDS=2000
```

- [ ] **Step 4: Run the backend configuration tests and verify GREEN**

Run:

```bash
cd backend
.venv/bin/pytest tests/unit/test_config.py -q
```

Expected: all tests in `test_config.py` pass.

- [ ] **Step 5: Commit the backend change**

```bash
git add backend/tests/unit/test_config.py backend/app/config.py backend/.env.example
git commit -m "fix: extend backend agent timeout"
```

### Task 2: Frontend Command Timeout Budgets and Documentation

**Files:**
- Modify: `frontend/aios-main/src/api/client.test.ts:68-78`
- Modify: `frontend/aios-main/src/api/sessions.ts:13-29`
- Modify: `frontend/aios-main/CODEX_ARCHITECTURE_AND_API_SPEC.md:105`

**Interfaces:**
- Consumes: `commandTimeoutMs(action: CommandAction): number`.
- Produces: exact request budgets of `2_075_000` milliseconds for single-Agent work and `4_150_000` milliseconds for double-Agent work.

- [ ] **Step 1: Tighten the frontend timeout contract with exact failing assertions**

Replace the existing timeout-budget test in `frontend/aios-main/src/api/client.test.ts` with:

```typescript
it('allows the configured backend Agent calls to finish before timing out the command', () => {
  expect(commandTimeoutMs('create_spec')).toBe(4_150_000);
  expect(commandTimeoutMs('revise')).toBe(4_150_000);
  expect(commandTimeoutMs('restore_spec_version')).toBe(4_150_000);
  expect(commandTimeoutMs('convert_to_work_item')).toBe(4_150_000);
  expect(commandTimeoutMs('message')).toBe(2_075_000);
  expect(commandTimeoutMs('skip_clarification')).toBe(130_000);
});
```

- [ ] **Step 2: Run the frontend API test and verify RED**

Run:

```bash
cd frontend/aios-main
npm test -- src/api/client.test.ts
```

Expected: the Agent budget assertions fail with the current `975_000` and `1_950_000` values.

- [ ] **Step 3: Implement the frontend budgets**

In `frontend/aios-main/src/api/sessions.ts`, use:

```typescript
const STANDARD_COMMAND_TIMEOUT_MS = 130_000;
const SINGLE_AGENT_COMMAND_TIMEOUT_MS = 2_075_000;
const DOUBLE_AGENT_COMMAND_TIMEOUT_MS = 4_150_000;
```

Keep the existing action classification unchanged. In `frontend/aios-main/CODEX_ARCHITECTURE_AND_API_SPEC.md`, state that standard commands use 130 seconds, single-Agent commands use 2075 seconds, double-Agent commands use 4150 seconds, and each backend Agent invocation is capped at 2000 seconds.

- [ ] **Step 4: Run frontend tests and static checks**

Run:

```bash
cd frontend/aios-main
npm test -- src/api/client.test.ts
npm run lint
```

Expected: the API tests and TypeScript check pass.

- [ ] **Step 5: Commit the frontend change**

```bash
git add frontend/aios-main/src/api/client.test.ts frontend/aios-main/src/api/sessions.ts frontend/aios-main/CODEX_ARCHITECTURE_AND_API_SPEC.md
git commit -m "fix: align frontend agent timeout budgets"
```

### Task 3: Local Runtime Configuration and Integrated Verification

**Files:**
- Modify locally, not committed: `/Users/tangtang/Desktop/zkrAI/backend/.env`
- Verify: `backend/data/gateway.db`

**Interfaces:**
- Consumes: `CODEX_TIMEOUT_SECONDS` loaded at backend process startup.
- Produces: a restarted local backend whose effective Agent timeout is 2000 seconds.

- [ ] **Step 1: Update the ignored local runtime setting**

In `/Users/tangtang/Desktop/zkrAI/backend/.env`, replace the existing line with:

```dotenv
CODEX_TIMEOUT_SECONDS=2000
```

Do not stage or commit this ignored, machine-local file.

- [ ] **Step 2: Verify the effective configuration without exposing secrets**

Run:

```bash
cd /Users/tangtang/Desktop/zkrAI/backend
.venv/bin/python -c 'from app.config import Settings; print(Settings.from_env().codex_timeout_seconds)'
```

Expected: `2000`.

- [ ] **Step 3: Run the complete backend and frontend suites**

Run:

```bash
cd backend
.venv/bin/pytest -q
```

Expected: the full backend suite passes.

Run:

```bash
cd frontend/aios-main
npm test
npm run lint
npm run build
```

Expected: all frontend tests, type checking, and the production build pass.

- [ ] **Step 4: Confirm that the failed call is not resumable**

Run:

```bash
sqlite3 -header -column /Users/tangtang/Desktop/zkrAI/backend/data/gateway.db "select id,status,length(response) as response_bytes,error from agent_calls where id='430647b5-4d4f-4ace-bb02-7741d1e51445';"
```

Expected: status is `FAILED`, `response_bytes` is empty, and the error records the prior 900-second timeout. Do not modify this record or create work items from it.

- [ ] **Step 5: Restart and retry guidance**

Restart the local backend process so it reloads `.env`. Then reload the frontend and issue a new `convert_to_work_item` command from the approved Spec; do not reuse the failed command ID. If this environment does not expose control of the existing backend process, report the restart requirement to the user instead of terminating an unknown process.

- [ ] **Step 6: Record final repository state**

```bash
git status --short --branch
git log --oneline --decorate -6
```

Expected: only known runtime SQLite/WAL files remain modified outside the implementation commits. Remind the user that the new commits are ready to save or integrate into `main`.

