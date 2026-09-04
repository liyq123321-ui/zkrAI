"""Drive WorkItem execution across the dependency DAG.

Execution state is append-only: every command appends a :class:`WorkItemRun`
row and the current status is the latest run for that item. This keeps
``WorkItem`` immutable after decomposition, so the command guard can admit new
rows without opening a mutation exemption for existing ones -- and it leaves a
complete audit trail of retries, failures and re-runs.

The handler validates twice on purpose:

* ``prepare``  -- fail fast with a message a human can act on;
* ``materialize`` -- re-validate inside the transaction, because state may have
  moved between the two phases.

Both phases read through the *same* snapshot helper so the rules cannot drift.
"""

from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

from sqlalchemy.orm import Session

from app.database.models import WorkItemRun
from app.domain.types import CommandAction, WorkItemRunStatus
from app.services.command_service import (
    ActionScopedUnitOfWork,
    CommandHandlerRejected,
    CommandHandlerResult,
    MaterializeContext,
    PreparedCommand,
    PrepareContext,
)


def _new_id() -> str:
    return str(uuid4())


def _target_work_item_id(request) -> str:
    payload = request.payload or {}
    work_item_id = payload.get("work_item_id")
    if not isinstance(work_item_id, str) or not work_item_id.strip():
        raise CommandHandlerRejected("payload.work_item_id is required")
    return work_item_id.strip()


def _assert_allowed(
    action: CommandAction,
    work_item_id: str,
    executable: bool,
    status: str,
    startable_ids: set[str],
) -> None:
    """Reject an execution command that the dependency graph does not permit."""

    if not executable:
        raise CommandHandlerRejected(
            f"work item {work_item_id!r} is not executable; only TASK items run"
        )

    if action is CommandAction.START_TASK:
        if status != "todo":
            raise CommandHandlerRejected(
                f"work item {work_item_id!r} is {status!r}; only 'todo' items can start"
            )
        if work_item_id not in startable_ids:
            raise CommandHandlerRejected(
                f"work item {work_item_id!r} still has unfinished dependencies"
            )
        return

    if status != "in_progress":
        raise CommandHandlerRejected(
            f"work item {work_item_id!r} is {status!r}; only 'in_progress' items "
            f"can be completed or failed"
        )


class ExecutionService:
    """Start, complete and fail work items in dependency order."""

    ACTION_TO_RUN_STATUS: dict[CommandAction, WorkItemRunStatus] = {
        CommandAction.START_TASK: WorkItemRunStatus.STARTED,
        CommandAction.COMPLETE_TASK: WorkItemRunStatus.SUCCEEDED,
        CommandAction.FAIL_TASK: WorkItemRunStatus.FAILED,
    }

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    async def prepare(self, context: PrepareContext) -> PreparedCommand:
        work_item_id = _target_work_item_id(context.request)
        run_status = self.ACTION_TO_RUN_STATUS[context.request.action]

        # Friendly pre-flight: surface a readable rejection before the caller
        # pays for a state-version round trip.
        with self._session_factory() as db:
            executable, status, _edges, startable = ActionScopedUnitOfWork(
                db, context.project_id, context.request.action
            ).work_item_execution_snapshot(work_item_id)
        _assert_allowed(
            context.request.action, work_item_id, executable, status, startable
        )

        # agent_backed=False: no external side effect, so the preparation lease
        # is held for a local read only and retries are always safe.
        return PreparedCommand(
            payload={"work_item_id": work_item_id, "run_status": run_status.value},
            agent_backed=False,
            audit_payload={
                "work_item_id": work_item_id,
                "run_status": run_status.value,
            },
        )

    def materialize(
        self,
        uow: ActionScopedUnitOfWork,
        context: MaterializeContext,
        prepared: PreparedCommand,
    ) -> CommandHandlerResult:
        work_item_id = str(prepared.payload["work_item_id"])
        run_status = str(prepared.payload["run_status"])

        # Authoritative check: the graph may have moved since prepare ran.
        executable, status, _edges, startable = uow.work_item_execution_snapshot(
            work_item_id
        )
        _assert_allowed(
            context.request.action, work_item_id, executable, status, startable
        )

        run = WorkItemRun(
            id=_new_id(),
            project_id=context.project_id,
            work_item_id=work_item_id,
            status=run_status,
            actor_id=context.request.actor_id,
            command_id=context.request.command_id,
            note=_note(context.request),
        )
        uow.add_work_item_run(run)

        # phase/spec_status stay None: execution never moves the workflow, it
        # only advances WorkItem run state. state_version still advances, which
        # is what makes the UI refresh.
        return CommandHandlerResult(
            created_resource_ids=[run.id],
            audit_payload={
                "work_item_id": work_item_id,
                "run_status": run_status,
                "work_item_run_id": run.id,
            },
        )


def _note(request) -> str | None:
    message = (request.message or "").strip()
    return message or None
