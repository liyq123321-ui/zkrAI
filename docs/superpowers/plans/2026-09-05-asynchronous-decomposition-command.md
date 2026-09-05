# Asynchronous Decomposition Command Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run `convert_to_work_item` as a durable background command, report live state through SSE, and reconcile completion through a non-overlapping frontend status poll every 5,000 ms.

**Architecture:** A new `command_jobs` record wraps the existing idempotent `CommandService` execution without changing decomposition logic. The Session API returns 202 for decomposition, exposes a durable status resource and an SSE state stream, while the frontend observes both channels through one version-aware coordinator.

**Tech Stack:** Python 3, FastAPI `BackgroundTasks` and `StreamingResponse`, SQLAlchemy/SQLite, Pydantic, React 19, TypeScript, native `EventSource`, Vitest, pytest.

## Global Constraints

- Only `convert_to_work_item` becomes asynchronous; every other Session command keeps its current synchronous 200 response.
- SSE is the primary update channel and a non-overlapping status poll runs every exactly 5,000 ms.
- The existing `CommandService` and `DecompositionService` remain authoritative for state validation, Agent work, idempotency, and materialization.
- The first version remains single-process and uses FastAPI background tasks; do not add Redis, Celery, WebSockets, or a distributed queue.
- Persist only classified public failures; never expose Agent output, internal exception strings, credentials, or filesystem paths.
- Browser disconnect, project switch, or component unmount stops observation but never cancels the backend job.

---

## File Structure

- `backend/app/database/models.py`: persist the command-job execution envelope.
- `backend/app/schemas/workflow.py`: define accepted, status, error, and union response contracts.
- `backend/app/services/command_jobs.py`: own job submission, claiming, execution, recovery, status reads, and SSE snapshots.
- `backend/app/api/sessions.py`: select synchronous versus background command transport and expose status/SSE routes.
- `backend/main.py`: assemble the decomposition command executor and mark interrupted jobs during startup.
- `backend/tests/unit/test_database_schema.py`: lock down the new table contract.
- `backend/tests/integration/test_command_jobs.py`: verify coordinator transitions, conflicts, failures, and recovery.
- `backend/tests/integration/test_sessions_api.py`: verify conditional 202, status scoping, SSE, and unchanged synchronous responses.
- `frontend/aios-main/src/api/dto.ts`: define discriminated command-job DTOs.
- `frontend/aios-main/src/api/sessions.ts`: expose job status and SSE URLs and remove the long timeout from decomposition submission.
- `frontend/aios-main/src/api/commandJobs.ts`: coordinate EventSource and five-second polling through one terminal promise.
- `frontend/aios-main/src/api/commandJobs.test.ts`: test timing, deduplication, fallback, and cleanup with fake timers.
- `frontend/aios-main/src/api/ApiWorkspace.tsx`: submit, persist, resume, and reconcile decomposition jobs.
- `frontend/aios-main/src/api/workspace.test.tsx`: verify the user-visible decomposition workflow.
- `backend/readme.md`: document the asynchronous contract and recovery behavior.

---

### Task 1: Persist and Type the Command Job Contract

**Files:**
- Modify: `backend/app/database/models.py`
- Modify: `backend/app/schemas/workflow.py`
- Modify: `backend/tests/unit/test_database_schema.py`

**Interfaces:**
- Consumes: SQLAlchemy `Base`, `utc_now`, and existing `CommandResult`.
- Produces: `CommandJob`, `CommandJobAccepted`, `CommandJobRead`, `CommandJobError`, `CommandSubmission`, and `CommandJobStatus`.

- [ ] **Step 1: Write the failing database contract test**

Add this test to `backend/tests/unit/test_database_schema.py`:

```python
def test_command_jobs_have_durable_status_and_idempotent_identity(engine):
    init_database(engine)
    schema = inspect(engine)

    columns = {item["name"]: item for item in schema.get_columns("command_jobs")}
    unique = {
        frozenset(item.get("column_names") or [])
        for item in schema.get_unique_constraints("command_jobs")
    }

    assert {
        "id", "project_id", "session_id", "command_id", "input_hash",
        "request_payload", "status", "status_version", "result",
        "error_code", "error_message", "created_at", "started_at",
        "completed_at", "updated_at",
    } <= set(columns)
    assert frozenset({"session_id", "command_id"}) in unique
    assert columns["status"]["nullable"] is False
    assert columns["status_version"]["nullable"] is False
```

- [ ] **Step 2: Run the schema test and verify RED**

Run:

```bash
cd backend && .venv/bin/pytest tests/unit/test_database_schema.py::test_command_jobs_have_durable_status_and_idempotent_identity -v
```

Expected: FAIL because `command_jobs` does not exist.

- [ ] **Step 3: Add the SQLAlchemy model**

Add this model beside `CommandAttempt` in `backend/app/database/models.py`:

```python
class CommandJob(Base):
    """Durable execution envelope for one asynchronous Session command."""

    __tablename__ = "command_jobs"
    __table_args__ = (
        UniqueConstraint("session_id", "command_id", name="uq_command_job"),
    )

    id = Column(String, primary_key=True)
    project_id = Column(String, nullable=False, index=True)
    session_id = Column(String, nullable=False, index=True)
    command_id = Column(String, nullable=False)
    input_hash = Column(String(64), nullable=False)
    request_payload = Column(JSON, nullable=False)
    status = Column(String, nullable=False, default="pending")
    status_version = Column(Integer, nullable=False, default=1)
    result = Column(JSON, nullable=True)
    error_code = Column(String, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )
```

`Base.metadata.create_all()` already creates new tables for existing SQLite
databases, so this additive table does not need a rebuild migration.

- [ ] **Step 4: Add exact transport schemas**

Add to `backend/app/schemas/workflow.py`:

```python
from datetime import datetime
from typing import Literal

CommandJobStatus = Literal["pending", "processing", "succeeded", "failed"]


class CommandJobError(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    message: str


class CommandJobAccepted(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command_id: str
    status: CommandJobStatus
    status_url: str
    events_url: str


class CommandJobRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command_id: str
    status: CommandJobStatus
    status_version: int = Field(ge=1)
    result: CommandResult | None = None
    error: CommandJobError | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


CommandSubmission = CommandResult | CommandJobAccepted
```

Define `CommandSubmission` after `CommandResult` so the forward reference is not
needed.

- [ ] **Step 5: Run the focused schema tests and verify GREEN**

Run:

```bash
cd backend && .venv/bin/pytest tests/unit/test_database_schema.py -q
```

Expected: all database schema tests pass.

- [ ] **Step 6: Commit the persistence contract**

```bash
git add backend/app/database/models.py backend/app/schemas/workflow.py backend/tests/unit/test_database_schema.py
git commit -m "feat: persist asynchronous command jobs"
```

---

### Task 2: Implement the Durable Command Job Coordinator

**Files:**
- Create: `backend/app/services/command_jobs.py`
- Create: `backend/tests/integration/test_command_jobs.py`

**Interfaces:**
- Consumes: `CommandJob`, `Project`, `SessionCommandRequest`, `CommandResult`, `classify_workflow_error`, and an injected `CommandExecutor = Callable[[str, SessionCommandRequest], Awaitable[CommandResult]]`.
- Produces: `CommandJobCoordinator.submit(session_id, request) -> tuple[CommandJobAccepted, bool]`, `run(job_id) -> None`, `get(session_id, command_id) -> CommandJobRead`, `mark_interrupted_jobs() -> int`, and `events(session_id, command_id, last_event_id) -> AsyncIterator[str]`.

