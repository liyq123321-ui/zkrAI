"""Durable coordination for asynchronous decomposition commands."""

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from time import monotonic, sleep
from uuid import uuid4

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.database.models import AgentCall, AuditEvent, CommandAttempt, CommandJob, Project
from app.agents.progress import bind_agent_progress
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


CommandExecutor = Callable[[str, SessionCommandRequest], Awaitable[CommandResult]]
TERMINAL = frozenset({"succeeded", "failed"})
AUTO_REPAIR_RECIPES: dict[str, dict[str, object]] = {
    "BLOCKING_OPEN_QUESTION": {
        "id": "decomposition-generated-blocker",
        "summary": "Remove planning-time blockers that were not present in the approved PRD.",
        "steps": [
            "Compare every blocking question with the approved PRD.",
            "Use a conservative testable default for implementation details within approved scope.",
            "Keep residual uncertainty non-blocking with a risk owner and accepted consequence.",
        ],
    },
    "INVALID_AGENT_RESULT": {
        "id": "agent-contract-repair",
        "summary": "Regenerate the failed Agent result from its validation diagnostics.",
        "steps": [
            "Preserve approved scope and the selected SDLC route.",
            "Repair all reported schema and consistency findings together.",
            "Run every server validation gate before persistence.",
        ],
    },
    "SEMANTIC_REVIEW_BLOCKED": {
        "id": "semantic-plan-repair",
        "summary": "Revise the task breakdown from the independent reviewer findings.",
        "steps": [
            "Retain valid milestones, task keys and dependencies.",
            "Correct every connected contract inconsistency.",
            "Submit the complete plan to independent review again.",
        ],
    },
    "AGENT_UNAVAILABLE": {
        "id": "agent-call-recovery",
        "summary": "Retry from durable Agent checkpoints without duplicating materialized work.",
        "steps": [
            "Inspect durable AgentCall status and failure evidence.",
            "Reuse valid completed checkpoints and regenerate only failed work.",
            "Revalidate current project state before materialization.",
        ],
    },
    "PROCESS_INTERRUPTED": {
        "id": "interrupted-command-recovery",
        "summary": "Resume an interrupted command from durable checkpoints.",
        "steps": [
            "Inspect the last persisted preparation and Agent calls.",
            "Resume only work that has no durable successful result.",
            "Recheck state version and all write guards.",
        ],
    },
}
_SQLITE_LOCK_RETRY_SECONDS = 1.0
_SQLITE_LOCK_INITIAL_DELAY = 0.005
_SQLITE_LOCK_MAX_DELAY = 0.05


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


def _is_sqlite_lock(error: OperationalError) -> bool:
    message = str(error.orig).lower()
    return "database is locked" in message or "database table is locked" in message


