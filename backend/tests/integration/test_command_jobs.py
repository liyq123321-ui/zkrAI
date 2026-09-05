import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier, Event, Lock

import pytest
from sqlalchemy import event

from app.database.database import (
    create_engine_for_url,
    init_database,
    make_session_factory,
)
from app.database.models import CommandJob, Project
from app.domain.types import CommandAction
from app.schemas.workflow import CommandResult, SessionCommandRequest, SessionState
from app.services.command_jobs import CommandJobCoordinator
from app.services.command_service import CommandConflict, StaleState


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
        db.add(
            Project(
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
            )
        )
        db.commit()


@pytest.fixture
def file_session_factory(tmp_path):
    engine = create_engine_for_url(f"sqlite:///{tmp_path / 'command-jobs.sqlite'}")

    @event.listens_for(engine, "connect")
    def disable_sqlite_lock_wait(dbapi_connection, connection_record):
        dbapi_connection.execute("PRAGMA busy_timeout = 0")

    init_database(engine)
    yield make_session_factory(engine)
    engine.dispose()


def concurrent_submissions(engine, coordinator, requests):
    barrier = Barrier(len(requests))
    lock = Lock()
    reads = 0

    def synchronize_after_initial_job_read(
        connection, cursor, statement, parameters, context, executemany
    ):
        nonlocal reads
        if "FROM command_jobs" not in statement:
            return
        with lock:
            if reads >= len(requests):
                return
            reads += 1
        barrier.wait(timeout=5)

    event.listen(engine, "after_cursor_execute", synchronize_after_initial_job_read)
    try:
        with ThreadPoolExecutor(max_workers=len(requests)) as pool:
            futures = [
                pool.submit(coordinator.submit, "session-1", command)
                for command in requests
            ]
            return [future.result(timeout=10) for future in futures]
    finally:
        event.remove(engine, "after_cursor_execute", synchronize_after_initial_job_read)


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


def test_file_sqlite_simultaneous_identical_submissions_converge(file_session_factory):
    seed_project(file_session_factory)

    async def execute(session_id, command):
        raise AssertionError("executor is not called by submit")

    coordinator = CommandJobCoordinator(file_session_factory, execute)
    results = concurrent_submissions(
        file_session_factory.kw["bind"], coordinator, [request(), request()]
    )

    accepted = [result[0] for result in results]
    assert accepted[0] == accepted[1]
    assert sum(should_schedule for _, should_schedule in results) == 1


def test_file_sqlite_simultaneous_conflicting_submissions_converge(file_session_factory):
    seed_project(file_session_factory)

    async def execute(session_id, command):
        raise AssertionError("executor is not called by submit")

    coordinator = CommandJobCoordinator(file_session_factory, execute)
    first, second = request(), request().model_copy(
        update={"expected_state_version": 8}
    )
    with pytest.raises(CommandConflict):
        concurrent_submissions(file_session_factory.kw["bind"], coordinator, [first, second])

    with file_session_factory() as db:
        assert db.query(CommandJob).count() == 1


def test_file_sqlite_simultaneous_failed_retries_schedule_once(file_session_factory):
    seed_project(file_session_factory)

    async def execute(session_id, command):
        raise AssertionError("executor is not called by submit")

    coordinator = CommandJobCoordinator(file_session_factory, execute)
    coordinator.submit("session-1", request())
    assert coordinator.mark_interrupted_jobs() == 1

    results = concurrent_submissions(
        file_session_factory.kw["bind"], coordinator, [request(), request()]
    )

    assert [accepted.status for accepted, _ in results] == ["pending", "pending"]
    assert sum(should_schedule for _, should_schedule in results) == 1
    assert coordinator.get("session-1", "decompose-1").status_version == 3


def test_file_sqlite_submission_waits_through_transient_writer_lock(file_session_factory):
    seed_project(file_session_factory)

    async def execute(session_id, command):
        raise AssertionError("executor is not called by submit")

    coordinator = CommandJobCoordinator(file_session_factory, execute)
    engine = file_session_factory.kw["bind"]
    lock_holder = engine.connect()
    lock_holder.exec_driver_sql("BEGIN IMMEDIATE")
    lock_conflict = Event()

    def observe_locked_insert(exception_context):
        if (
            "INSERT INTO command_jobs" in (exception_context.statement or "")
            and "database is locked" in str(exception_context.original_exception)
        ):
            lock_conflict.set()

    event.listen(engine, "handle_error", observe_locked_insert)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(coordinator.submit, "session-1", request())
            assert lock_conflict.wait(timeout=2)
            lock_holder.rollback()
            accepted, should_schedule = pending.result(timeout=5)
    finally:
        event.remove(engine, "handle_error", observe_locked_insert)
        if lock_holder.in_transaction():
            lock_holder.rollback()
        lock_holder.close()

    assert accepted.status == "pending"
    assert should_schedule is True


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


@pytest.mark.asyncio
async def test_stale_state_failure_keeps_public_code_without_internal_detail(
    session_factory,
):
    seed_project(session_factory)

    async def execute(session_id, command):
        raise StaleState("expected=7 actual=8 internal=/private/workflow.db")

    coordinator = CommandJobCoordinator(session_factory, execute)
    coordinator.submit("session-1", request())
    with session_factory() as db:
        job_id = db.query(CommandJob).one().id
    await coordinator.run(job_id)

    snapshot = coordinator.get("session-1", "decompose-1")
    assert snapshot.status == "failed"
    assert snapshot.error is not None
    assert snapshot.error.code == "STALE_STATE"
    assert snapshot.error.message == (
        "The workflow state changed or the request conflicts; retry with current state."
    )
    assert "internal" not in snapshot.model_dump_json()
    assert "/private" not in snapshot.model_dump_json()


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


@pytest.mark.asyncio
async def test_stale_executor_cannot_complete_a_retried_job(session_factory):
    seed_project(session_factory)
    first_started = asyncio.Event()
    second_started = asyncio.Event()
    release_first = asyncio.Event()
    release_second = asyncio.Event()
    calls = 0

    async def execute(session_id, command):
        nonlocal calls
        calls += 1
        if calls == 1:
            first_started.set()
            await release_first.wait()
        else:
            second_started.set()
            await release_second.wait()
        return CommandResult(
            command_id=command.command_id,
            state=state(),
            created_resource_ids=["work-item-1"],
        )

    coordinator = CommandJobCoordinator(session_factory, execute)
    coordinator.submit("session-1", request())
    with session_factory() as db:
        job_id = db.query(CommandJob).one().id

    first_run = asyncio.create_task(coordinator.run(job_id))
    await first_started.wait()
    assert coordinator.mark_interrupted_jobs() == 1
    coordinator.submit("session-1", request())
    second_run = asyncio.create_task(coordinator.run(job_id))
    await second_started.wait()

    release_first.set()
    await first_run
    in_progress = coordinator.get("session-1", "decompose-1")
    assert in_progress.status == "processing"
    assert in_progress.status_version == 5

    release_second.set()
    await second_run
    assert coordinator.get("session-1", "decompose-1").status == "succeeded"


@pytest.mark.asyncio
async def test_pending_events_emit_a_status_snapshot_then_heartbeat(session_factory):
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