- [ ] **Step 1: Write failing coordinator transition tests**

Create `backend/tests/integration/test_command_jobs.py` with a minimal persisted
Project and injected executor:

```python
from datetime import UTC, datetime

import pytest

from app.database.models import CommandJob, Project
from app.domain.types import CommandAction
from app.schemas.workflow import CommandResult, SessionCommandRequest, SessionState
from app.services.command_jobs import CommandJobCoordinator
from app.services.command_service import CommandConflict


def request(command_id: str = "decompose-1") -> SessionCommandRequest:
    return SessionCommandRequest(
        command_id=command_id,
        action=CommandAction.CONVERT_TO_WORK_ITEM,
        expected_state_version=7,
        actor_id="owner-1",
        payload={},
    )


def state() -> SessionState:
    return SessionState(
        session_id="session-1",
        project_id="project-1",
        phase="AGENT_SPECS_READY",
        state_version=8,
        current_spec_version_id="spec-1",
        current_spec_status="APPROVED",
        legal_actions=[],
        next_action="NONE",
        outstanding_questions=[],
        review_findings=[],
    )


def seed_project(session_factory):
    with session_factory() as db:
        db.add(Project(
            id="project-1",
            session_id="session-1",
            creation_request_id="request-1",
            brief={},
            final_approver="owner-1",
            project_manager_ids=["owner-1"],
            root_owner_ids=["owner-1"],
            phase="APPROVED",
            state_version=7,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        ))
        db.commit()


@pytest.mark.asyncio
async def test_job_moves_to_succeeded_and_persists_original_result(session_factory):
    seed_project(session_factory)
    calls = []

    async def execute(session_id, command):
        calls.append((session_id, command.command_id))
        return CommandResult(
            command_id=command.command_id,
            state=state(),
            created_resource_ids=["work-item-1"],
        )

    coordinator = CommandJobCoordinator(session_factory, execute)
    accepted, should_schedule = coordinator.submit("session-1", request())
    assert accepted.status == "pending"
    assert should_schedule is True

    with session_factory() as db:
        job_id = db.query(CommandJob).one().id
    await coordinator.run(job_id)

    snapshot = coordinator.get("session-1", "decompose-1")
    assert snapshot.status == "succeeded"
    assert snapshot.status_version == 3
    assert snapshot.result is not None
    assert snapshot.result.created_resource_ids == ["work-item-1"]
    assert calls == [("session-1", "decompose-1")]


def test_duplicate_submission_is_idempotent_and_conflicting_input_is_rejected(session_factory):
    seed_project(session_factory)

    async def execute(session_id, command):
        raise AssertionError("executor is not called by submit")

    coordinator = CommandJobCoordinator(session_factory, execute)
    first, first_schedule = coordinator.submit("session-1", request())
    second, second_schedule = coordinator.submit("session-1", request())
    assert second == first
    assert (first_schedule, second_schedule) == (True, False)

    changed = request().model_copy(update={"expected_state_version": 8})
    with pytest.raises(CommandConflict):
        coordinator.submit("session-1", changed)
```

- [ ] **Step 2: Run the transition tests and verify RED**

Run:

```bash
cd backend && .venv/bin/pytest tests/integration/test_command_jobs.py -v
```

Expected: collection fails because `app.services.command_jobs` does not exist.

- [ ] **Step 3: Implement submission, claiming, success, and status reads**

Create `backend/app/services/command_jobs.py`. Use canonical compact sorted JSON
for the input hash and UUID strings for IDs:

```python
import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from time import monotonic
from uuid import uuid4

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database.models import CommandJob, Project
from app.domain.types import CommandAction
from app.schemas.workflow import (
    CommandJobAccepted,
    CommandJobError,
    CommandJobRead,
    CommandResult,
    SessionCommandRequest,
)
from app.services.command_service import CommandConflict
from app.services.error_classification import classify_workflow_error

CommandExecutor = Callable[
    [str, SessionCommandRequest], Awaitable[CommandResult]
]
TERMINAL = frozenset({"succeeded", "failed"})


def _now() -> datetime:
    return datetime.now(UTC)


def _id() -> str:
    return str(uuid4())


def _input_hash(request: SessionCommandRequest) -> str:
    payload = json.dumps(
        request.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class CommandJobCoordinator:
    def __init__(self, session_factory: Callable[[], Session], execute: CommandExecutor):
        self._session_factory = session_factory
        self._execute = execute

    def submit(
        self, session_id: str, request: SessionCommandRequest
    ) -> tuple[CommandJobAccepted, bool]:
        if request.action is not CommandAction.CONVERT_TO_WORK_ITEM:
            raise ValueError("only decomposition commands may use background jobs")
        digest = _input_hash(request)
        with self._session_factory() as db:
            with db.begin():
                project = db.query(Project).filter_by(session_id=session_id).one_or_none()
                if project is None:
                    raise KeyError("project not found")
                job = db.query(CommandJob).filter_by(
                    session_id=session_id, command_id=request.command_id
                ).one_or_none()
                should_schedule = False
                if job is None:
                    candidate = CommandJob(
                        id=_id(),
                        project_id=project.id,
                        session_id=session_id,
                        command_id=request.command_id,
                        input_hash=digest,
                        request_payload=request.model_dump(mode="json"),
                        status="pending",
                        status_version=1,
                    )
                    try:
                        with db.begin_nested():
                            db.add(candidate)
                            db.flush()
                    except IntegrityError:
                        job = db.query(CommandJob).filter_by(
                            session_id=session_id,
                            command_id=request.command_id,
                        ).one()
                    else:
                        return self._accepted(candidate), True
                if job.input_hash != digest:
                    raise CommandConflict("command_id was used with different input")
                if job.status == "failed":
                    job.status = "pending"
                    job.status_version += 1
                    job.result = None
                    job.error_code = None
                    job.error_message = None
                    job.started_at = None
                    job.completed_at = None
                    should_schedule = True
                return self._accepted(job), should_schedule

    async def run(self, job_id: str) -> None:
        with self._session_factory() as db:
            claimed = db.execute(
                update(CommandJob)
                .where(CommandJob.id == job_id, CommandJob.status == "pending")
                .values(
                    status="processing",
                    status_version=CommandJob.status_version + 1,
                    started_at=_now(),
                    error_code=None,
                    error_message=None,
                )
            )
            db.commit()
            if claimed.rowcount != 1:
                return
            job = db.get(CommandJob, job_id)
            session_id = job.session_id
            request = SessionCommandRequest.model_validate(job.request_payload)
        try:
            result = await self._execute(session_id, request)
        except Exception as error:
            public = classify_workflow_error(error)
            self._finish_failure(job_id, public.code, public.message)
            return
        self._finish_success(job_id, result)

    def get(self, session_id: str, command_id: str) -> CommandJobRead:
        with self._session_factory() as db:
            job = db.query(CommandJob).filter_by(
                session_id=session_id, command_id=command_id
            ).one_or_none()
            if job is None:
                raise KeyError("command job not found")
            return self._read(job)

    def job_id(self, session_id: str, command_id: str) -> str:
        with self._session_factory() as db:
            job = db.query(CommandJob).filter_by(
                session_id=session_id, command_id=command_id
            ).one_or_none()
            if job is None:
                raise KeyError("command job not found")
            return str(job.id)

    @staticmethod
    def _accepted(job: CommandJob) -> CommandJobAccepted:
        base = f"/sessions/{job.session_id}/commands/{job.command_id}"
        return CommandJobAccepted(
            command_id=job.command_id,
            status=job.status,
            status_url=base,
            events_url=f"{base}/events",
        )

    @staticmethod
    def _read(job: CommandJob) -> CommandJobRead:
        return CommandJobRead(
            command_id=job.command_id,
            status=job.status,
            status_version=job.status_version,
            result=(CommandResult.model_validate(job.result) if job.result else None),
            error=(
                CommandJobError(code=job.error_code, message=job.error_message)
                if job.error_code and job.error_message
                else None
            ),
            created_at=job.created_at,
            started_at=job.started_at,
            completed_at=job.completed_at,
        )

    def _finish_success(self, job_id: str, result: CommandResult) -> None:
        now = _now()
        with self._session_factory() as db:
            db.execute(
                update(CommandJob)
                .where(CommandJob.id == job_id, CommandJob.status == "processing")
                .values(
                    status="succeeded",
                    status_version=CommandJob.status_version + 1,
                    result=result.model_dump(mode="json"),
                    error_code=None,
                    error_message=None,
                    completed_at=now,
                    updated_at=now,
                )
            )
            db.commit()

    def _finish_failure(self, job_id: str, code: str, message: str) -> None:
        now = _now()
        with self._session_factory() as db:
            db.execute(
                update(CommandJob)
                .where(CommandJob.id == job_id, CommandJob.status == "processing")
                .values(
                    status="failed",
                    status_version=CommandJob.status_version + 1,
                    result=None,
                    error_code=code,
                    error_message=message,
                    completed_at=now,
                    updated_at=now,
                )
            )
            db.commit()
```

