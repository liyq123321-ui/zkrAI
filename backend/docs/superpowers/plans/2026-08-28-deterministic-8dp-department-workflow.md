# Deterministic 8-DP Department Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace firstFlight's prompt-controlled workflow with a deterministic Python 8-DP graph that delegates Epic work through fixed departments to isolated Codex Workers and enforces checkpointed, hierarchical review and closure.

**Architecture:** A zero-dependency domain graph owns all transitions while SQLite stores versioned workflow state, commands, jobs, checkpoints, contracts, audit records, and outbox events. FastAPI exposes explicit command/state/work-item/event APIs; Codex CLI runs only typed, single-purpose nodes and isolated Worker worktrees. Repository artifacts are eventually materialized from the database and all merge, review, and closure authority remains in Python.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic, SQLAlchemy, SQLite, asyncio subprocesses, Codex CLI JSONL/output schemas, Git worktrees, pytest, pytest-asyncio, and HTTPX.

## Global Constraints

- The graph engine has no third-party graph dependency; phases and transitions are explicit Python data.
- The Full path is the only workflow path in version one.
- `project_path` and `base_commit` are immutable for a workflow run.
- The Planner may use only the fixed eight-department capability table.
- Requirement clarification and Department Council discussion each stop after three rounds; the Planner adjudicates unresolved items.
- Model output never controls phase, status, approval, or next action.
- Every agent output must validate against a node-specific JSON Schema; repair is limited to two attempts.
- Every Worker uses its own Git worktree, branch, process, working directory, workspace-write sandbox, network-disabled policy, read-only Skill subset, and `allowed_paths[]` validation.
- Workers submit commit, diff, test evidence, and a completion claim but cannot merge, push, approve, or close work.
- Child, department, integration, and Epic closure are separate supervisor gates.
- SQLite is authoritative; repository artifacts use an idempotent outbox and version/hash reconciliation.
- DP-0 through DP-7 create immutable checkpoints.
- The system never merges or pushes the main branch and always reminds the user to save Git when a candidate version is available.
- Preserve existing conversation history and keep legacy sessions usable during migration.

## File Structure

The implementation introduces focused packages instead of growing the current service modules:

This remains one ordered plan because the apparent subsystems are not independently deployable: department coordination depends on the durable graph, Worker isolation depends on validated WorkItems, and recovery depends on every prior persistence contract. The fourteen task boundaries still provide independent test and review gates.

```text
app/
  config.py                      # Immutable environment-derived settings
  api/
    chat.py                       # Legacy compatibility facade
    workflows.py                  # Commands, state, work items, event endpoints
  artifacts/
    materializer.py               # Outbox-to-repository atomic writer
  database/
    database.py                   # Engine/session factory and initialization
    migrations.py                 # Forward-only lightweight SQLite migrations
    models.py                     # Conversation plus normalized workflow records
  execution/
    codex.py                      # Structured Codex CLI adapter
    evidence.py                   # Commit/diff/test evidence collection
    merge.py                      # Dependency-ordered department/integration merges
    node_schemas.py               # Node-specific validated output contracts
    prompts.py                    # Confined node prompt rendering
    review.py                     # Read-only Lead and Planner reviews
    sandbox.py                    # Worktree/process/network/path isolation
    skill_installer.py            # Administrative verified Skill acquisition
    skills.py                     # Curated immutable Skill registry and resolver
  schemas/
    chat.py                       # Legacy request schema
    workflow.py                   # Command/state/work-item/event schemas
  workflow/
    commands.py                   # Transactional command application
    departments.py                # Fixed capability table and Council rounds
    graph.py                      # Transition table and pure graph engine
    jobs.py                       # Durable job claiming and execution
    recovery.py                   # Checkpoint and lease reconciliation
    types.py                      # Enums and immutable domain value objects
    workitems.py                  # Work hierarchy and closure guards
  services/
    codex_runner.py               # Temporary compatibility wrapper, later removed
    session_service.py            # Conversation compatibility, delegates to v2
main.py                           # Startup migrations, routers, worker lifecycle
prompts/
  nodes/                          # Eleven single-purpose node templates
tests/
  conftest.py
  helpers/
    clock.py                       # Deterministic UTC clock for lease/recovery tests
    fake_codex.py                  # Scripted async subprocess replacement
  unit/
  integration/
  e2e/
```

---

### Task 1: Establish the Test and Configuration Foundation

**Files:**
- Create: `app/config.py`
- Create: `tests/conftest.py`
- Create: `tests/unit/test_config.py`
- Modify: `requirements.txt`
- Modify: `app/database/database.py`

**Interfaces:**
- Produces: `Settings.from_env() -> Settings`
- Produces: `create_engine_for_url(database_url: str) -> Engine`
- Produces: pytest fixtures `db_session`, `project_repo`, and `settings`

- [ ] **Step 1: Add test dependencies and write the failing settings test**

```python
# tests/unit/test_config.py
from pathlib import Path

from app.config import Settings


def test_settings_require_absolute_paths(tmp_path: Path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'gateway.db'}",
        codex_binary="codex",
        codex_home=tmp_path / "codex-home",
        skill_store=tmp_path / "skills",
        agent_job_poll_ms=100,
    )
    assert settings.codex_home.is_absolute()
    assert settings.skill_store.is_absolute()
```

Add `pytest`, `pytest-asyncio`, and `httpx` to `requirements.txt`.

- [ ] **Step 2: Run the test and verify the missing module failure**

Run: `pytest tests/unit/test_config.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'app.config'`.

- [ ] **Step 3: Implement immutable settings and an injectable engine factory**

```python
# app/config.py
from dataclasses import dataclass
from pathlib import Path
import os


@dataclass(frozen=True)
class Settings:
    database_url: str
    codex_binary: str
    codex_home: Path
    skill_store: Path
    agent_job_poll_ms: int = 250

    @classmethod
    def from_env(cls) -> "Settings":
        root = Path(__file__).resolve().parents[1]
        return cls(
            database_url=os.getenv("DATABASE_URL", f"sqlite:///{root / 'data' / 'gateway.db'}"),
            codex_binary=os.getenv("CODEX_BINARY", "codex"),
            codex_home=Path(os.getenv("CODEX_RUNTIME_HOME", str(Path.home()))).resolve(),
            skill_store=Path(os.getenv("SKILL_STORE", str(root / "data" / "skills"))).resolve(),
            agent_job_poll_ms=int(os.getenv("AGENT_JOB_POLL_MS", "250")),
        )
```

Refactor `app/database/database.py` so production imports use `Settings.from_env().database_url`, while tests can call `create_engine_for_url("sqlite://")` with `StaticPool`.

- [ ] **Step 4: Create isolated database and Git repository fixtures**

```python
# tests/conftest.py
import subprocess
from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker

from app.database.database import Base, create_engine_for_url


@pytest.fixture
def db_session():
    engine = create_engine_for_url("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session


@pytest.fixture
def project_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "project"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "tests@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Workflow Tests"], cwd=repo, check=True)
    (repo / "README.md").write_text("fixture\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "fixture"], cwd=repo, check=True)
    return repo


@pytest.fixture
def settings(tmp_path: Path):
    from app.config import Settings

    return Settings(
        database_url="sqlite://",
        codex_binary="codex",
        codex_home=tmp_path / "codex-home",
        skill_store=tmp_path / "skills",
        agent_job_poll_ms=10,
    )
```

