import asyncio
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