The nested insert is required: simultaneous identical submissions must resolve to
the same row, while different canonical input reaches the conflict branch.

- [ ] **Step 4: Add failing failure and interruption recovery tests**

Append tests that prove raw error text is not persisted and abandoned work is
recoverable:

```python
@pytest.mark.asyncio
async def test_failure_is_classified_without_persisting_internal_detail(session_factory):
    seed_project(session_factory)

    async def execute(session_id, command):
        raise RuntimeError("token=secret local=/Users/private")

    coordinator = CommandJobCoordinator(session_factory, execute)
    coordinator.submit("session-1", request())
    with session_factory() as db:
        job_id = db.query(CommandJob).one().id
    await coordinator.run(job_id)

    snapshot = coordinator.get("session-1", "decompose-1")
    assert snapshot.status == "failed"
    assert snapshot.error is not None
    assert snapshot.error.code == "WORKFLOW_ERROR"
    assert "secret" not in snapshot.error.message
    with session_factory() as db:
        persisted = db.get(CommandJob, job_id)
        assert "secret" not in (persisted.error_message or "")


def test_startup_marks_abandoned_jobs_and_identical_submit_reschedules(session_factory):
    seed_project(session_factory)

    async def execute(session_id, command):
        raise AssertionError("not run")

    coordinator = CommandJobCoordinator(session_factory, execute)
    coordinator.submit("session-1", request())
    assert coordinator.mark_interrupted_jobs() == 1
    failed = coordinator.get("session-1", "decompose-1")
    assert failed.status == "failed"
    assert failed.error is not None
    assert failed.error.code == "PROCESS_INTERRUPTED"

    accepted, should_schedule = coordinator.submit("session-1", request())
    assert accepted.status == "pending"
    assert should_schedule is True
```

- [ ] **Step 5: Implement terminal writes and startup recovery**

Use one public interruption message and never copy exception text:

```python
def mark_interrupted_jobs(self) -> int:
    with self._session_factory() as db:
        result = db.execute(
            update(CommandJob)
            .where(CommandJob.status.in_(("pending", "processing")))
            .values(
                status="failed",
                status_version=CommandJob.status_version + 1,
                error_code="PROCESS_INTERRUPTED",
                error_message="The background command process was interrupted; retry the command.",
                completed_at=_now(),
            )
        )
        db.commit()
        return int(result.rowcount or 0)
```

In `_finish_success` and `_finish_failure`, raise no exception when the
conditional update affects zero rows; another terminal writer already owns the
result.

- [ ] **Step 6: Run coordinator tests and verify GREEN**

Run:

```bash
cd backend && .venv/bin/pytest tests/integration/test_command_jobs.py -q
```

Expected: all coordinator tests pass.

- [ ] **Step 7: Commit the coordinator**

```bash
git add backend/app/services/command_jobs.py backend/tests/integration/test_command_jobs.py
git commit -m "feat: coordinate durable decomposition jobs"
```

---

### Task 3: Expose Conditional 202, Status, and SSE Endpoints

**Files:**
- Modify: `backend/app/api/sessions.py`
- Modify: `backend/main.py`
- Modify: `backend/app/services/command_jobs.py`
- Modify: `backend/tests/integration/test_sessions_api.py`

**Interfaces:**
- Consumes: Task 2 `CommandJobCoordinator` and Task 1 command-job schemas.
- Produces: conditional `POST /sessions/{session_id}/commands`, `GET /sessions/{session_id}/commands/{command_id}`, and `GET /sessions/{session_id}/commands/{command_id}/events`.

- [ ] **Step 1: Write failing HTTP acceptance and compatibility tests**

Add tests to `backend/tests/integration/test_sessions_api.py`. Build an approved
Session with `_approved_http_session`, then block the injected decomposition
executor with `asyncio.Event` in a coordinator unit route test so the assertion
can inspect the accepted response before releasing the executor. Also retain a
normal command assertion:

```python
def test_decomposition_returns_accepted_job_and_status_is_pollable(session_factory):
    agent = ScriptedAgentGateway(
        analyze_results=deque([ClarificationAnalysis(
            ready_for_spec=True, questions=[], assumptions=[]
        )]),
        generate_results=deque([make_valid_spec()]),
        review_results=deque([make_passing_semantic_review()]),
        decompose_results=deque([make_valid_breakdown()]),
    )
    with TestClient(create_app(agent_gateway=agent, session_factory=session_factory)) as client:
        approved = _approved_http_session(client, "async-decompose")
        response = client.post(
            f"/sessions/{approved['session_id']}/commands",
            json={
                "command_id": "decompose-accepted",
                "action": "convert_to_work_item",
                "expected_state_version": approved["state_version"],
                "actor_id": "approver-1",
                "payload": {},
            },
        )
        assert response.status_code == 202
        accepted = response.json()
        assert accepted == {
            "command_id": "decompose-accepted",
            "status": "pending",
            "status_url": f"/sessions/{approved['session_id']}/commands/decompose-accepted",
            "events_url": f"/sessions/{approved['session_id']}/commands/decompose-accepted/events",
        }
        status_response = client.get(accepted["status_url"])
        assert status_response.status_code == 200
        assert status_response.json()["status"] == "succeeded"
        assert status_response.json()["result"]["state"]["phase"] == "AGENT_SPECS_READY"


def test_non_decomposition_command_still_returns_completed_200(session_factory):
    agent = ScriptedAgentGateway(analyze_results=deque([
        _blocking_analysis("Q-SYNC", "Which deployment boundary applies?")
    ]))
    with TestClient(create_app(agent_gateway=agent, session_factory=session_factory)) as client:
        created = client.post("/sessions", json={
            "request_id": "sync-command",
            "actor_id": "approver-1",
            "brief": make_complete_brief().model_dump(mode="json"),
        }).json()
        response = client.post(f"/sessions/{created['session_id']}/commands", json={
            "command_id": "sync-skip",
            "action": "skip_clarification",
            "expected_state_version": created["state_version"],
            "actor_id": "approver-1",
            "payload": {},
        })
        assert response.status_code == 200
        assert "state" in response.json()
        assert "status_url" not in response.json()
```