- [ ] **Step 5: Run the foundation tests**

Run: `pytest tests/unit/test_config.py -v`

Expected: PASS.

- [ ] **Step 6: Commit the foundation**

```bash
git add requirements.txt app/config.py app/database/database.py tests/conftest.py tests/unit/test_config.py
git commit -m "test: establish workflow test foundation"
```

---

### Task 2: Add the Durable Workflow Schema and Forward-Only Migration Runner

**Files:**
- Modify: `app/database/models.py`
- Create: `app/database/migrations.py`
- Create: `tests/unit/test_migrations.py`
- Modify: `main.py`

**Interfaces:**
- Consumes: `create_engine_for_url(database_url)` from Task 1
- Produces: `run_migrations(engine: Engine) -> None`
- Produces: SQLAlchemy models `WorkflowRun`, `WorkflowCommand`, `WorkflowCheckpoint`, `DecisionRecord`, `AuditEvent`, `AgentJob`, `AgentRun`, `InterfaceContract`, `ReviewReceipt`, `ArtifactVersion`, `OutboxEvent`, `SkillPackage`

- [ ] **Step 1: Write a failing migration idempotency test**

```python
# tests/unit/test_migrations.py
from sqlalchemy import inspect, text

from app.database.database import create_engine_for_url
from app.database.migrations import run_migrations


def test_workflow_schema_migration_is_idempotent():
    engine = create_engine_for_url("sqlite://")
    run_migrations(engine)
    run_migrations(engine)
    names = set(inspect(engine).get_table_names())
    assert {"workflow_runs", "workflow_commands", "workflow_checkpoints", "agent_jobs", "outbox_events"} <= names
    with engine.connect() as connection:
        count = connection.execute(text("select count(*) from schema_migrations")).scalar_one()
    assert count == 1
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `pytest tests/unit/test_migrations.py -v`

Expected: FAIL because `app.database.migrations` does not exist.

- [ ] **Step 3: Define normalized models and constraints**

Add the records named in **Interfaces** to `app/database/models.py`. Store enum values as strings, JSON arrays/objects as canonical JSON text, hashes as 64-character strings, and timestamps in UTC. Add these unique constraints:

```python
UniqueConstraint("session_id", name="uq_workflow_run_session")
UniqueConstraint("run_id", "command_id", name="uq_workflow_command_idempotency")
UniqueConstraint("idempotency_key", name="uq_agent_job_idempotency")
UniqueConstraint("run_id", "state_version", "decision_point", name="uq_checkpoint_boundary")
UniqueConstraint("event_key", name="uq_outbox_event_key")
```

Extend `Conversation` with immutable nullable `project_path` and `workflow_type` so legacy rows remain valid. Extend `WorkItem` with kind, objective, scope, exclusions, interface/dependency/skill/path/test JSON, supervisor, submitted commit, diff hash, evidence hash, and timestamps.

- [ ] **Step 4: Implement one forward-only migration**

```python
# app/database/migrations.py
from collections.abc import Callable
from sqlalchemy import Engine, inspect, text


Migration = tuple[int, Callable[[Engine], None]]