class CommandJobCoordinator:
    """Submit, claim, and persist results for one durable command job."""

    def __init__(
        self, session_factory: Callable[[], Session], execute: CommandExecutor
    ) -> None:
        self._session_factory = session_factory
        self._execute = execute

    def submit(
        self, session_id: str, request: SessionCommandRequest
    ) -> tuple[CommandJobAccepted, bool]:
        if request.action is not CommandAction.CONVERT_TO_WORK_ITEM:
            raise ValueError("only decomposition commands may use background jobs")

        digest = _input_hash(request)
        deadline = monotonic() + _SQLITE_LOCK_RETRY_SECONDS
        delay = _SQLITE_LOCK_INITIAL_DELAY
        while True:
            try:
                return self._submit_once(session_id, request, digest)
            except OperationalError as error:
                remaining = deadline - monotonic()
                if remaining <= 0 or not _is_sqlite_lock(error):
                    raise
                sleep(min(delay, remaining))
                delay = min(delay * 2, _SQLITE_LOCK_MAX_DELAY)

    def submit_auto_repair(
        self, session_id: str, command_id: str, actor_id: str
    ) -> tuple[CommandJobAccepted, bool]:
        """Create a fresh, auditable retry enriched with bounded failure evidence."""

        with self._session_factory() as db:
            source = db.query(CommandJob).filter_by(
                session_id=session_id, command_id=command_id
            ).one_or_none()
            if source is None:
                raise KeyError("command job not found")
            if source.status != "failed" or not source.error_code:
                raise ValueError("only failed command jobs can be automatically repaired")
            original = SessionCommandRequest.model_validate(source.request_payload)
            if original.actor_id != actor_id:
                raise CommandConflict("automatic repair actor does not own the failed command")
            project = db.get(Project, source.project_id)
            if project is None or project.session_id != session_id:
                raise KeyError("project not found")
            attempt = db.query(CommandAttempt).filter_by(
                session_id=session_id, command_id=command_id
            ).one_or_none()
            call_ids = list(attempt.agent_call_ids or []) if attempt is not None else []
            calls = [db.get(AgentCall, call_id) for call_id in call_ids[-8:]]
            recipe = AUTO_REPAIR_RECIPES.get(source.error_code, {
                "id": "validated-command-retry",
                "summary": "Retry the failed workflow command with its durable diagnostics.",
                "steps": [
                    "Preserve approved scope and current project state.",
                    "Use prior failure evidence to correct the next Agent result.",
                    "Run normal validation and materialization guards.",
                ],
            })
            evidence = [{
                "agent_call_id": call.id,
                "operation": call.operation,
                "status": call.status,
                "error": (call.error or "")[:1000] or None,
            } for call in calls if call is not None]
            repair_context = {
                "source_command_id": command_id,
                "error_code": source.error_code,
                "error_message": (source.error_message or "")[:1000],
                "attempt_error": ((attempt.error or "")[:2000] if attempt is not None else None),
                "project_phase": project.phase,
                "project_state_version": project.state_version,
                "recipe": recipe,
                "agent_call_evidence": evidence,
            }
            repair_request = original.model_copy(update={
                "command_id": _id(),
                "expected_state_version": project.state_version,
                "payload": {**dict(original.payload), "auto_repair": repair_context},
            })
            source_project_id = source.project_id
            source_error_code = source.error_code

        accepted, should_schedule = self.submit(session_id, repair_request)
        if accepted.command_id != repair_request.command_id:
            raise CommandConflict("another decomposition command is already active")
        with self._session_factory() as db:
            db.add(AuditEvent(
                id=_id(), project_id=source_project_id, session_id=session_id,
                event_type="AUTO_REPAIR_REQUESTED", actor_id=actor_id,
                payload={
                    "source_command_id": command_id,
                    "repair_command_id": accepted.command_id,
                    "error_code": source_error_code,
                    "recipe_id": recipe["id"],
                },
            ))
            db.commit()
        return accepted, should_schedule

    def _submit_once(
        self, session_id: str, request: SessionCommandRequest, digest: str
    ) -> tuple[CommandJobAccepted, bool]:
        with self._session_factory() as db:
            with db.begin():
                project = db.query(Project).filter_by(session_id=session_id).one_or_none()
                if project is None:
                    raise KeyError("project not found")

                job = db.query(CommandJob).filter_by(
                    session_id=session_id, command_id=request.command_id
                ).one_or_none()
                if job is not None and job.input_hash != digest:
                    raise CommandConflict("command_id was used with different input")
                active = (
                    db.query(CommandJob)
                    .filter(
                        CommandJob.session_id == session_id,
                        CommandJob.status.in_(("pending", "processing")),
                    )
                    .order_by(CommandJob.created_at, CommandJob.id)
                    .first()
                )
                if active is not None and (job is None or active.id != job.id):
                    return self._accepted(active), False
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
                            session_id=session_id, command_id=request.command_id
                        ).one_or_none()
                        if job is None:
                            job = (
                                db.query(CommandJob)
                                .filter(
                                    CommandJob.session_id == session_id,
                                    CommandJob.status.in_(("pending", "processing")),
                                )
                                .order_by(CommandJob.created_at, CommandJob.id)
                                .one()
                            )
                    else:
                        return self._accepted(candidate), True

                if job.command_id == request.command_id and job.input_hash != digest:
                    raise CommandConflict("command_id was used with different input")
                if job.status == "failed":
                    retry = db.execute(
                        update(CommandJob)
                        .where(
                            CommandJob.id == job.id,
                            CommandJob.status == "failed",
                            CommandJob.status_version == job.status_version,
                        )
                        .values(
                            status="pending",
                            status_version=CommandJob.status_version + 1,
                            result=None,
                            error_code=None,
                            error_message=None,
                            started_at=None,
                            completed_at=None,
                            updated_at=_now(),
                        )
                    )
                    db.expire(job)
                    db.refresh(job)
                    should_schedule = retry.rowcount == 1
                return self._accepted(job), should_schedule

    async def run(self, job_id: str) -> None:
        with self._session_factory() as db:
            claimed_version = db.execute(
                update(CommandJob)
                .where(CommandJob.id == job_id, CommandJob.status == "pending")
                .values(
                    status="processing",
                    status_version=CommandJob.status_version + 1,
                    started_at=_now(),
                    progress_stage="starting",
                    progress_message="后台任务拆分已启动。",
                    last_activity_at=_now(),
                    error_code=None,
                    error_message=None,
                )
                .returning(CommandJob.status_version)
            )
            claimed_version = claimed_version.scalar_one_or_none()
            db.commit()
            if claimed_version is None:
                return
            job = db.get(CommandJob, job_id)
            session_id = job.session_id
            request = SessionCommandRequest.model_validate(job.request_payload)

        current_version = claimed_version

        def report_progress(stage: str, message: str) -> None:
            nonlocal current_version
            updated = self.update_progress(
                job_id, current_version, stage=stage, message=message
            )
            if updated is not None:
                current_version = updated

        try:
            with bind_agent_progress(report_progress):
                if request.payload.get("auto_repair"):
                    report_progress(
                        "auto_repair",
                        "自动修复 Agent 正在分析失败上下文并按匹配策略重建任务规划。",
                    )
                result = await self._execute(session_id, request)
        except Exception as error:
            public = classify_workflow_error(error)
            self._finish_failure(job_id, current_version, public.code, public.message)
            return
        self._finish_success(job_id, current_version, result)

    def update_progress(
        self,
        job_id: str,
        expected_version: int,
        *,
        stage: str,
        message: str,
    ) -> int | None:
        """Persist one public-safe progress transition owned by the live runner."""

        now = _now()
        with self._session_factory() as db:
            updated = db.execute(
                update(CommandJob)
                .where(
                    CommandJob.id == job_id,
                    CommandJob.status == "processing",
                    CommandJob.status_version == expected_version,
                )
                .values(
                    status_version=CommandJob.status_version + 1,
                    progress_stage=stage.strip()[:100],
                    progress_message=message.strip()[:1000],
                    last_activity_at=now,
                    updated_at=now,
                )
                .returning(CommandJob.status_version)
            ).scalar_one_or_none()
            db.commit()
            return int(updated) if updated is not None else None

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

    def mark_interrupted_jobs(self) -> int:
        with self._session_factory() as db:
            result = db.execute(
                update(CommandJob)
                .where(CommandJob.status.in_(("pending", "processing")))
                .values(
                    status="failed",
                    status_version=CommandJob.status_version + 1,
                    error_code="PROCESS_INTERRUPTED",
                    error_message=(
                        "The background command process was interrupted; retry the command."
                    ),
                    progress_stage="failed",
                    progress_message=(
                        "后台任务进程已中断，请重新提交拆分。"
                    ),
                    last_activity_at=_now(),
                    completed_at=_now(),
                    updated_at=_now(),
                )
            )
            db.commit()
            return int(result.rowcount or 0)

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
            result=CommandResult.model_validate(job.result) if job.result else None,
            error=(
                CommandJobError(code=job.error_code, message=job.error_message)
                if job.error_code and job.error_message
                else None
            ),
            progress_stage=job.progress_stage,
            progress_message=job.progress_message,
            last_activity_at=job.last_activity_at,
            created_at=job.created_at,
            started_at=job.started_at,
            completed_at=job.completed_at,
        )

    def _finish_success(
        self, job_id: str, processing_status_version: int, result: CommandResult
    ) -> None:
        now = _now()
        with self._session_factory() as db:
            db.execute(
                update(CommandJob)
                .where(
                    CommandJob.id == job_id,
                    CommandJob.status == "processing",
                    CommandJob.status_version == processing_status_version,
                )
                .values(
                    status="succeeded",
                    status_version=CommandJob.status_version + 1,
                    result=result.model_dump(mode="json"),
                    error_code=None,
                    error_message=None,
                    progress_stage="completed",
                    progress_message="后台任务拆分已完成。",
                    last_activity_at=now,
                    completed_at=now,
                    updated_at=now,
                )
            )
            db.commit()

    def _finish_failure(
        self, job_id: str, processing_status_version: int, code: str, message: str
    ) -> None:
        now = _now()
        with self._session_factory() as db:
            db.execute(
                update(CommandJob)
                .where(
                    CommandJob.id == job_id,
                    CommandJob.status == "processing",
                    CommandJob.status_version == processing_status_version,
                )
                .values(
                    status="failed",
                    status_version=CommandJob.status_version + 1,
                    result=None,
                    error_code=code,
                    error_message=message,
                    progress_stage="failed",
                    progress_message=message,
                    last_activity_at=now,
                    completed_at=now,
                    updated_at=now,
                )
            )
            db.commit()