- [ ] **Step 2: Run the HTTP tests and verify RED**

Run:

```bash
cd backend && .venv/bin/pytest tests/integration/test_sessions_api.py -k "decomposition_returns_accepted or non_decomposition_command" -v
```

Expected: the decomposition assertion fails because the route still returns a
completed 200 response.

- [ ] **Step 3: Assemble the job executor in `main.py`**

Construct a decomposition-only `CommandService` and coordinator before the
lifespan function:

```python
from app.domain.types import CommandAction
from app.services.command_jobs import CommandJobCoordinator
from app.services.command_service import CommandService
from app.services.decomposition_service import DecompositionService

decomposition_commands = CommandService(
    session_factory,
    handlers={
        CommandAction.CONVERT_TO_WORK_ITEM: DecompositionService(
            session_factory, agent_gateway
        ).as_command_handler()
    },
)
command_jobs = CommandJobCoordinator(session_factory, decomposition_commands.execute)
```

Inside lifespan, call `command_jobs.mark_interrupted_jobs()` after
`init_database(db.get_bind())` and pass `command_jobs` to
`build_sessions_router(session_factory, agent_gateway, actor_resolver,
review_service, command_jobs)`.

- [ ] **Step 4: Implement conditional command submission and status routing**

Update the Session router signature to accept `command_jobs`. Inject
`BackgroundTasks` and `Response` into the POST handler, set status 202 only for
decomposition, and schedule only when `submit` says the job needs a runner:

```python
@router.post("/{session_id}/commands", response_model=CommandSubmission)
async def execute_command(
    session_id: str,
    request_body: SessionCommandRequest,
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
) -> CommandSubmission:
    actor_id = actor_resolver.resolve(request, request_body.actor_id)
    request_body = request_body.model_copy(update={"actor_id": actor_id})
    try:
        if request_body.action is CommandAction.CONVERT_TO_WORK_ITEM:
            accepted, should_schedule = command_jobs.submit(session_id, request_body)
            if should_schedule:
                background_tasks.add_task(command_jobs.run, command_jobs.job_id(
                    session_id, request_body.command_id
                ))
            response.status_code = status.HTTP_202_ACCEPTED
            return accepted
        result = await commands.execute(session_id, request_body)
        return result
    except Exception as error:
        raise http_error(error) from error


@router.get(
    "/{session_id}/commands/{command_id}", response_model=CommandJobRead
)
def get_command_job(session_id: str, command_id: str) -> CommandJobRead:
    try:
        return command_jobs.get(session_id, command_id)
    except Exception as error:
        raise http_error(error) from error
```

Add `job_id(session_id, command_id) -> str` to the coordinator so the router does
not reach into persistence. Keep the existing eager PRD binding logic on the
synchronous branch only.

- [ ] **Step 5: Write failing SSE state, resume, and Session-scope tests**

Add endpoint tests using a terminal job so the stream closes deterministically:

```python
def test_command_job_sse_emits_terminal_snapshot_with_version_id(async_job_client):
    client, session_id, command_id = async_job_client
    with client.stream(
        "GET", f"/sessions/{session_id}/commands/{command_id}/events"
    ) as response:
        body = "".join(response.iter_text())
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: command.status" in body
    assert "id: 3" in body
    assert '"status":"succeeded"' in body


def test_command_job_sse_honors_current_last_event_id(async_job_client):
    client, session_id, command_id = async_job_client
    response = client.get(
        f"/sessions/{session_id}/commands/{command_id}/events",
        headers={"Last-Event-ID": "3"},
    )
    assert response.status_code == 200
    assert "event: command.status" not in response.text


def test_command_job_reads_do_not_cross_session_boundary(async_job_client):
    client, _, command_id = async_job_client
    response = client.get(f"/sessions/another-session/commands/{command_id}")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "NOT_FOUND"


@pytest.mark.asyncio
async def test_pending_command_stream_sends_heartbeat_while_unchanged(session_factory):
    seed_project(session_factory)

    async def execute(session_id, command):
        raise AssertionError("pending job must not execute in this stream test")

    coordinator = CommandJobCoordinator(session_factory, execute)
    coordinator.submit("session-1", request())
    stream = coordinator.events(
        "session-1",
        "decompose-1",
        0,
        poll_interval=0,
        heartbeat_interval=0,
    )
    assert "event: command.status" in await anext(stream)
    assert await anext(stream) == ": keep-alive\n\n"
    await stream.aclose()
```

Place the first three tests and `async_job_client` in
`backend/tests/integration/test_sessions_api.py`. Place the heartbeat test in
`backend/tests/integration/test_command_jobs.py`, where `seed_project` and
`request` were defined in Task 2.

Define `async_job_client` in the same test module with a completed public command:

```python
@pytest.fixture
def async_job_client(session_factory):
    agent = ScriptedAgentGateway(
        analyze_results=deque([ClarificationAnalysis(
            ready_for_spec=True, questions=[], assumptions=[]
        )]),
        generate_results=deque([make_valid_spec()]),
        review_results=deque([make_passing_semantic_review()]),
        decompose_results=deque([make_valid_breakdown()]),
    )
    with TestClient(create_app(
        agent_gateway=agent, session_factory=session_factory
    )) as client:
        approved = _approved_http_session(client, "sse-command")
        command_id = "sse-decompose"
        response = client.post(
            f"/sessions/{approved['session_id']}/commands",
            json={
                "command_id": command_id,
                "action": "convert_to_work_item",
                "expected_state_version": approved["state_version"],
                "actor_id": "approver-1",
                "payload": {},
            },
        )
        assert response.status_code == 202
        yield client, approved["session_id"], command_id
```

- [ ] **Step 6: Implement the SSE generator and endpoint**

Add this formatter and generator behavior to `command_jobs.py`:

```python
def _sse(snapshot: CommandJobRead) -> str:
    data = json.dumps(
        snapshot.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return (
        f"id: {snapshot.status_version}\n"
        "event: command.status\n"
        f"data: {data}\n\n"
    )


async def events(
    self,
    session_id: str,
    command_id: str,
    last_event_id: int = 0,
    *,
    poll_interval: float = 0.5,
    heartbeat_interval: float = 15.0,
) -> AsyncIterator[str]:
    last_heartbeat = monotonic()
    while True:
        snapshot = self.get(session_id, command_id)
        if snapshot.status_version > last_event_id:
            yield _sse(snapshot)
            last_event_id = snapshot.status_version
            last_heartbeat = monotonic()
        if snapshot.status in TERMINAL:
            return
        if monotonic() - last_heartbeat >= heartbeat_interval:
            yield ": keep-alive\n\n"
            last_heartbeat = monotonic()
        await asyncio.sleep(poll_interval)
```

Expose it from `sessions.py`:

```python
@router.get("/{session_id}/commands/{command_id}/events")
async def command_job_events(
    session_id: str,
    command_id: str,
    request: Request,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    try:
        cursor = max(0, int(last_event_id or "0"))
        command_jobs.get(session_id, command_id)
    except ValueError as error:
        raise HTTPException(400, detail={
            "code": "INVALID_EVENT_ID", "message": "Last-Event-ID must be an integer."
        }) from error
    except Exception as error:
        raise http_error(error) from error

    async def stream():
        async for frame in command_jobs.events(session_id, command_id, cursor):
            if await request.is_disconnected():
                return
            yield frame

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

- [ ] **Step 7: Update existing decomposition error assertions to the job contract**

In `test_convert_before_approval_returns_illegal_action`,
`test_breakdown_reviewer_rejection_exposes_domain_code`,
`test_nested_decomposition_agent_output_error_is_422`, and
`test_decomposition_local_key_error_preserves_stable_public_code`, replace the
direct error-response assertion with the accepted job plus durable error:

```python
submission = client.post(
    f"/sessions/{session_id}/commands",
    json=command_body,
)
assert submission.status_code == 202
snapshot = client.get(submission.json()["status_url"])
assert snapshot.status_code == 200
assert snapshot.json()["status"] == "failed"
assert snapshot.json()["error"]["code"] == expected_error_code
```

Set the local variables explicitly in each test:

```python
session_id = created["session_id"]
command_body = {
    "command_id": "convert-early",
    "action": "convert_to_work_item",
    "expected_state_version": created["state_version"],
    "actor_id": "approver-1",
    "payload": {},
}
expected_error_code = "ILLEGAL_ACTION"
```

The three breakdown tests keep their existing command bodies and use respectively
`SEMANTIC_REVIEW_BLOCKED`, `INVALID_AGENT_RESULT`, and `LOCAL_KEY_EXISTS`. Keep
the secret-string assertion against both submission and status response text.

- [ ] **Step 8: Run Session API tests and verify GREEN**

Run:

```bash
cd backend && .venv/bin/pytest tests/integration/test_sessions_api.py tests/integration/test_command_jobs.py -q
```

Expected: all Session and job tests pass.

- [ ] **Step 9: Commit the backend transport**

```bash
git add backend/app/api/sessions.py backend/main.py backend/app/services/command_jobs.py backend/tests/integration/test_sessions_api.py
git commit -m "feat: expose decomposition job status and sse"
```

---

### Task 4: Build the Frontend SSE and Five-Second Polling Coordinator

**Files:**
- Modify: `frontend/aios-main/src/api/dto.ts`
- Modify: `frontend/aios-main/src/api/sessions.ts`
- Modify: `frontend/aios-main/src/api/client.test.ts`
- Create: `frontend/aios-main/src/api/commandJobs.ts`
- Create: `frontend/aios-main/src/api/commandJobs.test.ts`

**Interfaces:**
- Consumes: backend `CommandJobAccepted` and `CommandJobRead` JSON contracts, `apiClient`, and `appConfig.apiBaseUrl`.
- Produces: `isCommandJobAccepted`, `getCommandJob`, `commandJobEventsUrl`, and `observeCommandJob(sessionId, accepted, options): { completion: Promise<CommandResultDto>; close(): void }`.

- [ ] **Step 1: Add failing DTO and timeout assertions**

Update `frontend/aios-main/src/api/client.test.ts` so decomposition submission no
longer uses the multi-Agent request timeout:

```typescript
expect(commandTimeoutMs('convert_to_work_item')).toBe(130_000);
```

Add a type-level fixture in the new `commandJobs.test.ts`:

```typescript
import type { CommandJobAcceptedDto, CommandJobReadDto } from './dto';

const accepted: CommandJobAcceptedDto = {
  command_id: 'decompose-1',
  status: 'pending',
  status_url: '/sessions/session-1/commands/decompose-1',
  events_url: '/sessions/session-1/commands/decompose-1/events',
};

const processing: CommandJobReadDto = {
  command_id: 'decompose-1',
  status: 'processing',
  status_version: 2,
  result: null,
  error: null,
  created_at: '2026-09-05T00:00:00Z',
  started_at: '2026-09-05T00:00:01Z',
  completed_at: null,
};
```

- [ ] **Step 2: Run type checking and verify RED**

Run:

```bash
cd frontend/aios-main && npm run lint
```

Expected: FAIL because the command-job DTOs do not exist.

- [ ] **Step 3: Add discriminated DTOs and API functions**

Add to `dto.ts`:

```typescript
export type CommandJobStatus = 'pending' | 'processing' | 'succeeded' | 'failed';

export interface CommandJobAcceptedDto {
  command_id: string;
  status: CommandJobStatus;
  status_url: string;
  events_url: string;
}

export interface CommandJobReadDto {
  command_id: string;
  status: CommandJobStatus;
  status_version: number;
  result: CommandResultDto | null;
  error: { code: string; message: string } | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
}

export type CommandSubmissionDto = CommandResultDto | CommandJobAcceptedDto;
```

Change `executeCommand` to return `CommandSubmissionDto`, remove
`convert_to_work_item` from `DOUBLE_AGENT_ACTIONS`, and add to `sessions.ts`:

```typescript
export function isCommandJobAccepted(
  value: CommandSubmissionDto,
): value is CommandJobAcceptedDto {
  return 'status_url' in value && 'events_url' in value;
}

export const getCommandJob = (
  sessionId: string,
  commandId: string,
  signal?: AbortSignal,
) => apiClient.request<CommandJobReadDto>(
  `/sessions/${sessionId}/commands/${commandId}`,
  { signal },
);

export const commandJobEventsUrl = (eventsUrl: string) =>
  `${appConfig.apiBaseUrl}${eventsUrl}`;
```

- [ ] **Step 4: Write failing observer timing and SSE fallback tests**

In `commandJobs.test.ts`, install fake timers and a controllable EventSource:

```typescript
class FakeEventSource {
  static instances: FakeEventSource[] = [];
  listeners = new Map<string, Array<(event: MessageEvent) => void>>();
  close = vi.fn();
  onerror: ((event: Event) => void) | null = null;

  constructor(readonly url: string) {
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: EventListener) {
    const current = this.listeners.get(type) ?? [];
    current.push(listener as (event: MessageEvent) => void);
    this.listeners.set(type, current);
  }

  emit(type: string, data: unknown) {
    const event = new MessageEvent(type, { data: JSON.stringify(data) });
    for (const listener of this.listeners.get(type) ?? []) listener(event);
  }
}

beforeEach(() => {
  FakeEventSource.instances = [];
  vi.stubGlobal('EventSource', FakeEventSource);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});
```

Add these behavioral assertions:

```typescript
it('polls every 5000 ms without overlapping requests when SSE is quiet', async () => {
  vi.useFakeTimers();
  const poll = vi.fn<() => Promise<CommandJobReadDto>>();
  let release!: (value: CommandJobReadDto) => void;
  poll.mockImplementation(() => new Promise((resolve) => { release = resolve; }));

  const observer = observeCommandJob('session-1', accepted, { poll });
  await vi.advanceTimersByTimeAsync(5_000);
  expect(poll).toHaveBeenCalledTimes(1);
  await vi.advanceTimersByTimeAsync(10_000);
  expect(poll).toHaveBeenCalledTimes(1);

  release(processing);
  await Promise.resolve();
  await vi.advanceTimersByTimeAsync(4_999);
  expect(poll).toHaveBeenCalledTimes(1);
  await vi.advanceTimersByTimeAsync(1);
  expect(poll).toHaveBeenCalledTimes(2);
  observer.close();
});


it('keeps polling after SSE error and resolves once on terminal status', async () => {
  vi.useFakeTimers();
  const succeeded: CommandJobReadDto = {
    ...processing,
    status: 'succeeded',
    status_version: 3,
    result: {
      command_id: 'decompose-1',
      state: sessionState,
      created_resource_ids: ['work-item-1'],
    },
    completed_at: '2026-09-05T00:00:10Z',
  };
  const poll = vi.fn().mockResolvedValue(succeeded);
  const observer = observeCommandJob('session-1', accepted, { poll });
  FakeEventSource.instances[0].onerror?.(new Event('error'));
  await vi.advanceTimersByTimeAsync(5_000);

  await expect(observer.completion).resolves.toEqual(succeeded.result);
  expect(FakeEventSource.instances[0].close).toHaveBeenCalledTimes(1);
  await vi.advanceTimersByTimeAsync(10_000);
  expect(poll).toHaveBeenCalledTimes(1);
});