def run_migrations(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(text("create table if not exists schema_migrations (version integer primary key, applied_at text not null)"))
    applied = _applied_versions(engine)
    for version, migration in MIGRATIONS:
        if version in applied:
            continue
        migration(engine)
        with engine.begin() as connection:
            connection.execute(
                text("insert into schema_migrations(version, applied_at) values (:version, CURRENT_TIMESTAMP)"),
                {"version": version},
            )
```

Migration `1` inspects existing tables and columns, creates only missing v2 tables through model metadata, and adds nullable compatibility columns to existing tables. It must not rewrite conversation or message rows.

Define `_migration_1(engine)` in the same module and register it exactly once with `MIGRATIONS: tuple[Migration, ...] = ((1, _migration_1),)`. The test first creates a legacy-only conversation/message schema, inserts one conversation and one message, runs the migration twice, and verifies both legacy rows remain byte-for-byte unchanged.

- [ ] **Step 5: Replace import-time `create_all` with startup migrations**

In `main.py`, add a FastAPI lifespan handler that calls `run_migrations(engine)` before accepting requests. Keep no database mutation at module import time.

- [ ] **Step 6: Run migration and legacy preservation tests**

Run: `pytest tests/unit/test_migrations.py -v`

Expected: PASS, including two consecutive migration calls and preserved legacy rows.

- [ ] **Step 7: Commit the schema**

```bash
git add app/database/models.py app/database/migrations.py main.py tests/unit/test_migrations.py
git commit -m "feat: add durable workflow schema"
```

---

### Task 3: Implement the Pure 8-DP Graph

**Files:**
- Create: `app/workflow/__init__.py`
- Create: `app/workflow/types.py`
- Create: `app/workflow/graph.py`
- Create: `tests/unit/test_workflow_graph.py`

**Interfaces:**
- Produces: enums `Phase`, `DecisionPoint`, `CommandAction`, `RunStatus`, `ExecutionMode`, `DepartmentId`
- Produces: `WorkflowSnapshot`, `TransitionContext`, `TransitionResult`
- Produces: `WorkflowGraphEngine.apply(snapshot, action, context) -> TransitionResult`
- Produces: `WorkflowGraphEngine.legal_actions(snapshot) -> tuple[CommandAction, ...]`

- [ ] **Step 1: Write table-driven failing transition tests**

```python
# tests/unit/test_workflow_graph.py
import pytest

from app.workflow.graph import IllegalTransition, WorkflowGraphEngine
from app.workflow.types import CommandAction, DecisionPoint, Phase, TransitionContext, WorkflowSnapshot


@pytest.mark.parametrize(
    ("phase", "dp", "action", "next_phase"),
    [
        (Phase.EXPLORING, DecisionPoint.DP0, CommandAction.APPROVE, Phase.SPECIFYING),
        (Phase.SPECIFYING, DecisionPoint.DP1, CommandAction.APPROVE, Phase.BRIDGING),
        (Phase.BRIDGING, DecisionPoint.DP3, CommandAction.APPROVE, Phase.APPROVED_FOR_BUILD),
        (Phase.APPROVED_FOR_BUILD, DecisionPoint.DP4, CommandAction.SELECT_EXECUTION_MODE, Phase.EXECUTING),
        (Phase.EXECUTING, DecisionPoint.DP5, CommandAction.APPROVE, Phase.DEBUGGING),
        (Phase.CLOSING, DecisionPoint.DP7, CommandAction.APPROVE, Phase.CLOSING),
    ],
)
def test_approved_transitions(phase, dp, action, next_phase):
    result = WorkflowGraphEngine().apply(
        WorkflowSnapshot.example(phase=phase, decision_point=dp),
        action,
        TransitionContext.all_guards_pass(),
    )
    assert result.snapshot.current_phase is next_phase


def test_model_cannot_submit_state_change():
    snapshot = WorkflowSnapshot.example(phase=Phase.EXPLORING, decision_point=DecisionPoint.DP0)
    with pytest.raises(IllegalTransition):
        WorkflowGraphEngine().apply(snapshot, CommandAction.MESSAGE, TransitionContext.all_guards_pass())
```

- [ ] **Step 2: Run the test and verify the missing graph failure**

Run: `pytest tests/unit/test_workflow_graph.py -v`

Expected: FAIL because the workflow package is not implemented.

- [ ] **Step 3: Define immutable types and the explicit transition table**

```python
# app/workflow/graph.py
TRANSITIONS = {
    (Phase.EXPLORING, DecisionPoint.DP0, CommandAction.APPROVE): Phase.SPECIFYING,
    (Phase.SPECIFYING, DecisionPoint.DP1, CommandAction.APPROVE): Phase.BRIDGING,
    (Phase.BRIDGING, DecisionPoint.DP2, CommandAction.APPROVE): Phase.BRIDGING,
    (Phase.BRIDGING, DecisionPoint.DP3, CommandAction.APPROVE): Phase.APPROVED_FOR_BUILD,
    (Phase.APPROVED_FOR_BUILD, DecisionPoint.DP4, CommandAction.SELECT_EXECUTION_MODE): Phase.EXECUTING,
    (Phase.EXECUTING, DecisionPoint.DP5, CommandAction.APPROVE): Phase.DEBUGGING,
    (Phase.EXECUTING, DecisionPoint.DP6, CommandAction.APPROVE): Phase.CLOSING,
    (Phase.DEBUGGING, DecisionPoint.DP6, CommandAction.APPROVE): Phase.EXECUTING,
    (Phase.CLOSING, DecisionPoint.DP7, CommandAction.APPROVE): Phase.CLOSING,
}
```

Implement `REVISE` as a phase-local transition, `ABANDON` as a transition to `Phase.ABANDONED`, and legal `MESSAGE` handling only when `waiting_for_user` is true and no approval action is required. DP-7 approval sets `RunStatus.COMPLETED`.

- [ ] **Step 4: Implement guards and derived state**

`TransitionContext` contains named booleans for artifacts materialized, requirements confirmed, contracts frozen, execution mode selected, dependencies closed, department tests passed, integration tests passed, and archive ready. `apply()` raises `GuardRejected` with a stable reason code when any required guard is false. It derives `waiting_for_user`, `approval_required`, legal actions, and `next_action`; callers cannot supply them.

- [ ] **Step 5: Run all graph tests**

Run: `pytest tests/unit/test_workflow_graph.py -v`

Expected: all parameterized transitions, rejection paths, clarification limit, abandonment, DP-6 recovery, and DP-7 completion tests PASS.

- [ ] **Step 6: Commit the graph**

```bash
git add app/workflow tests/unit/test_workflow_graph.py
git commit -m "feat: implement deterministic 8-DP graph"
```

---

### Task 4: Apply Commands Transactionally with Checkpoints and Audit

**Files:**
- Create: `app/workflow/commands.py`
- Create: `tests/integration/test_command_service.py`

**Interfaces:**
- Consumes: `WorkflowGraphEngine` from Task 3 and workflow models from Task 2
- Produces: `CommandRequestData(command_id, action, expected_state_version, message, payload)`
- Produces: `CommandResult(run_id, state_version, phase, status, replayed)`
- Produces: `CommandService.create_run(session_id, project_path, base_commit) -> WorkflowRun`
- Produces: `CommandService.apply(run_id, command) -> CommandResult`

- [ ] **Step 1: Write failing idempotency and stale-version tests**

```python
# tests/integration/test_command_service.py
from pathlib import Path

import pytest

from app.workflow.commands import CommandRequestData, CommandService, StaleStateVersion
from app.workflow.types import CommandAction


def test_duplicate_command_returns_original_result(db_session, project_repo: Path):
    service = CommandService(db_session)
    run = service.create_run("session-1", project_repo, "HEAD")
    command = CommandRequestData("cmd-1", CommandAction.APPROVE, 0, None, {})
    first = service.apply(run.id, command)
    second = service.apply(run.id, command)
    assert second == first
    assert second.replayed is True


def test_stale_version_has_no_side_effect(db_session, project_repo: Path):
    service = CommandService(db_session)
    run = service.create_run("session-2", project_repo, "HEAD")
    service.apply(run.id, CommandRequestData("cmd-1", CommandAction.APPROVE, 0, None, {}))
    with pytest.raises(StaleStateVersion):
        service.apply(run.id, CommandRequestData("cmd-2", CommandAction.REVISE, 0, "change", {}))
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `pytest tests/integration/test_command_service.py -v`

Expected: FAIL because `CommandService` does not exist.

- [ ] **Step 3: Implement run creation and immutable Git identity**

Resolve `project_path` to an absolute real path, verify it is a Git worktree, resolve `base_commit` to a full commit SHA, and store both once. Reject a second run for the same session or any later attempt to change either field.

- [ ] **Step 4: Implement atomic command application**

Use a SQLite write transaction to:

1. return a stored result when `(run_id, command_id)` already exists;
2. compare `expected_state_version`;
3. build the graph snapshot and guard context;
4. apply the pure transition;
5. increment `state_version` once;
6. write `WorkflowCommand`, `DecisionRecord`, and `AuditEvent`;
7. write a `WorkflowCheckpoint` for each DP boundary;
8. enqueue required AgentJobs and OutboxEvents;
9. commit all records together.

- [ ] **Step 5: Add command rollback and clarification-cap tests**

Assert that a guard failure leaves `state_version`, checkpoints, jobs, and outbox counts unchanged. Assert that clarification rounds 1, 2, and 3 are accepted, round 4 is rejected, and the Planner assumption DecisionRecord becomes the only DP-1 path.

- [ ] **Step 6: Run the command suite**

Run: `pytest tests/integration/test_command_service.py -v`

Expected: PASS with duplicate, stale, rollback, checkpoint, and clarification cases.

- [ ] **Step 7: Commit command processing**

```bash
git add app/workflow/commands.py tests/integration/test_command_service.py
git commit -m "feat: apply workflow commands transactionally"
```

---

### Task 5: Expose Workflow APIs and Preserve `/chat`

**Files:**
- Create: `app/schemas/workflow.py`
- Create: `app/api/workflows.py`
- Modify: `app/api/chat.py`
- Modify: `app/schemas/chat.py`
- Modify: `main.py`
- Modify: `tests/conftest.py`
- Create: `tests/integration/test_workflow_api.py`

**Interfaces:**
- Consumes: `CommandService` from Task 4
- Produces: `POST /sessions`, `POST /sessions/{id}/commands`, `GET /sessions/{id}/state`, `GET /sessions/{id}/work-items`, `GET /sessions/{id}/events`
- Produces: `create_app(settings: Settings, session_factory: sessionmaker) -> FastAPI`
- Preserves: `POST /chat` streaming facade and `X-Session-ID`

- [ ] **Step 1: Write failing API contract tests**

```python
# tests/integration/test_workflow_api.py
def test_create_session_requires_project_path(client, project_repo):
    response = client.post("/sessions", json={"project_path": str(project_repo)})
    assert response.status_code == 201
    body = response.json()
    assert body["state_version"] == 0
    assert body["current_phase"] == "exploring"
    assert body["legal_actions"] == ["approve", "abandon"]


def test_stale_command_maps_to_409(client, workflow_run):
    response = client.post(
        f"/sessions/{workflow_run.session_id}/commands",
        json={"command_id": "stale", "action": "approve", "expected_state_version": 99, "payload": {}},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "stale_state_version"
```

- [ ] **Step 2: Run the API tests and verify they fail**

Run: `pytest tests/integration/test_workflow_api.py -v`

Expected: FAIL with missing routes.

- [ ] **Step 3: Define transport schemas**

Create Pydantic models for session creation, command input, workflow state, WorkItem tree, event envelope, and stable error detail. Validate `command_id` as a nonempty string, `expected_state_version >= 0`, and execution mode payload as `inline`, `batch-inline`, or `sdd`.

- [ ] **Step 4: Add an application factory and API fixtures**

Move router registration and lifespan construction into `create_app(settings, session_factory)`. Keep `app = create_app(Settings.from_env(), SessionLocal)` for Uvicorn. In `tests/conftest.py`, add a `client` fixture using `fastapi.testclient.TestClient(create_app(settings, test_session_factory))` and a `workflow_run` fixture created through `CommandService` against `project_repo`.

- [ ] **Step 5: Implement routes and error mapping**

Map `StaleStateVersion` and illegal transitions to 409, schema-invalid agent results to 422, workflow waits to 423, and transient execution failures to 503. State responses include `current_phase`, `current_decision_point`, `status`, `state_version`, `waiting_for_user`, `approval_required`, `legal_actions`, and `next_action`.

- [ ] **Step 6: Convert `/chat` into a compatibility command**

Add optional `project_path`, `command_id`, and `expected_state_version` to `ChatRequest`. New sessions require `project_path` and use `full_flow_v2`; existing legacy sessions continue through the old path. A v2 chat message calls `CommandService.apply()` with `CommandAction.MESSAGE` and streams durable events rather than parsing assistant text into WorkItems.

- [ ] **Step 7: Run API and legacy compatibility tests**

Run: `pytest tests/integration/test_workflow_api.py tests/integration/test_command_service.py -v`

Expected: PASS, including unchanged legacy conversation retrieval and the `X-Session-ID` header.

- [ ] **Step 8: Commit the API layer**

```bash
git add app/api app/schemas main.py tests/conftest.py tests/integration/test_workflow_api.py
git commit -m "feat: expose workflow command APIs"
```

---

### Task 6: Add Typed Node Schemas and the Structured Codex Adapter

**Files:**
- Create: `app/execution/__init__.py`
- Create: `app/execution/codex.py`
- Create: `app/execution/node_schemas.py`
- Create: `app/execution/prompts.py`
- Create: `prompts/nodes/clarification.txt`
- Create: `prompts/nodes/architecture.txt`
- Create: `prompts/nodes/allocation.txt`
- Create: `prompts/nodes/council.txt`
- Create: `prompts/nodes/decomposition.txt`
- Create: `prompts/nodes/artifact.txt`
- Create: `prompts/nodes/contract.txt`
- Create: `prompts/nodes/worker.txt`
- Create: `prompts/nodes/review.txt`
- Create: `prompts/nodes/debug.txt`
- Create: `prompts/nodes/release.txt`
- Create: `tests/helpers/fake_codex.py`
- Create: `tests/unit/test_codex_adapter.py`
- Create: `tests/unit/test_node_prompts.py`
- Modify: `app/services/codex_runner.py`

**Interfaces:**
- Produces: `NodeName` enum and Pydantic result models for clarification, architecture, allocation, Council, decomposition, artifact writing, contract building, Worker completion, review, debugging, and release
- Produces: `CodexRequest(node_name, prompt, cwd, sandbox, output_schema, skill_paths)`
- Produces: `CodexResult(events, output, stderr, exit_code, repair_attempts)`
- Produces: `CodexAdapter.execute(request) -> CodexResult` with an injectable async `process_factory`
- Produces: `NodePromptBuilder.build(node_name, snapshot, artifact_refs, workitem, skill_refs, allowed_paths) -> str`

- [ ] **Step 1: Write failing adapter argument and validation tests**

```python
# tests/unit/test_codex_adapter.py
import pytest

from app.execution.codex import CodexAdapter, CodexRequest, InvalidNodeOutput
from app.execution.node_schemas import NodeName, WorkerCompletion
from tests.helpers.fake_codex import FakeProcessFactory


@pytest.mark.asyncio
async def test_adapter_uses_json_schema_and_workspace_sandbox(tmp_path):
    fake_codex = FakeProcessFactory()
    fake_codex.return_json({"claim": "done", "commit": "a" * 40, "tests": []})
    adapter = CodexAdapter(binary="codex", runtime_home=tmp_path, process_factory=fake_codex)
    result = await adapter.execute(CodexRequest.for_worker(tmp_path, "implement exact work item"))
    assert isinstance(result.output, WorkerCompletion)
    assert "--json" in fake_codex.argv
    assert "--output-schema" in fake_codex.argv
    assert fake_codex.option("--sandbox") == "workspace-write"
```

- [ ] **Step 2: Run the adapter test and verify it fails**

Run: `pytest tests/unit/test_codex_adapter.py -v`

Expected: FAIL because the execution package is missing.

- [ ] **Step 3: Define every node result schema**

Each result model contains only the node's proposal or evidence fields. Do not include `current_phase`, `status`, `approval_required`, `waiting_for_user`, or `next_action`. `DepartmentAllocationResult.department_ids` validates against the fixed department enum. `EmployeeSubWorkItemResult` requires objective, scope, exclusions, inputs, outputs, interfaces, dependency IDs, Skill IDs, allowed paths, tests, acceptance criteria, and supervisor ID.

- [ ] **Step 4: Write and test confined node prompts**

Each template states one immutable role and objective, instructs the node to use only supplied artifacts, WorkItems, contracts, Skills, and paths, and requires output matching the supplied schema. `tests/unit/test_node_prompts.py` parameterizes all eleven `NodeName` values, verifies a template exists, verifies the current user message appears once, and rejects rendered prompts containing instructions to change workflow phase, merge, push, or close a task outside that node's authority.

- [ ] **Step 5: Implement JSONL execution and two repairs**

Invoke:

```python
argv = [
    request.binary,
    "exec",
    "--json",
    "--output-schema",
    str(schema_path),
    "--sandbox",
    request.sandbox,
    request.prompt,
]
```

Set `cwd` from the request and set `CODEX_HOME` without overwriting the process `HOME`. Parse each JSONL event, select the final structured output event, and validate it with the node model. On validation failure, resume the same Codex thread with the validation errors; stop after two repair attempts and raise `InvalidNodeOutput`.

Define `FakeProcessFactory` in `tests/helpers/fake_codex.py` with `__call__(*argv, cwd, env, stdout, stderr)`, a queued JSONL byte stream, captured `argv`, and deterministic `wait()`/`terminate()` behavior. Its `return_json()` method queues one final structured-output event, and `option(name)` returns the following argument. This keeps tests offline, is reused by the end-to-end harness, and verifies the exact subprocess contract.

- [ ] **Step 6: Make the old runner a thin compatibility wrapper**

Remove hard-coded `/home/zkr` paths and substring JSON parsing. `execute_stream()` delegates to `CodexAdapter` only for legacy sessions; v2 code imports the adapter directly.

- [ ] **Step 7: Run prompt, valid, repair, malformed JSONL, nonzero exit, and timeout tests**

Run: `pytest tests/unit/test_codex_adapter.py tests/unit/test_node_prompts.py -v`

Expected: PASS for one valid result, one repaired result, a two-repair rejection, preserved stderr, and process termination on timeout.

- [ ] **Step 8: Commit structured Codex execution**

```bash
git add app/execution app/services/codex_runner.py prompts/nodes tests/helpers/fake_codex.py tests/unit/test_codex_adapter.py tests/unit/test_node_prompts.py
git commit -m "feat: add schema-validated Codex nodes"
```

---

### Task 7: Implement Durable Agent Jobs and Resumable Events

**Files:**
- Create: `app/workflow/jobs.py`
- Create: `tests/helpers/clock.py`
- Create: `tests/integration/test_agent_jobs.py`
- Modify: `app/api/workflows.py`
- Modify: `main.py`

**Interfaces:**
- Produces: `AgentJobQueue.enqueue()`, `claim_next()`, `renew_lease()`, `complete()`, `fail()`
- Produces: `AgentJobWorker.run_once() -> bool`
- Produces: ordered `AuditEvent.sequence_id` consumed by SSE

- [ ] **Step 1: Write failing duplicate and lease tests**

```python
# tests/integration/test_agent_jobs.py
from datetime import timedelta

from app.workflow.jobs import AgentJobQueue
from tests.helpers.clock import FakeClock


def test_job_idempotency_and_expired_lease(db_session):
    clock = FakeClock()
    queue = AgentJobQueue(db_session, clock=clock)
    first = queue.enqueue("run-1", "architecture", 2, None, 1, {})
    second = queue.enqueue("run-1", "architecture", 2, None, 1, {})
    assert second.id == first.id
    claimed = queue.claim_next("worker-a", lease_for=timedelta(seconds=30))
    clock.advance(seconds=31)
    reclaimed = queue.claim_next("worker-b", lease_for=timedelta(seconds=30))
    assert reclaimed.id == claimed.id
    assert reclaimed.attempt == 2
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `pytest tests/integration/test_agent_jobs.py -v`

Expected: FAIL because the queue is missing.

- [ ] **Step 3: Implement transactional enqueue and lease claiming**

Build the idempotency key as `run_id:node_name:state_version:workitem_id-or-none:attempt`. Claim only `READY` jobs whose dependency IDs are complete. Lease changes `READY` to `RUNNING`, stores owner and expiry, and writes an audit event in the same transaction.

Create `tests/helpers/clock.py` with a callable `FakeClock` initialized to `2026-08-28T00:00:00Z` and `advance(seconds: int)`. Production queue code accepts a clock callable and defaults to current UTC.

- [ ] **Step 4: Implement one-iteration Worker dispatch**

`AgentJobWorker.run_once()` claims a job, loads its immutable input snapshot, calls the mapped node handler, stores output or failure, emits an event, and commits. It does not calculate workflow transitions itself; successful node events are consumed by `CommandService` continuation handlers.

- [ ] **Step 5: Add SSE event resumption**

`GET /sessions/{id}/events` reads audit events after `Last-Event-ID`, emits their durable sequence IDs, and sends heartbeat comments without storing them. Reconnecting with the last received ID must return each subsequent event exactly once.

- [ ] **Step 6: Run queue and SSE tests**

Run: `pytest tests/integration/test_agent_jobs.py tests/integration/test_workflow_api.py -v`

Expected: PASS for idempotency, dependency readiness, lease recovery, job completion, and event resumption.

- [ ] **Step 7: Commit durable jobs**

```bash
git add app/workflow/jobs.py app/api/workflows.py main.py tests/helpers/clock.py tests/integration/test_agent_jobs.py
git commit -m "feat: add durable agent job processing"
```

---

### Task 8: Materialize Versioned Repository Artifacts through an Outbox

**Files:**
- Create: `app/artifacts/__init__.py`
- Create: `app/artifacts/materializer.py`
- Create: `tests/integration/test_artifact_materializer.py`

**Interfaces:**
- Produces: `ArtifactRequest(run_id, event_key, relative_path, state_version, content, expected_hash)`
- Produces: `ArtifactMaterializer.run_once() -> bool`
- Produces: `ArtifactMaterializer.verify_run(run_id) -> ArtifactVerification`

- [ ] **Step 1: Write failing idempotency and mismatch tests**

```python
# tests/integration/test_artifact_materializer.py
from app.database.models import OutboxEvent


def enqueue_artifact(db_session, run_id, event_key, relative_path, state_version, content):
    db_session.add(OutboxEvent.for_artifact(run_id, event_key, relative_path, state_version, content))
    db_session.commit()


def test_materializer_writes_once_and_records_hash(db_session, workflow_run, project_repo):
    materializer = ArtifactMaterializer(db_session)
    enqueue_artifact(db_session, workflow_run.id, "proposal-v1", "changes/login/proposal.md", 3, "# Login\n")
    assert materializer.run_once() is True
    assert materializer.run_once() is False
    artifact = db_session.query(ArtifactVersion).one()
    assert artifact.relative_path == "changes/login/proposal.md"
    assert len(artifact.content_hash) == 64
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `pytest tests/integration/test_artifact_materializer.py -v`

Expected: FAIL because the artifacts package is missing.

- [ ] **Step 3: Implement safe relative-path validation and atomic writes**

Reject absolute paths, parent traversal, symlink escapes, and paths outside `changes/<change-id>/`. Write bytes to a sibling temporary file, `fsync`, atomically replace the destination, hash the final bytes, and record `ArtifactVersion` before marking the outbox event delivered.

- [ ] **Step 4: Generate the required artifact set**

Continuation handlers enqueue exact paths for `proposal.md`, `specs/`, `design.md`, `tasks.md`, `execution-contract.md`, and `workflow-manifest.json`. The manifest contains `state_version`, artifact hashes, WorkItem graph version, and frozen Interface Contract versions.

- [ ] **Step 5: Implement approval guards for eventual consistency**

`ArtifactMaterializer.verify_run()` compares database versions and hashes with repository files. DP-2, DP-3, and DP-7 guards remain false until required artifact versions are delivered and verified.

- [ ] **Step 6: Run write, replay, crash-window, tamper, and traversal tests**

Run: `pytest tests/integration/test_artifact_materializer.py -v`

Expected: PASS, including a simulated crash after file replacement and before outbox acknowledgment.

- [ ] **Step 7: Commit artifact materialization**

```bash
git add app/artifacts tests/integration/test_artifact_materializer.py
git commit -m "feat: materialize versioned workflow artifacts"
```

---

### Task 9: Implement Fixed Departments, Council Rounds, and Frozen Contracts

**Files:**
- Create: `app/workflow/departments.py`
- Create: `tests/unit/test_departments.py`
- Create: `tests/integration/test_department_council.py`

**Interfaces:**
- Consumes: `DepartmentId` enum with exactly eight values from Task 3
- Produces: `DEPARTMENT_CAPABILITIES: Mapping[DepartmentId, DepartmentCapability]`
- Produces: `DepartmentAllocator.validate_selection(ids) -> tuple[DepartmentId, ...]`
- Produces: `CouncilService.submit_round()`, `advance_round()`, `arbitrate_and_freeze()`
- Produces: `FrozenInterfaceContract`

- [ ] **Step 1: Write failing fixed-roster and round-cap tests**

```python
# tests/unit/test_departments.py
import pytest

from app.workflow.departments import DEPARTMENT_CAPABILITIES, DepartmentAllocator, UnknownDepartment


def test_department_roster_is_fixed_at_eight():
    assert {department.value for department in DEPARTMENT_CAPABILITIES} == {
        "product", "architecture", "backend", "frontend", "data", "security", "qa", "devops"
    }


def test_allocator_rejects_ninth_department():
    with pytest.raises(UnknownDepartment):
        DepartmentAllocator.validate_selection(["backend", "research"])
```

- [ ] **Step 2: Run the department tests and verify they fail**

Run: `pytest tests/unit/test_departments.py tests/integration/test_department_council.py -v`

Expected: FAIL because the department module is missing.

- [ ] **Step 3: Define the immutable capability table**

Encode the eight departments and default Skill capability names from the approved design. Return immutable tuples and frozen dataclasses so node output cannot mutate the registry.

- [ ] **Step 4: Implement the three-round Council state machine**

Round 1 accepts one independent responsibility/deliverable/risk proposal per selected Lead. Round 2 accepts provider/consumer, schema, shared-model, and dependency proposals. Round 3 accepts only conflict resolutions. `advance_round()` rejects a fourth round.

- [ ] **Step 5: Implement Planner arbitration and contract freezing**

After round 3, unresolved items require a Planner DecisionRecord. `arbitrate_and_freeze()` versions contracts, stores provider, consumers, schema refs, compatibility, errors, dependencies, fixtures, and acceptance tests, then marks status `FROZEN`. Any change creates a new version and invalidates affected review receipts.

- [ ] **Step 6: Run independent-proposal, negotiation, conflict, fourth-round, and revision tests**

Run: `pytest tests/unit/test_departments.py tests/integration/test_department_council.py -v`

Expected: PASS.

- [ ] **Step 7: Commit department coordination**

```bash
git add app/workflow/departments.py tests/unit/test_departments.py tests/integration/test_department_council.py
git commit -m "feat: coordinate fixed software departments"
```

---

### Task 10: Enforce WorkItem Specifications and Hierarchical Closure

**Files:**
- Create: `app/workflow/workitems.py`
- Create: `tests/unit/test_workitems.py`
- Modify: `app/services/workitem_service.py`
- Modify: `tests/conftest.py`
- Delete: `app/services/workitem_parser.py`

**Interfaces:**
- Produces: `EmployeeSubWorkItemSpec`
- Produces: `WorkItemService.create_department_items()`, `create_employee_items()`, `submit()`, `accept()`, `reject()`, `integrate_and_close()`, `close_department()`, `close_epic()`
- Consumes: frozen contracts from Task 9 and validated node output from Task 6

- [ ] **Step 1: Write failing mandatory-field and authority tests**

```python
# tests/unit/test_workitems.py
import pytest

from app.workflow.workitems import ClosureForbidden, EmployeeSubWorkItemSpec, WorkItemService


def test_employee_spec_requires_execution_boundaries():
    with pytest.raises(ValueError):
        EmployeeSubWorkItemSpec(
            title="Add endpoint",
            objective="Expose login",
            scope=["API"],
            exclusions=[],
            inputs=[],
            outputs=[],
            interface_ids=[],
            dependency_ids=[],
            required_skills=[],
            allowed_paths=[],
            test_obligations=[],
            acceptance_criteria=[],
            supervisor_agent_id="lead-backend",
        )


def test_worker_cannot_close_own_item(db_session, submitted_workitem):
    with pytest.raises(ClosureForbidden):
        WorkItemService(db_session).integrate_and_close(submitted_workitem.id, actor_id="worker-1")
```

Add `submitted_workitem` to `tests/conftest.py`. It creates a valid Epic, Department WorkItem, frozen contract, and Employee SubWorkItem through public service methods, assigns `worker-1`, advances it to `SUBMITTED`, and returns the persisted item. Do not insert a pre-shaped row that bypasses lifecycle validation.

- [ ] **Step 2: Run the tests and verify they fail**

Run: `pytest tests/unit/test_workitems.py -v`

Expected: FAIL because `app.workflow.workitems` is missing.

- [ ] **Step 3: Validate complete employee specifications**

Require nonempty objective, scope, outputs, Skill IDs, allowed paths, test obligations, acceptance criteria, and supervisor. Validate dependency IDs belong to the same Epic and interface IDs reference the frozen version assigned to the Department WorkItem.

- [ ] **Step 4: Implement lifecycle and actor guards**

Implement the exact lifecycle `DRAFT -> READY -> ASSIGNED -> RUNNING -> SUBMITTED -> ACCEPTED -> INTEGRATED -> CLOSED`, plus rejection, failure, block, and orphan paths. Store an audit event for every transition. Enforce Worker submission, Lead acceptance, Merge Coordinator integration/child closure, Planner department closure, and DP-7 Epic closure.

- [ ] **Step 5: Replace model-text parsing**

Change `workitem_service.py` to consume validated `DepartmentAllocationResult` and `EmployeeSubWorkItemResult`. Delete `workitem_parser.py` and remove all substring JSON parsing imports from `/chat`.

- [ ] **Step 6: Run lifecycle, dependency, stale-contract, and authority tests**

Run: `pytest tests/unit/test_workitems.py tests/integration/test_workflow_api.py -v`

Expected: PASS.

- [ ] **Step 7: Commit WorkItem enforcement**

```bash
git add app/workflow/workitems.py app/services/workitem_service.py app/api/chat.py tests/conftest.py tests/unit/test_workitems.py
git rm app/services/workitem_parser.py
git commit -m "feat: enforce hierarchical work item closure"
```

---

### Task 11: Build the Curated Skill Registry and Isolation Manager

**Files:**
- Create: `app/execution/skills.py`
- Create: `app/execution/skill_installer.py`
- Create: `app/execution/sandbox.py`
- Create: `tests/unit/test_skill_registry.py`
- Create: `tests/integration/test_skill_installer.py`
- Create: `tests/integration/test_sandbox.py`
- Modify: `tests/conftest.py`

**Interfaces:**
- Produces: `SkillManifest`, `ResolvedSkill`, `SkillRegistry.install_verified()`, `SkillResolver.resolve()`
- Produces: `SkillInstaller.install(manifest: SkillManifest) -> ResolvedSkill`
- Produces: `SandboxAssignment`, `WorktreeSandbox.prepare()`, `validate_before()`, `validate_after()`, `cleanup()`

- [ ] **Step 1: Write failing pinned-Skill and path-escape tests**

```python
# tests/unit/test_skill_registry.py
import pytest

from app.execution.skills import SkillManifest, UnpinnedSkill


def test_skill_manifest_requires_full_commit_and_checksum():
    with pytest.raises(UnpinnedSkill):
        SkillManifest(
            id="backend-tdd",
            repository="https://github.com/example/skills",
            commit_sha="main",
            checksum="",
            license="MIT",
            entry_path="tdd/SKILL.md",
            capabilities=("tdd",),
        )
```

```python
# tests/integration/test_sandbox.py
def test_worker_change_outside_allowed_paths_is_rejected(project_repo, sandbox_factory):
    sandbox = sandbox_factory(project_repo, allowed_paths=("app/api/**",))
    sandbox.prepare()
    (sandbox.worktree / "README.md").write_text("unauthorized\n", encoding="utf-8")
    result = sandbox.validate_after()
    assert result.accepted is False
    assert result.violations == ("README.md",)
```

Add `sandbox_factory` to `tests/conftest.py`. It resolves the fixture repository's initial commit, creates a unique `SandboxAssignment` under `tmp_path / "worktrees"`, supplies a worker-specific Codex home and an empty read-only Skill tuple, and returns `WorktreeSandbox(assignment)`.

- [ ] **Step 2: Run the tests and verify they fail**

Run: `pytest tests/unit/test_skill_registry.py tests/integration/test_sandbox.py -v`

Expected: FAIL because Skill and sandbox implementations are missing.

- [ ] **Step 3: Implement verified Skill installation and resolution**

Accept only HTTPS GitHub repository URLs, 40-character commit SHAs, SHA-256 checksums, known licenses, existing `SKILL.md` entry paths, and an approved review status. Store packages under `skill_store/<skill-id>/<commit-sha>/`. Resolution returns only exact versions compatible with the assigned department capabilities.

Wire Council AgentJob creation through `SkillResolver`: each Department Lead receives only that department's pinned Lead Skills plus the current Council round input. Cross-department proposals arrive as structured Council records rather than shared filesystem access.

- [ ] **Step 4: Implement the administrative Skill installer**

`SkillInstaller.install()` runs outside Worker execution. It clones the single allowlisted repository into a staging directory, checks out the exact commit SHA, rejects submodules and symlinks escaping the checkout, computes the deterministic tree checksum, validates the declared license and `SKILL.md`, then atomically installs the verified package into the immutable Skill store. `tests/integration/test_skill_installer.py` uses a local Git fixture as the transport and proves commit/checksum mismatch leaves no installed package. No Worker code path imports or invokes the installer.

- [ ] **Step 5: Implement worktree isolation**

Create `workflow/<run>/worker/<subworkitem>` from the run's immutable `base_commit` or approved dependency commit. Reject an existing dirty destination. Construct a process environment with the assigned working directory, `CODEX_HOME`, no proxy variables, and an explicit network-disabled Codex sandbox configuration. Expose only resolved Skill directories as read-only inputs.

- [ ] **Step 6: Implement pre/post path validation**

Record porcelain-v2 status before execution. After execution, inspect changed, untracked, renamed, deleted, symlink, and submodule paths. Match normalized repository-relative paths against `allowed_paths[]`; reject `.git`, absolute paths, parent traversal, symlink escapes, submodule changes, and any out-of-scope path.

- [ ] **Step 7: Run installer, Skill, worktree, network-env, read-only-mount, traversal, rename, and symlink tests**

Run: `pytest tests/unit/test_skill_registry.py tests/integration/test_skill_installer.py tests/integration/test_sandbox.py -v`

Expected: PASS.

- [ ] **Step 8: Commit execution isolation**

```bash
git add app/execution/skills.py app/execution/skill_installer.py app/execution/sandbox.py tests/conftest.py tests/unit/test_skill_registry.py tests/integration/test_skill_installer.py tests/integration/test_sandbox.py
git commit -m "feat: isolate workers and pin skills"
```

---

### Task 12: Collect Worker Evidence and Enforce Review and Merge Coordination

**Files:**
- Create: `app/execution/evidence.py`
- Create: `app/execution/review.py`
- Create: `app/execution/merge.py`
- Create: `tests/integration/test_worker_review_merge.py`
- Modify: `tests/conftest.py`

**Interfaces:**
- Produces: `EvidenceBundle(commit_sha, diff_hash, changed_paths, tests, logs_hash, claim)`
- Produces: `EvidenceCollector.collect(assignment, completion) -> EvidenceBundle`
- Produces: `ReviewService.review_employee()`, `review_department()`
- Produces: `MergeCoordinator.integrate_employee()`, `integrate_department()`

- [ ] **Step 1: Write a failing evidence-bound review test**

```python
# tests/integration/test_worker_review_merge.py
def test_lead_receipt_is_bound_to_spec_diff_and_tests(workflow_fixture):
    submitted = workflow_fixture.submit_valid_worker_commit()
    receipt = workflow_fixture.lead_review(submitted, accepted=True)
    assert receipt.spec_version == submitted.spec_version
    assert receipt.diff_hash == submitted.diff_hash
    assert receipt.evidence_hash == submitted.evidence_hash
    workflow_fixture.tamper_with_worker_branch()
    assert workflow_fixture.can_integrate(submitted.id) is False
```

Add `workflow_fixture` to `tests/conftest.py` as a façade over real public services. `submit_valid_worker_commit()` creates a valid WorkItem, worktree, one commit, passing test evidence, and a `SUBMITTED` item. `lead_review()` invokes the read-only review path. `tamper_with_worker_branch()` adds a second commit, and `can_integrate()` evaluates the receipt hash guard without changing state.

- [ ] **Step 2: Run the test and verify it fails**

Run: `pytest tests/integration/test_worker_review_merge.py -v`

Expected: FAIL because evidence and merge coordinators are missing.

- [ ] **Step 3: Implement deterministic evidence collection**

Verify the Worker produced exactly one commit rooted at the assigned base, collect `git diff --binary` and changed paths, hash normalized evidence JSON, capture each declared test command with exit code and output hash, and store the structured completion claim. Reject missing commits, extra commits, failed mandatory tests, or an unclean worktree.

- [ ] **Step 4: Implement read-only Lead review receipts**

Invoke the Lead node through `CodexAdapter` with a read-only sandbox and supply only the SubWorkItem Spec version, frozen interfaces, diff, and evidence. Validate the review schema, record pass/fail per acceptance criterion, and bind the receipt to `spec_version`, `diff_hash`, `evidence_hash`, and reviewer AgentRun. A changed value invalidates the receipt.

- [ ] **Step 5: Implement dependency-ordered merges**

Topologically sort accepted Employee SubWorkItems. Merge each commit into `workflow/<run>/dept/<department>` using a noninteractive Git merge. On conflict, abort only the in-progress department merge, preserve both commits, mark the item blocked, and request Planner/Lead action. After department tests and Planner acceptance, topologically integrate department heads into `workflow/<run>/integration`.

- [ ] **Step 6: Enforce all closure gates**

Close a child only after successful department-branch integration. Close a department only after all children close, department tests pass, and the Planner receipt matches the aggregate diff. Set DP-6's integration guard only after every selected department closes and cross-interface plus full integration tests pass.

- [ ] **Step 7: Run review, tamper, rejection, merge-order, conflict, and closure tests**

Run: `pytest tests/integration/test_worker_review_merge.py -v`

Expected: PASS.

- [ ] **Step 8: Commit evidence and integration coordination**

```bash
git add app/execution/evidence.py app/execution/review.py app/execution/merge.py tests/conftest.py tests/integration/test_worker_review_merge.py
git commit -m "feat: review and integrate worker evidence"
```

---

### Task 13: Recover from Checkpoints and Reconcile Divergence

**Files:**
- Create: `app/workflow/recovery.py`
- Create: `tests/integration/test_recovery.py`
- Modify: `main.py`
- Modify: `tests/conftest.py`

**Interfaces:**
- Produces: `RecoveryService.recover_all() -> RecoveryReport`
- Produces: `RecoveryService.recover_run(run_id) -> RunRecoveryResult`
- Produces: `ReconciliationIssue(code, resource_id, expected, actual, next_action)`

- [ ] **Step 1: Write a failing orphan and artifact-divergence test**

```python
# tests/integration/test_recovery.py
def test_recovery_preserves_orphan_evidence_and_blocks_on_divergence(recovery_fixture):
    run, job = recovery_fixture.running_worker_with_expired_lease()
    recovery_fixture.change_materialized_artifact(run)
    result = recovery_fixture.service.recover_run(run.id)
    assert result.orphaned_job_ids == (job.id,)
    assert result.waiting_for_user is True
    assert result.issues[0].code == "artifact_hash_mismatch"
    assert recovery_fixture.worker_worktree(job).exists()
```

Add `recovery_fixture` to `tests/conftest.py`. It uses `FakeClock`, creates a checkpointed run and leased Worker job through public APIs, creates its worktree and evidence files, advances the clock beyond the lease, and exposes methods to alter one materialized artifact and retrieve the Worker's preserved worktree.

- [ ] **Step 2: Run the test and verify it fails**

Run: `pytest tests/integration/test_recovery.py -v`

Expected: FAIL because the recovery service is missing.

- [ ] **Step 3: Verify the latest checkpoint before resuming**

Load the latest checkpoint and verify real project path, reachable base and branch commits, artifact hashes, frozen contract hashes, WorkItem DAG version, completed idempotency keys, outbox cursor, and job leases. Never mutate Git during verification.

- [ ] **Step 4: Reconcile only safe states**

Requeue an expired non-Worker node job when its idempotency key has no completed result. Mark expired Worker jobs `ORPHANED` and preserve their branch, worktree, logs, commit, diff, and evidence. On Git, contract, artifact, or review divergence, set `waiting_for_user` or `approval_required`, record a `ReconciliationIssue`, and stop automatic scheduling.

- [ ] **Step 5: Run recovery at application startup**

After migrations and before starting AgentJob polling, call `recover_all()`. Startup remains successful when runs require user reconciliation; only database corruption or an unavailable configured database prevents service startup.

- [ ] **Step 6: Run kill-boundary recovery tests**

Run: `pytest tests/integration/test_recovery.py -v`

Expected: PASS for command-commit, job-lease, Codex completion, artifact replacement, review receipt, employee merge, department merge, and DP checkpoint crash windows.

- [ ] **Step 7: Commit checkpoint recovery**

```bash
git add app/workflow/recovery.py main.py tests/conftest.py tests/integration/test_recovery.py
git commit -m "feat: recover workflow checkpoints safely"
```

---

### Task 14: Complete the DP-0-to-DP-7 End-to-End Path and Migration

**Files:**
- Create: `tests/e2e/test_full_workflow.py`
- Create: `tests/e2e/conftest.py`
- Modify: `app/services/session_service.py`
- Modify: `app/services/prompt_builder.py`
- Delete: `app/services/workflow_service.py`
- Modify: `readme.md`

**Interfaces:**
- Consumes: all previous task interfaces
- Produces: one fully tested `full_flow_v2` path and documented operator workflow

- [ ] **Step 1: Write the failing full-path test**

```python
# tests/e2e/test_full_workflow.py
def test_full_workflow_survives_restart_and_never_updates_main(e2e_gateway, project_repo):
    main_before = e2e_gateway.git_head(project_repo, "main")
    run = e2e_gateway.create_full_run(project_repo)
    e2e_gateway.approve_dp0(run)
    e2e_gateway.complete_three_clarification_rounds(run)
    e2e_gateway.approve_dp1(run)
    e2e_gateway.generate_and_review_artifacts(run)
    e2e_gateway.complete_three_council_rounds(run)
    e2e_gateway.planner_freezes_interfaces(run)
    e2e_gateway.approve_dp2_and_dp3(run)
    e2e_gateway.select_sdd_at_dp4(run)
    e2e_gateway.execute_workers_in_dependency_waves(run)
    e2e_gateway.restart_gateway()
    e2e_gateway.complete_reviews_and_integration(run)
    e2e_gateway.approve_dp6_and_dp7(run)
    state = e2e_gateway.state(run)
    assert state["status"] == "completed"
    assert e2e_gateway.git_head(project_repo, "main") == main_before
    assert e2e_gateway.remote_refs(project_repo) == {}
    release = e2e_gateway.release_event(run)
    assert release["code"] == "git_save_required"
    assert release["main_merged"] is False
    assert release["remote_pushed"] is False
```

Implement `e2e_gateway` in `tests/e2e/conftest.py` as a deterministic harness around `create_app`, the real database, real temporary Git repositories, and `CodexAdapter` with a scripted `FakeProcessFactory`. Each named method consumes the current legal action and asserts the expected phase/version before returning, so the test cannot skip a gate. `restart_gateway()` disposes the app and session factory, creates them again against the same SQLite file, and runs startup recovery.

- [ ] **Step 2: Run the end-to-end test and verify the first unmet integration fails**

Run: `pytest tests/e2e/test_full_workflow.py -v`

Expected: FAIL at the first unconnected continuation handler or gate, not because of missing fixtures.

- [ ] **Step 3: Connect node outcomes to deterministic continuations**

Wire clarification, architecture, allocation, Council, decomposition, artifact, contract, Worker, review, debug, and release job outcomes to `CommandService` continuation methods. Each continuation validates the originating `state_version`, persists proposed domain records, enqueues the next job or approval event, and never advances a DP without its explicit command.

- [ ] **Step 4: Finish legacy-session migration behavior**

Existing conversations with `workflow_type` null remain legacy and retain history. New sessions default to `full_flow_v2`. Remove calls to the old prompt-driven Workflow Service and delete it only after compatibility tests show old conversation reads and explicitly legacy `/chat` calls still work.

- [ ] **Step 5: Document operation and user-owned Git completion**

Update `readme.md` with environment variables, startup, new API examples, command conflict handling, SSE resume, Skill installation, sandbox assumptions, checkpoint recovery, candidate integration branch inspection, and explicit user steps for saving, merging, and pushing Git.

- [ ] **Step 6: Run the full verification suite**

Run: `pytest -v`

Expected: all unit, integration, recovery, isolation, compatibility, and end-to-end tests PASS with zero failures.

- [ ] **Step 7: Verify no main or remote mutation**

Run: `git status --short && git branch --show-current && git log --oneline --decorate -5`

Expected: only intentional implementation changes are present before the final commit; execution fixtures have not changed the repository's `main` ref or any remote ref.

- [ ] **Step 8: Commit the complete vertical slice**

```bash
git add app main.py readme.md tests/e2e
git rm app/services/workflow_service.py
git commit -m "feat: complete deterministic department workflow"
```

- [ ] **Step 9: Produce the candidate-version reminder**

Report the integration branch name, candidate commit, full test command and result, outstanding reconciliation issues, and the exact statement that main was not merged or pushed. Ask the user to save Git and decide whether to merge and push.

---

## Plan Completion Criteria

The plan is complete when all fourteen task commits exist, `pytest -v` reports zero failures, every acceptance criterion in the approved design maps to a passing test, the workflow reaches DP-7 after a forced restart, repository artifact hashes reconcile with SQLite, and the main branch plus remote refs remain unchanged by workflow execution.

