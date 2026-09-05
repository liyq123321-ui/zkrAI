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


CommandExecutor = Callable[[str, SessionCommandRequest], Awaitable[CommandResult]]
TERMINAL = frozenset({"succeeded", "failed"})
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
                        ).one()
                    else:
                        return self._accepted(candidate), True

                if job.input_hash != digest:
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

        try:
            result = await self._execute(session_id, request)
        except Exception as error:
            public = classify_workflow_error(error)
            self._finish_failure(job_id, claimed_version, public.code, public.message)
            return
        self._finish_success(job_id, claimed_version, result)

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
                    completed_at=_now(),
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
                    completed_at=now,
                    updated_at=now,
                )
            )
            db.commit()