it('ignores an older poll after SSE has delivered a newer version', async () => {
  vi.useFakeTimers();
  const poll = vi.fn().mockResolvedValue(processing);
  const onStatus = vi.fn();
  const observer = observeCommandJob('session-1', accepted, { poll, onStatus });
  const succeeded: CommandJobReadDto = {
    ...processing,
    status: 'succeeded',
    status_version: 3,
    result: {
      command_id: 'decompose-1',
      state: sessionState,
      created_resource_ids: ['work-item-1'],
    },
    completed_at: '2026-09-05T00:00:10Z',
  };
  FakeEventSource.instances[0].emit('command.status', succeeded);

  await expect(observer.completion).resolves.toEqual(succeeded.result);
  expect(onStatus).toHaveBeenCalledTimes(1);
  expect(onStatus).toHaveBeenLastCalledWith(succeeded);
  await vi.advanceTimersByTimeAsync(5_000);
  expect(poll).not.toHaveBeenCalled();
});


it('reports connectivity trouble only when SSE and polling are both unavailable', async () => {
  vi.useFakeTimers();
  const transportError = vi.fn();
  const poll = vi.fn().mockRejectedValue(new Error('offline'));
  const observer = observeCommandJob('session-1', accepted, {
    poll,
    onTransportError: transportError,
  });
  FakeEventSource.instances[0].onerror?.(new Event('error'));
  await vi.advanceTimersByTimeAsync(5_000);

  expect(transportError).toHaveBeenCalledTimes(1);
  expect(FakeEventSource.instances[0].close).not.toHaveBeenCalled();
  observer.close();
});
```

Declare a complete local `sessionState: SessionStateDto` fixture at the top of
the test file rather than importing a test fixture from another module.

- [ ] **Step 5: Run the observer tests and verify RED**

Run:

```bash
cd frontend/aios-main && npm test -- src/api/commandJobs.test.ts
```

Expected: FAIL because `observeCommandJob` does not exist.

- [ ] **Step 6: Implement one version-aware observer**

Create `commandJobs.ts` with this public interface and behavior:

```typescript
import { ApiError } from './errors';
import { commandJobEventsUrl, getCommandJob } from './sessions';
import type {
  CommandJobAcceptedDto,
  CommandJobReadDto,
  CommandResultDto,
} from './dto';

type ObserverOptions = {
  poll?: () => Promise<CommandJobReadDto>;
  eventSourceFactory?: (url: string) => EventSource;
  pollIntervalMs?: number;
  onStatus?: (status: CommandJobReadDto) => void;
  onTransportError?: (error: unknown) => void;
};

export function observeCommandJob(
  sessionId: string,
  accepted: CommandJobAcceptedDto,
  options: ObserverOptions = {},
): { completion: Promise<CommandResultDto>; close: () => void } {
  const controller = new AbortController();
  const pollIntervalMs = options.pollIntervalMs ?? 5_000;
  const poll = options.poll ?? (() =>
    getCommandJob(sessionId, accepted.command_id, controller.signal));
  const makeSource = options.eventSourceFactory ?? ((url) => new EventSource(url));
  let timer: ReturnType<typeof setTimeout> | undefined;
  let source: EventSource | undefined;
  let closed = false;
  let sseUnavailable = false;
  let lastVersion = 0;
  let resolveCompletion!: (result: CommandResultDto) => void;
  let rejectCompletion!: (error: unknown) => void;
  const completion = new Promise<CommandResultDto>((resolve, reject) => {
    resolveCompletion = resolve;
    rejectCompletion = reject;
  });

  const close = () => {
    if (closed) return;
    closed = true;
    if (timer) clearTimeout(timer);
    controller.abort();
    source?.close();
  };

  const reconcile = (snapshot: CommandJobReadDto) => {
    if (closed || snapshot.status_version <= lastVersion) return;
    lastVersion = snapshot.status_version;
    options.onStatus?.(snapshot);
    if (snapshot.status === 'succeeded' && snapshot.result) {
      close();
      resolveCompletion(snapshot.result);
    } else if (snapshot.status === 'failed') {
      const error = snapshot.error ?? {
        code: 'WORKFLOW_ERROR', message: '后台拆分任务失败。',
      };
      close();
      rejectCompletion(new ApiError({
        status: 0,
        code: error.code,
        message: error.message,
        retryable: true,
      }));
    }
  };

  const schedulePoll = () => {
    timer = setTimeout(async () => {
      try {
        reconcile(await poll());
      } catch (error) {
        if (controller.signal.aborted) return;
        if (sseUnavailable) options.onTransportError?.(error);
      } finally {
        if (!closed) schedulePoll();
      }
    }, pollIntervalMs);
  };

  schedulePoll();
  try {
    source = makeSource(commandJobEventsUrl(accepted.events_url));
    source.addEventListener('command.status', (event) => {
      try {
        sseUnavailable = false;
        reconcile(JSON.parse((event as MessageEvent<string>).data));
      } catch {
        // A malformed frame is ignored; the durable poll remains authoritative.
      }
    });
    source.onerror = () => { sseUnavailable = true; };
  } catch (error) {
    sseUnavailable = true;
    options.onTransportError?.(error);
  }
  return { completion, close };
}
```

Caller-supplied `poll` and `eventSourceFactory` are test seams. Guard JSON parsing
so a malformed SSE frame is ignored and the poll remains active.

- [ ] **Step 7: Run frontend API tests and verify GREEN**

Run:

```bash
cd frontend/aios-main && npm test -- src/api/commandJobs.test.ts src/api/client.test.ts && npm run lint
```

Expected: observer tests, timeout tests, and TypeScript checks pass.

- [ ] **Step 8: Commit the frontend observer**

```bash
git add frontend/aios-main/src/api/dto.ts frontend/aios-main/src/api/sessions.ts frontend/aios-main/src/api/client.test.ts frontend/aios-main/src/api/commandJobs.ts frontend/aios-main/src/api/commandJobs.test.ts
git commit -m "feat: observe command jobs with sse and polling"
```

---

### Task 5: Integrate Background Decomposition into the Workspace

**Files:**
- Modify: `frontend/aios-main/src/api/ApiWorkspace.tsx`
- Modify: `frontend/aios-main/src/api/workspace.test.tsx`

**Interfaces:**
- Consumes: `executeCommand`, `isCommandJobAccepted`, and `observeCommandJob(sessionId, accepted, options)`.
- Produces: user-triggered and reload-resumed decomposition with one resource refresh on success.

- [ ] **Step 1: Write a failing accepted-job workflow test**

Extend the workspace fetch stub with a queue of command-job statuses and stub
`EventSource`. Add this test with fake timers:

```typescript
class WorkspaceEventSource {
  static instances: WorkspaceEventSource[] = [];
  close = vi.fn();
  onerror: ((event: Event) => void) | null = null;
  addEventListener = vi.fn();

  constructor(readonly url: string) {
    WorkspaceEventSource.instances.push(this);
  }
}

// Add these two statements to the existing beforeEach body.
WorkspaceEventSource.instances = [];
vi.stubGlobal('EventSource', WorkspaceEventSource);

it('submits decomposition once and polls its job every five seconds', async () => {
  vi.useFakeTimers();
  localStorage.setItem('firstflight.active-session-id', 'session-1');
  const approved = {
    ...state,
    current_spec_status: 'APPROVED',
    legal_actions: ['convert_to_work_item'] as SessionStateDto['legal_actions'],
  };
  resourceOverrides['/sessions/session-1/state'] = approved;
  resourceOverrides['/sessions/session-1/commands'] = {
    command_id: 'decompose-job-1',
    status: 'pending',
    status_url: '/sessions/session-1/commands/decompose-job-1',
    events_url: '/sessions/session-1/commands/decompose-job-1/events',
  };
  resourceOverrides['/sessions/session-1/commands/decompose-job-1'] = {
    command_id: 'decompose-job-1',
    status: 'succeeded',
    status_version: 3,
    result: {
      command_id: 'decompose-job-1',
      state: { ...approved, phase: 'AGENT_SPECS_READY', state_version: 7 },
      created_resource_ids: ['task-created'],
    },
    error: null,
    created_at: '2026-09-05T00:00:00Z',
    started_at: '2026-09-05T00:00:01Z',
    completed_at: '2026-09-05T00:00:10Z',
  };

  render(<ApiWorkspace />);
  fireEvent.click(await screen.findByRole('button', { name: /知识问答.*打开 PRD 审核/ }));
  const dialog = within(await screen.findByRole('dialog', { name: 'PRD 审核' }));
  fireEvent.click(dialog.getByRole('button', { name: '开始任务拆分' }));

  await waitFor(() => expect(fetchSpy.mock.calls.some(([input, options]) =>
    options?.method === 'POST'
    && String(input).endsWith('/sessions/session-1/commands')
  )).toBe(true));
  expect(screen.getByText(/正在后台拆解子 WorkItem/)).toBeTruthy();

  await vi.advanceTimersByTimeAsync(4_999);
  expect(fetchSpy.mock.calls.some(([input]) =>
    String(input).endsWith('/commands/decompose-job-1')
  )).toBe(false);
  await vi.advanceTimersByTimeAsync(1);
  await waitFor(() => expect(fetchSpy.mock.calls.some(([input]) =>
    String(input).endsWith('/commands/decompose-job-1')
  )).toBe(true));
  expect(localStorage.getItem('firstflight.decomposition-job.session-1')).toBeNull();
});
```

Adapt the shared fetch stub so `/sessions/session-1/commands` can return a
configured response while still recording the submitted request body.

- [ ] **Step 2: Run the workspace test and verify RED**

Run:

```bash
cd frontend/aios-main && npm test -- src/api/workspace.test.tsx -t "polls its job every five seconds"
```

Expected: FAIL because the workspace assumes every command response already has
`state`.

- [ ] **Step 3: Integrate submission, persistence, and terminal reconciliation**

In `ApiWorkspace.tsx`, keep the synchronous `submitCommand` contract for existing
callers and add a decomposition-specific helper:

```typescript
const commandObservation = useRef<{ close: () => void } | null>(null);
const decompositionJobKey = (sessionId: string) =>
  `firstflight.decomposition-job.${sessionId}`;

async function submitDecomposition(baseState: SessionStateDto) {
  const fingerprint = JSON.stringify({
    sessionId: baseState.session_id,
    action: 'convert_to_work_item',
    stateVersion: baseState.state_version,
  });
  const savedCommandId = localStorage.getItem(
    decompositionJobKey(baseState.session_id),
  );
  const commandId = pendingCommandIds.current.get(fingerprint)
    ?? savedCommandId
    ?? crypto.randomUUID();
  pendingCommandIds.current.set(fingerprint, commandId);
  const submission = await executeCommand(baseState.session_id, {
    commandId,
    action: 'convert_to_work_item',
    expectedStateVersion: baseState.state_version,
  });
  if (!isCommandJobAccepted(submission)) {
    throw new Error('Decomposition command did not return a background job.');
  }
  localStorage.setItem(decompositionJobKey(baseState.session_id), submission.command_id);
  setWorkflowProgress('PRD 已确认，正在后台拆解子 WorkItem 和 Agent Spec…');
  const observer = observeCommandJob(baseState.session_id, submission, {
    onStatus: () => setError(null),
    onTransportError: (reason) => setError(errorText(reason)),
  });
  commandObservation.current = observer;
  try {
    const result = await observer.completion;
    pendingCommandIds.current.delete(fingerprint);
    localStorage.removeItem(decompositionJobKey(baseState.session_id));
    updateSessionState(result.state);
    return result.state;
  } finally {
    if (commandObservation.current === observer) commandObservation.current = null;
  }
}
```

Change `submitCommand` to reject an unexpected accepted job for synchronous
actions, and call `submitDecomposition` only in `confirmPrdAndDecompose`.
Use a cleanup effect that calls `commandObservation.current?.close()` on unmount.

- [ ] **Step 4: Add failing reload recovery and durable failure tests**

Add two tests:

```typescript
it('resumes a saved decomposition job after reload without posting it again', async () => {
  vi.useFakeTimers();
  localStorage.setItem('firstflight.active-session-id', 'session-1');
  localStorage.setItem('firstflight.decomposition-job.session-1', 'saved-job');
  resourceOverrides['/sessions/session-1/commands/saved-job'] = {
    command_id: 'saved-job',
    status: 'processing',
    status_version: 2,
    result: null,
    error: null,
    created_at: '2026-09-05T00:00:00Z',
    started_at: '2026-09-05T00:00:01Z',
    completed_at: null,
  };
  render(<ApiWorkspace />);
  await waitFor(() => expect(fetchSpy.mock.calls.some(([input]) =>
    String(input).endsWith('/commands/saved-job')
  )).toBe(true));
  expect(fetchSpy.mock.calls.some(([input, options]) =>
    options?.method === 'POST' && String(input).endsWith('/commands')
  )).toBe(false);
});


it('shows the durable command error and stops observing', async () => {
  vi.useFakeTimers();
  localStorage.setItem('firstflight.active-session-id', 'session-1');
  const approved = {
    ...state,
    current_spec_status: 'APPROVED',
    legal_actions: ['convert_to_work_item'] as SessionStateDto['legal_actions'],
  };
  resourceOverrides['/sessions/session-1/state'] = approved;
  resourceOverrides['/sessions/session-1/commands'] = {
    command_id: 'decompose-job-1',
    status: 'pending',
    status_url: '/sessions/session-1/commands/decompose-job-1',
    events_url: '/sessions/session-1/commands/decompose-job-1/events',
  };
  resourceOverrides['/sessions/session-1/commands/decompose-job-1'] = {
    command_id: 'decompose-job-1',
    status: 'failed',
    status_version: 3,
    result: null,
    error: {
      code: 'AGENT_UNAVAILABLE',
      message: 'An Agent operation is unavailable; retry the workflow action.',
    },
    created_at: '2026-09-05T00:00:00Z',
    started_at: '2026-09-05T00:00:01Z',
    completed_at: '2026-09-05T00:00:10Z',
  };
  render(<ApiWorkspace />);
  fireEvent.click(await screen.findByRole('button', {
    name: /知识问答.*打开 PRD 审核/,
  }));
  const dialog = within(await screen.findByRole('dialog', { name: 'PRD 审核' }));
  fireEvent.click(dialog.getByRole('button', { name: '开始任务拆分' }));
  await vi.advanceTimersByTimeAsync(5_000);
  expect(await screen.findByText(
    /AGENT_UNAVAILABLE：An Agent operation is unavailable/
  )).toBeTruthy();
  await vi.advanceTimersByTimeAsync(10_000);
  expect(commandStatusRequestCount()).toBe(1);
});


it('closes SSE and cancels polling when the workspace unmounts', async () => {
  vi.useFakeTimers();
  localStorage.setItem('firstflight.active-session-id', 'session-1');
  const approved = {
    ...state,
    current_spec_status: 'APPROVED',
    legal_actions: ['convert_to_work_item'] as SessionStateDto['legal_actions'],
  };
  resourceOverrides['/sessions/session-1/state'] = approved;
  resourceOverrides['/sessions/session-1/commands'] = {
    command_id: 'decompose-job-1',
    status: 'pending',
    status_url: '/sessions/session-1/commands/decompose-job-1',
    events_url: '/sessions/session-1/commands/decompose-job-1/events',
  };
  resourceOverrides['/sessions/session-1/commands/decompose-job-1'] = {
    command_id: 'decompose-job-1',
    status: 'processing',
    status_version: 2,
    result: null,
    error: null,
    created_at: '2026-09-05T00:00:00Z',
    started_at: '2026-09-05T00:00:01Z',
    completed_at: null,
  };
  const view = render(<ApiWorkspace />);
  fireEvent.click(await screen.findByRole('button', {
    name: /知识问答.*打开 PRD 审核/,
  }));
  const dialog = within(await screen.findByRole('dialog', { name: 'PRD 审核' }));
  fireEvent.click(dialog.getByRole('button', { name: '开始任务拆分' }));
  await waitFor(() => expect(WorkspaceEventSource.instances).toHaveLength(1));

  view.unmount();
  expect(WorkspaceEventSource.instances[0].close).toHaveBeenCalledTimes(1);
  await vi.advanceTimersByTimeAsync(10_000);
  expect(commandStatusRequestCount()).toBe(0);
});
```

Add a local `commandStatusRequestCount()` helper that filters `fetchSpy.mock.calls`
for the exact `/sessions/session-1/commands/decompose-job-1` GET URL:

```typescript
function commandStatusRequestCount(): number {
  return fetchSpy.mock.calls.filter(([input, options]) =>
    (!options?.method || options.method === 'GET')
    && new URL(String(input)).pathname
      === '/sessions/session-1/commands/decompose-job-1'
  ).length;
}
```

- [ ] **Step 5: Implement reload recovery**

When a Session state becomes available, query a saved command immediately. If it
is terminal, reconcile it at once. If it is pending or processing, construct the
accepted URLs deterministically and start `observeCommandJob`:

```typescript
useEffect(() => {
  if (!state || commandObservation.current) return;
  const commandId = localStorage.getItem(decompositionJobKey(state.session_id));
  if (!commandId) return;
  const controller = new AbortController();
  const resume = async () => {
    try {
      const snapshot = await getCommandJob(state.session_id, commandId, controller.signal);
      if (snapshot.status === 'failed') {
        setError(`${snapshot.error?.code ?? 'WORKFLOW_ERROR'}：${snapshot.error?.message ?? '后台拆分任务失败。'}`);
        return;
      }
      if (snapshot.status === 'succeeded' && snapshot.result) {
        localStorage.removeItem(decompositionJobKey(state.session_id));
        updateSessionState(snapshot.result.state);
        await refreshResources(state.session_id);
        return;
      }
      setWorkflowProgress('正在恢复后台任务拆分进度…');
      const observer = observeCommandJob(
        state.session_id,
        {
          command_id: commandId,
          status: snapshot.status,
          status_url: `/sessions/${state.session_id}/commands/${commandId}`,
          events_url: `/sessions/${state.session_id}/commands/${commandId}/events`,
        },
        {
          onStatus: () => setError(null),
          onTransportError: (reason) => setError(errorText(reason)),
        },
      );
      commandObservation.current = observer;
      const result = await observer.completion;
      localStorage.removeItem(decompositionJobKey(state.session_id));
      updateSessionState(result.state);
      await refreshResources(state.session_id);
    } catch (reason) {
      if (normalizeNetworkError(reason).code !== 'REQUEST_ABORTED') {
        setError(errorText(reason));
      }
    } finally {
      setWorkflowProgress(null);
    }
  };
  void resume();
  return () => {
    controller.abort();
    commandObservation.current?.close();
    commandObservation.current = null;
  };
}, [state?.session_id]);
```

Extract the shared terminal reconciliation used by new submissions and recovery
so each terminal `status_version` refreshes resources exactly once.

- [ ] **Step 6: Run workspace tests and verify GREEN**

Run:

```bash
cd frontend/aios-main && npm test -- src/api/workspace.test.tsx src/api/commandJobs.test.ts && npm run lint
```

Expected: the new asynchronous tests and all existing workspace regressions pass.

- [ ] **Step 7: Commit workspace integration**

```bash
git add frontend/aios-main/src/api/ApiWorkspace.tsx frontend/aios-main/src/api/workspace.test.tsx
git commit -m "feat: run task decomposition in background"
```

---

### Task 6: Document and Verify the Complete Flow

**Files:**
- Modify: `backend/readme.md`
- Test: `backend/tests/unit/test_database_schema.py`
- Test: `backend/tests/integration/test_command_jobs.py`
- Test: `backend/tests/integration/test_sessions_api.py`
- Test: `frontend/aios-main/src/api/client.test.ts`
- Test: `frontend/aios-main/src/api/commandJobs.test.ts`
- Test: `frontend/aios-main/src/api/workspace.test.tsx`

**Interfaces:**
- Consumes: all earlier tasks.
- Produces: operator-facing API documentation and verified backend/frontend builds.

- [ ] **Step 1: Add the exact API examples to the backend README**

Document the conditional response and observation sequence with these commands:

```bash
curl -i -X POST http://127.0.0.1:8088/sessions/SESSION_ID/commands \
  -H 'Content-Type: application/json' \
  -d '{"command_id":"COMMAND_ID","action":"convert_to_work_item","expected_state_version":7,"payload":{}}'

curl http://127.0.0.1:8088/sessions/SESSION_ID/commands/COMMAND_ID

curl -N http://127.0.0.1:8088/sessions/SESSION_ID/commands/COMMAND_ID/events
```

State that POST returns 202 after the durable job exists, SSE is the live channel,
the shipped frontend also polls every 5,000 ms, and process restart marks an
in-memory runner interrupted so the identical command ID can be resubmitted.

- [ ] **Step 2: Run the complete backend suite**

Run:

```bash
cd backend && .venv/bin/pytest -q
```

Expected: all backend tests pass with no traceback or warning introduced by this
change.

- [ ] **Step 3: Run the complete frontend suite and build**

Run:

```bash
cd frontend/aios-main && npm test && npm run lint && npm run build
```

Expected: all Vitest tests pass, TypeScript reports no errors, and Vite produces a
successful production build.

- [ ] **Step 4: Inspect the final diff for transport regressions**

Run:

```bash
git diff --check
git status --short
git diff --stat origin/main...HEAD
```

Expected: no whitespace errors, only scoped files are changed, and no database,
log, `.env`, credential, or generated build artifact is staged.

- [ ] **Step 5: Commit documentation and final test adjustments**

```bash
git add backend/readme.md
git commit -m "docs: explain asynchronous decomposition commands"
```

- [ ] **Step 6: Record the release-ready commit for the user**

Run:

```bash
git status --short --branch
git log -6 --oneline --decorate
```

Expected: the worktree is clean and `main` is ahead of `origin/main`; remind the
user to push the new commits to preserve this version remotely.
