"""Two-phase, auditable command handling for project workflow transitions."""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Lock
from typing import Awaitable, Callable, ClassVar, Mapping, Protocol
from uuid import uuid4

from sqlalchemy import inspect, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database.models import (
    AgentCall, AgentSpec, AuditEvent, ClarificationRequest, ClarificationResponse, CommandAttempt,
    ProcessedCommand, Project, ReviewTask, SpecReview, SpecVersion, WorkItem,
    WorkItemDependency, WorkItemRun,
)
from app.domain.types import CommandAction, ProjectBriefUpdates, ProjectPhase, ReviewKind, ReviewVerdict, SpecStatus
from app.domain.workflow import IllegalAction, WorkflowSnapshot, assert_action_allowed
from app.schemas.workflow import CommandResult, SessionCommandRequest, SessionState
from app.services.execution_graph import collapse_runs, startable_work_item_ids
from app.services.state_projection import active_clarification_request, project_state


_EXECUTION_ACTIONS = frozenset({
    CommandAction.START_TASK,
    CommandAction.COMPLETE_TASK,
    CommandAction.FAIL_TASK,
})


class CommandConflict(Exception): code = "COMMAND_CONFLICT"
class StaleState(Exception): code = "STALE_STATE"
class ForbiddenActor(Exception): code = "FORBIDDEN_ACTOR"
class CommandInDoubt(RuntimeError): code = "COMMAND_IN_DOUBT"


def assert_reviewer(project: Project, actor_id: str) -> None:
    """Enforce the one persisted responsibility policy for review commands."""

    reviewers = {
        project.final_approver,
        *(project.project_manager_ids or []),
        *(project.root_owner_ids or []),
    }
    if actor_id not in reviewers:
        raise ForbiddenActor(
            f"actor {actor_id!r} is not authorized for human review"
        )


class CommandHandlerFailure(RuntimeError):
    """Preparation failure with references to Agent calls already made durable."""
    def __init__(self, message: str, *, agent_call_ids: list[str] | None = None,
                 audit_payload: Mapping[str, object] | None = None) -> None:
        super().__init__(message)
        self.agent_call_ids = list(agent_call_ids or [])
        self.audit_payload = dict(audit_payload or {})


class CommandHandlerRejected(RuntimeError):
    """A completed Agent response rejected a command without an execution failure."""

    def __init__(self, message: str, *, agent_call_ids: list[str] | None = None,
                 audit_payload: Mapping[str, object] | None = None) -> None:
        super().__init__(message)
        self.agent_call_ids = list(agent_call_ids or [])
        self.audit_payload = dict(audit_payload or {})


@dataclass(frozen=True, slots=True)
class PreparedCommand:
    """Canonical, durable output of external work that is safe to reuse on retry."""
    payload: Mapping[str, object]
    agent_backed: bool = False
    agent_call_ids: list[str] = field(default_factory=list)
    audit_payload: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PrepareContext:
    project_id: str
    session_id: str
    request: SessionCommandRequest
    input_hash: str
    state: SessionState


class ActionScopedUnitOfWork:
    """Narrow materialization capabilities over the short final transaction."""
    def __init__(self, session: Session, project_id: str, action: CommandAction) -> None:
        self.__session, self.__project_id, self.__action = session, project_id, action
        self.__review_task_ids: set[str] = set()

    def add_spec_version(self, version: SpecVersion) -> None:
        if self.__action not in {
            CommandAction.CREATE_SPEC,
            CommandAction.REVISE,
            CommandAction.PUBLISH_REVIEW,
            CommandAction.RESTORE_SPEC_VERSION,
        }:
            raise ValueError("this action cannot create a Spec version")
        if version.project_id != self.__project_id:
            raise ValueError("Spec version belongs to another project")
        self.__session.add(version)

    def add_clarification_response(self, response: ClarificationResponse) -> None:
        if self.__action is not CommandAction.MESSAGE or response.project_id != self.__project_id:
            raise ValueError("clarification response is outside this action scope")
        self.__session.add(response)

    def update_project_brief(self, updates: ProjectBriefUpdates) -> None:
        """Merge clarified content in the same transaction as its answer and state."""
        if self.__action is not CommandAction.MESSAGE:
            raise ValueError("Brief updates are outside this action scope")
        project = self.__session.get(Project, self.__project_id)
        if project is None:
            raise ValueError("Brief project is unavailable")
        project.brief = updates.apply_to(project.brief)

    def add_clarification_request(self, request: ClarificationRequest) -> None:
        if self.__action not in {
            CommandAction.MESSAGE,
            CommandAction.CREATE_SPEC,
            CommandAction.REVISE,
            CommandAction.PUBLISH_REVIEW,
            CommandAction.RESTORE_SPEC_VERSION,
        } or request.project_id != self.__project_id:
            raise ValueError("clarification request is outside this action scope")
        self.__session.add(request)

    def add_spec_review(self, review: SpecReview) -> None:
        if self.__action not in {
            CommandAction.CREATE_SPEC,
            CommandAction.REVISE,
            CommandAction.PUBLISH_REVIEW,
            CommandAction.RESTORE_SPEC_VERSION,
        }:
            raise ValueError("this action cannot create a Spec review")
        if review.project_id != self.__project_id:
            raise ValueError("Spec review belongs to another project")
        self.__session.add(review)

    def add_work_item(self, item: WorkItem) -> None:
        if self.__action is not CommandAction.CONVERT_TO_WORK_ITEM or item.project_id != self.__project_id:
            raise ValueError("WorkItem is outside this action scope")
        self.__session.add(item)

    def add_work_item_dependency(self, edge: WorkItemDependency) -> None:
        if self.__action is not CommandAction.CONVERT_TO_WORK_ITEM or edge.project_id != self.__project_id:
            raise ValueError("WorkItem dependency is outside this action scope")
        self.__session.add(edge)

    def add_agent_spec(self, spec: AgentSpec) -> None:
        if self.__action is not CommandAction.CONVERT_TO_WORK_ITEM or spec.project_id != self.__project_id:
            raise ValueError("AgentSpec is outside this action scope")
        self.__session.add(spec)

    def add_work_item_run(self, run: WorkItemRun) -> None:
        """Append an execution ledger entry.

        Execution state is append-only on purpose: ``WorkItem`` rows stay
        immutable after decomposition, which lets the command guard admit new
        rows without opening a mutation exemption for existing ones.
        """

        if self.__action not in _EXECUTION_ACTIONS:
            raise ValueError("this action cannot record a work item run")
        if run.project_id != self.__project_id:
            raise ValueError("WorkItem run belongs to another project")
        self.__session.add(run)

    def work_item_execution_snapshot(
        self, work_item_id: str
    ) -> tuple[bool, str, dict[str, list[str]], set[str]]:
        """Read-only execution facts for one work item, inside the transaction.

        Returns ``(executable, status, dependencies, startable_ids)`` so the
        materialize step can re-validate against the same data the projection
        used, closing the gap between what the UI was offered and what the
        engine accepts.
        """

        if self.__action not in _EXECUTION_ACTIONS:
            raise ValueError("this action cannot inspect execution state")
        item = self.__session.get(WorkItem, work_item_id)
        if item is None or item.project_id != self.__project_id:
            raise ValueError("WorkItem is outside this action scope")
        items = (
            self.__session.query(WorkItem)
            .filter(WorkItem.project_id == self.__project_id)
            .all()
        )
        runs = (
            self.__session.query(WorkItemRun)
            .filter(WorkItemRun.project_id == self.__project_id)
            .order_by(
                WorkItemRun.work_item_id,
                WorkItemRun.created_at,
                WorkItemRun.id,
            )
            .all()
        )
        status_by_item = collapse_runs(runs)
        dependencies = {
            edge.from_work_item_id: edge.to_work_item_id
            for edge in self.__session.query(WorkItemDependency)
            .filter(WorkItemDependency.project_id == self.__project_id)
            .all()
        }
        edges: dict[str, list[str]] = {}
        for from_id, to_id in dependencies.items():
            edges.setdefault(from_id, []).append(to_id)
        executable_ids = {
            row.id for row in items if row.executable and row.id is not None
        }
        return (
            bool(item.executable),
            status_by_item.get(work_item_id, "todo"),
            edges,
            set(startable_work_item_ids(executable_ids, edges, status_by_item)),
        )

    def existing_work_item_keys(self, local_keys: set[str]) -> set[str]:
        if self.__action is not CommandAction.CONVERT_TO_WORK_ITEM:
            raise ValueError("this action cannot inspect WorkItem keys")
        if not local_keys:
            return set()
        return {
            row[0]
            for row in self.__session.query(WorkItem.local_key)
            .filter(WorkItem.project_id == self.__project_id, WorkItem.local_key.in_(local_keys))
            .all()
        }

    def assert_decomposition_spec_snapshot(
        self, spec_id: str, content_hash: str, content: Mapping[str, object], input_refs: tuple[str, ...],
    ) -> None:
        """Check the reviewed PRD inside the same transaction that creates its tasks."""
        if self.__action is not CommandAction.CONVERT_TO_WORK_ITEM:
            raise ValueError("this action cannot validate a decomposition snapshot")
        project = self.__session.get(Project, self.__project_id)
        version = self.__session.get(SpecVersion, spec_id)
        if (project is None or project.current_spec_version_id != spec_id
                or project.phase != ProjectPhase.REVIEW.value
                or version is None or version.project_id != self.__project_id
                or version.status != SpecStatus.APPROVED.value
                or version.content_hash != content_hash or version.content != content
                or tuple(version.input_refs) != input_refs):
            raise ValueError("approved Spec changed before decomposition materialization")

    def mark_decomposition_call_succeeded(self, call_id: str) -> None:
        if self.__action is not CommandAction.CONVERT_TO_WORK_ITEM:
            raise ValueError("this action cannot complete an Agent call")
        call = self.__session.get(AgentCall, call_id)
        if call is None or call.project_id != self.__project_id or call.status != "RESULT_READY":
            raise ValueError("decomposition Agent call is not ready to complete")
        if call.operation != "decompose_spec":
            raise ValueError("Agent call operation does not match this materialization")
        call.status, call.completed_at = "SUCCEEDED", _now()

    def mark_breakdown_reviewer_call_succeeded(self, call_id: str) -> None:
        """Complete only the reviewer evidence produced by breakdown review."""
        if self.__action is not CommandAction.CONVERT_TO_WORK_ITEM:
            raise ValueError("this action cannot complete an Agent call")
        call = self.__session.get(AgentCall, call_id)
        if call is None or call.project_id != self.__project_id or call.status != "RESULT_READY":
            raise ValueError("breakdown Reviewer Agent call is not ready to complete")
        if call.operation != "review_breakdown":
            raise ValueError("Agent call operation does not match this materialization")
        call.status, call.completed_at = "SUCCEEDED", _now()

    def mark_task_plan_call_succeeded(self, call_id: str) -> None:
        """Complete one task-plan call adopted by decomposition materialization."""
        if self.__action is not CommandAction.CONVERT_TO_WORK_ITEM:
            raise ValueError("this action cannot complete an Agent call")
        call = self.__session.get(AgentCall, call_id)
        if call is None or call.project_id != self.__project_id or call.status != "RESULT_READY":
            raise ValueError("task-plan Agent call is not ready to complete")
        if call.operation != "plan_task":
            raise ValueError("Agent call operation does not match this materialization")
        call.status, call.completed_at = "SUCCEEDED", _now()

    def mark_analysis_call_succeeded(self, call_id: str) -> None:
        self.__mark_staged_call_succeeded(
            call_id, "analyze_brief", {CommandAction.MESSAGE}
        )

    def mark_spec_generation_call_succeeded(self, call_id: str) -> None:
        operation = (
            "rewrite_prd"
            if self.__action is CommandAction.PUBLISH_REVIEW
            else "restore_spec"
            if self.__action is CommandAction.RESTORE_SPEC_VERSION
            else "generate_spec"
        )
        self.__mark_staged_call_succeeded(
            call_id,
            operation,
            {
                CommandAction.CREATE_SPEC,
                CommandAction.REVISE,
                CommandAction.PUBLISH_REVIEW,
                CommandAction.RESTORE_SPEC_VERSION,
            },
        )

    def mark_spec_reviewer_call_succeeded(self, call_id: str) -> None:
        self.__mark_staged_call_succeeded(
            call_id,
            "review_spec",
            {
                CommandAction.CREATE_SPEC,
                CommandAction.REVISE,
                CommandAction.PUBLISH_REVIEW,
                CommandAction.RESTORE_SPEC_VERSION,
            },
        )

    def set_review_task_revision(
        self, task_id: str, spec_version_id: str, revision: int
    ) -> None:
        """Bind one review task to the revision created by this publication."""

        if self.__action is not CommandAction.PUBLISH_REVIEW:
            raise ValueError("this action cannot update a review task")
        task = self.__session.get(ReviewTask, task_id)
        item = self.__session.get(WorkItem, task.wi) if task is not None else None
        version = next(
            (
                candidate
                for candidate in self.__session.new
                if isinstance(candidate, SpecVersion)
                and candidate.id == spec_version_id
            ),
            None,
        )
        if version is None:
            version = self.__session.get(SpecVersion, spec_version_id)
        if (
            task is None
            or item is None
            or item.project_id != self.__project_id
            or version is None
            or version.project_id != self.__project_id
            or version.revision != revision
            or revision <= 0
            or task.new_spec_version_id is not None
            or task.new_version is not None
        ):
            raise ValueError("review task is outside this action scope")
        task.new_spec_version_id = spec_version_id
        task.new_version = revision
        self.__review_task_ids.add(task.id)

    @property
    def _review_task_ids(self) -> frozenset[str]:
        return frozenset(self.__review_task_ids)

    def __mark_staged_call_succeeded(
        self, call_id: str, operation: str, allowed_actions: set[CommandAction]
    ) -> None:
        if self.__action not in allowed_actions:
            raise ValueError("this action cannot complete this Agent call")
        call = self.__session.get(AgentCall, call_id)
        if (
            call is None
            or call.project_id != self.__project_id
            or call.status != "RESULT_READY"
            or call.operation != operation
        ):
            raise ValueError("Agent call is not ready for this materialization")
        call.status, call.completed_at = "SUCCEEDED", _now()


@dataclass(frozen=True, slots=True)
class MaterializeContext:
    project_id: str
    session_id: str
    request: SessionCommandRequest
    state: SessionState
    reserved_state_version: int


@dataclass(frozen=True, slots=True)
class CommandHandlerResult:
    phase: ProjectPhase | None = None
    current_spec_version_id: str | None = None
    spec_status: SpecStatus | None = None
    created_resource_ids: list[str] = field(default_factory=list)
    side_effect_refs: list[str] = field(default_factory=list)
    audit_payload: Mapping[str, object] = field(default_factory=dict)


class TwoPhaseCommandHandler(Protocol):
    async def prepare(self, context: PrepareContext) -> PreparedCommand: ...
    def materialize(self, uow: ActionScopedUnitOfWork, context: MaterializeContext,
                    prepared: PreparedCommand) -> CommandHandlerResult: ...


class CallableTwoPhaseHandler:
    """Small adapter for staged production handlers and integration tests."""
    def __init__(self, prepare: Callable[[PrepareContext], Awaitable[PreparedCommand]],
                 materialize: Callable[[ActionScopedUnitOfWork, MaterializeContext, PreparedCommand], CommandHandlerResult]) -> None:
        self._prepare, self._materialize = prepare, materialize
    async def prepare(self, context: PrepareContext) -> PreparedCommand:
        return await self._prepare(context)
    def materialize(self, uow: ActionScopedUnitOfWork, context: MaterializeContext,
                    prepared: PreparedCommand) -> CommandHandlerResult:
        return self._materialize(uow, context, prepared)


@dataclass(slots=True)
class _Failure:
    project_id: str | None
    session_id: str
    request: SessionCommandRequest
    agent_call_ids: list[str] = field(default_factory=list)
    audit_payload: dict[str, object] = field(default_factory=dict)


def _new_id() -> str: return str(uuid4())
def _now() -> datetime: return datetime.now(UTC)
def _canonical_hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
def _canonical_json(value: Mapping[str, object]) -> dict[str, object]:
    return json.loads(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False))


class CommandService:
    _HUMAN = frozenset({CommandAction.APPROVE, CommandAction.REJECT, CommandAction.REWORK})
    _RESERVED_AUDIT = frozenset({"command_id", "action", "prior_state_version", "new_state_version", "agent_call_ids"})
    _REVIEW = frozenset({SpecStatus.AUTO_REVIEW, SpecStatus.HUMAN_REVIEW, SpecStatus.REWORK, SpecStatus.NEED_CLARIFICATION})
    # Preparation leases are process-local by design.  The deployment is
    # explicitly single-process; durable owner IDs make abandoned leases
    # diagnosable while AgentCall checkpoints determine safe recovery.
    _ACTIVE_PREPARATIONS: ClassVar[dict[str, str]] = {}
    _ACTIVE_PREPARATIONS_LOCK: ClassVar[Lock] = Lock()

    def __init__(self, session_factory: Callable[[], Session], *,
                 handlers: Mapping[CommandAction, TwoPhaseCommandHandler] | None = None) -> None:
        self._session_factory, self._handlers = session_factory, dict(handlers or {})

    async def execute(self, session_id: str, request: SessionCommandRequest) -> CommandResult:
        input_hash = _canonical_hash(request.model_dump(mode="json"))
        replay = self._receipt_for(session_id, request.command_id, input_hash)
        if replay is not None: return replay
        try:
            if request.action in self._HUMAN:
                return self._human(session_id, request, input_hash)
            if request.action is CommandAction.SKIP_CLARIFICATION:
                return self._skip_clarification(session_id, request, input_hash)
            attempt, prepare_context = self._claim_or_load_attempt(
                session_id, request, input_hash
            )
            if attempt.status == "PREPARING":
                await self._prepare_attempt(
                    attempt.id, prepare_context, attempt.prepare_owner_id
                )
            return self._materialize_attempt(attempt.id, session_id, request, input_hash)
        except StaleState:
            self._before_stale_replay(session_id, request.command_id)
            replay = self._receipt_for(session_id, request.command_id, input_hash)
            if replay is not None:
                return replay
            raise

    def _claim_or_load_attempt(self, session_id: str, request: SessionCommandRequest, input_hash: str) -> tuple[CommandAttempt, PrepareContext]:
        owner_id = _new_id()
        registered_attempt_id: str | None = None
        try:
            with self._session_factory() as db:
                with db.begin():
                    project = self._project(db, session_id)
                    existing = db.query(CommandAttempt).filter_by(session_id=session_id, command_id=request.command_id).one_or_none()
                    if existing is not None:
                        if existing.input_hash != input_hash: raise CommandConflict("command_id was used with different input")
                        if existing.status == "PREPARING":
                            if self._preparation_is_active(existing.id):
                                raise CommandInDoubt("command preparation has a live owner")
                            self._assert_recoverable_preparing(
                                db, existing, request, input_hash
                            )
                            self._assert_current_command(db, project, request)
                            existing.prepare_owner_id = owner_id
                            existing.prepare_owner_started_at = _now()
                            existing.error = None
                            self._register_preparation(existing.id, owner_id)
                            registered_attempt_id = existing.id
                        if existing.status in {"FAILED", "REJECTED"}:
                            self._assert_current_command(db, project, request)
                            existing.status, existing.error, existing.prepared_payload, existing.agent_call_ids = "PREPARING", None, None, []
                            existing.prepare_owner_id = owner_id
                            existing.prepare_owner_started_at = _now()
                            self._register_preparation(existing.id, owner_id)
                            registered_attempt_id = existing.id
                        attempt = existing
                    else:
                        self._assert_current_command(db, project, request)
                        attempt = CommandAttempt(id=_new_id(), project_id=project.id, session_id=session_id,
                            command_id=request.command_id, input_hash=input_hash, expected_state_version=request.expected_state_version,
                            action=request.action.value, status="PREPARING", prepare_owner_id=owner_id,
                            prepare_owner_started_at=_now())
                        try:
                            self._before_initial_attempt_insert(session_id, request.command_id)
                            with db.begin_nested():
                                db.add(attempt)
                                db.flush()
                            self._register_preparation(attempt.id, owner_id)
                            registered_attempt_id = attempt.id
                        except IntegrityError:
                            attempt = db.query(CommandAttempt).filter_by(session_id=session_id, command_id=request.command_id).one()
                            if attempt.input_hash != input_hash:
                                raise CommandConflict("command_id was used with different input")
                            if attempt.status == "PREPARING":
                                if self._preparation_is_active(attempt.id):
                                    raise CommandInDoubt("command preparation has a live owner")
                                self._assert_recoverable_preparing(
                                    db, attempt, request, input_hash
                                )
                                attempt.prepare_owner_id = owner_id
                                attempt.prepare_owner_started_at = _now()
                                attempt.error = None
                                self._register_preparation(attempt.id, owner_id)
                                registered_attempt_id = attempt.id
                            if attempt.status == "MATERIALIZED":
                                receipt = self._receipt(db, session_id, request.command_id, input_hash)
                                if receipt is not None:
                                    return attempt, PrepareContext(project.id, session_id, request, input_hash, self._state(db, project))
                                raise CommandInDoubt("command materialization is in doubt")
                    context = PrepareContext(project.id, session_id, request, input_hash, self._state(db, project))
                return attempt, context
        except BaseException:
            if registered_attempt_id is not None:
                self._release_preparation(registered_attempt_id, owner_id)
            raise

    async def _prepare_attempt(self, attempt_id: str, context: PrepareContext,
                               owner_id: str | None = None) -> None:
        owner_id = owner_id or self._active_preparation_owner(attempt_id)
        try:
            try:
                context = self._revalidate_before_prepare(attempt_id, context, owner_id)
            except (StaleState, IllegalAction) as error:
                self._fail_attempt(attempt_id, _Failure(context.project_id, context.session_id, context.request), error)
                raise
            handler = self._handlers.get(context.request.action)
            if handler is None:
                self._fail_attempt(attempt_id, _Failure(context.project_id, context.session_id, context.request), RuntimeError("no handler registered"))
                raise RuntimeError(f"no handler registered for legal action {context.request.action.value!r}")
            try:
                prepared = await handler.prepare(context)
                self._validate_prepared_evidence(context.project_id, context.request, context.input_hash, prepared, success=True)
            except CommandHandlerRejected as error:
                rejection = _Failure(context.project_id, context.session_id, context.request,
                                     list(error.agent_call_ids), dict(error.audit_payload))
                self._validate_agent_refs(context.project_id, context.request, context.input_hash, rejection.agent_call_ids, success=True)
                self._reject_attempt(attempt_id, rejection, error)
                raise
            except Exception as error:
                failure = _Failure(context.project_id, context.session_id, context.request)
                if isinstance(error, CommandHandlerFailure):
                    failure.agent_call_ids, failure.audit_payload = error.agent_call_ids, error.audit_payload
                    self._validate_failure_evidence(context.project_id, context.request, context.input_hash, failure.agent_call_ids)
                self._fail_attempt(attempt_id, failure, error)
                raise
            with self._session_factory() as db:
                with db.begin():
                    attempt = db.get(CommandAttempt, attempt_id)
                    if (
                        attempt is None
                        or attempt.status != "PREPARING"
                        or owner_id is None
                        or attempt.prepare_owner_id != owner_id
                    ):
                        raise CommandInDoubt("command attempt changed while preparing")
                    attempt.status, attempt.prepared_payload, attempt.agent_call_ids = "PREPARED", _canonical_json({
                        "payload": dict(prepared.payload), "agent_backed": prepared.agent_backed,
                        "audit_payload": dict(prepared.audit_payload),
                    }), list(prepared.agent_call_ids)
                    attempt.prepared_at = _now()
                    attempt.prepare_owner_id = None
                    attempt.prepare_owner_started_at = None
        finally:
            if owner_id is not None:
                self._release_preparation(attempt_id, owner_id)

    def _revalidate_before_prepare(self, attempt_id: str, context: PrepareContext,
                                   owner_id: str | None = None) -> PrepareContext:
        """Close the claim-to-external-work race without holding a write lock across await."""

        with self._session_factory() as db:
            with db.begin():
                attempt = db.get(CommandAttempt, attempt_id)
                owner_id = owner_id or self._active_preparation_owner(attempt_id)
                if (
                    attempt is None
                    or attempt.status != "PREPARING"
                    or owner_id is None
                    or attempt.prepare_owner_id != owner_id
                    or self._active_preparation_owner(attempt_id) != owner_id
                ):
                    raise CommandInDoubt("command attempt is no longer available for preparation")
                project = self._project(db, context.session_id)
                self._assert_current_command(db, project, context.request)
                return PrepareContext(project.id, context.session_id, context.request, context.input_hash, self._state(db, project))

    def _materialize_attempt(self, attempt_id: str, session_id: str, request: SessionCommandRequest, input_hash: str) -> CommandResult:
        failure = _Failure(None, session_id, request)
        try:
            with self._session_factory() as db:
                with db.begin():
                    receipt = self._receipt(db, session_id, request.command_id, input_hash)
                    if receipt is not None: return receipt
                    attempt = db.get(CommandAttempt, attempt_id)
                    if attempt is None or attempt.status != "PREPARED": raise CommandInDoubt("prepared command attempt is unavailable")
                    if attempt.input_hash != input_hash: raise CommandConflict("command attempt input conflict")
                    project = self._project(db, session_id); failure.project_id = project.id
                    failure.agent_call_ids = list(attempt.agent_call_ids or [])
                    prepared = self._prepared_from_attempt(attempt)
                    self._validate_prepared_evidence(project.id, request, input_hash, prepared, success=True)
                    if project.state_version != request.expected_state_version: raise StaleState("expected state version is no longer current")
                    self._assert_legal(db, project, request.action)
                    before = self._state(db, project)
                    snapshot = WorkflowSnapshot(before.phase, before.current_spec_status)
                    self._before_materialization_cas(session_id, request.command_id)
                    self._advance_project(db, project, request.expected_state_version); db.refresh(project)
                    reserved = project.state_version
                    guard = self._guard(db, project)
                    handler = self._handlers[request.action]
                    uow = ActionScopedUnitOfWork(db, project.id, request.action)
                    result = handler.materialize(
                        uow,
                        MaterializeContext(
                            project.id, session_id, request, before, reserved
                        ),
                        prepared,
                    )
                    self._enforce_guard(
                        db,
                        project,
                        guard,
                        reserved,
                        set(prepared.agent_call_ids),
                        uow._review_task_ids,
                    )
                    db.flush()
                    self._validate_result(db, project, request.action, snapshot, result)
                    self._validate_audit(prepared.audit_payload)
                    self._validate_audit(result.audit_payload)
                    self._apply(db, project, result); db.flush()
                    state = self._state(db, project)
                    output = CommandResult(command_id=request.command_id, state=state, created_resource_ids=list(result.created_resource_ids))
                    db.add(ProcessedCommand(id=_new_id(), session_id=session_id, command_id=request.command_id,
                        input_hash=input_hash, state_version=state.state_version, result=output.model_dump(mode="json"),
                        side_effect_refs=[*result.side_effect_refs, *(f"agent_call:{x}" for x in prepared.agent_call_ids)]))
                    db.add(AuditEvent(id=_new_id(), project_id=project.id, session_id=session_id, event_type="COMMAND_APPLIED",
                        actor_id=request.actor_id, payload={**dict(prepared.audit_payload), **dict(result.audit_payload),
                        "command_id":request.command_id,"action":request.action.value,"prior_state_version":request.expected_state_version,
                        "new_state_version":state.state_version,"agent_call_ids":list(prepared.agent_call_ids)}))
                    attempt.status = "MATERIALIZED"
                return output
        except StaleState as error:
            self._record_failure(failure, error, "COMMAND_STALE_STATE")
            raise
        except (CommandConflict, IllegalAction, ForbiddenActor, CommandInDoubt): raise
        except Exception as error:
            self._record_failure(failure, error, "COMMAND_HANDLER_FAILED")
            raise

    # Human approvals remain a one-step local transaction.
    def _human(self, session_id: str, request: SessionCommandRequest, input_hash: str) -> CommandResult:
        with self._session_factory() as db:
            with db.begin():
                receipt=self._receipt(db,session_id,request.command_id,input_hash)
                if receipt:return receipt
                project=self._project(db,session_id); version=self._current_spec(db,project)
                if project.state_version != request.expected_state_version: raise StaleState("expected state version is no longer current")
                self._assert_legal(db,project,request.action); self._assert_reviewer(project,request.actor_id)
                if request.action is CommandAction.REWORK and not self._comments(request): raise ValueError("comments are required for rework")
                if version is None: raise IllegalAction(request.action,())
                if (
                    request.action is CommandAction.APPROVE
                    and version.status == SpecStatus.REWORK.value
                    and not self._comments(request)
                ):
                    raise ValueError("findings acceptance comments are required")
                self._advance_project(db,project,request.expected_state_version); db.refresh(project)
                review=SpecReview(id=_new_id(),project_id=project.id,spec_version_id=version.id,kind=ReviewKind.HUMAN.value,
                    reviewer_id=request.actor_id,input_spec_hash=version.content_hash,verdict=(ReviewVerdict.PASS if request.action is CommandAction.APPROVE else ReviewVerdict.REJECT).value,
                    findings=[],comments=self._comments(request),command_id=request.command_id)
                version.status={CommandAction.APPROVE:SpecStatus.APPROVED,CommandAction.REWORK:SpecStatus.REWORK,CommandAction.REJECT:SpecStatus.REJECTED}[request.action].value
                db.add(review);db.flush();state=self._state(db,project);out=CommandResult(command_id=request.command_id,state=state,created_resource_ids=[review.id])
                db.add(ProcessedCommand(id=_new_id(),session_id=session_id,command_id=request.command_id,input_hash=input_hash,state_version=state.state_version,result=out.model_dump(mode="json"),side_effect_refs=[f"spec_review:{review.id}"]))
                db.add(AuditEvent(id=_new_id(),project_id=project.id,session_id=session_id,event_type={CommandAction.APPROVE:"SPEC_HUMAN_APPROVED",CommandAction.REWORK:"SPEC_HUMAN_REWORKED",CommandAction.REJECT:"SPEC_HUMAN_REJECTED"}[request.action],actor_id=request.actor_id,payload={"command_id":request.command_id,"spec_version_id":version.id,"spec_review_id":review.id,"prior_state_version":request.expected_state_version,"new_state_version":state.state_version,"comments":self._comments(request)}))
            return out
    _execute_human_review = _human

    def _skip_clarification(
        self,
        session_id: str,
        request: SessionCommandRequest,
        input_hash: str,
    ) -> CommandResult:
        """Record explicit ambiguity acceptance and advance without another PM loop."""

        with self._session_factory() as db:
            with db.begin():
                receipt = self._receipt(
                    db, session_id, request.command_id, input_hash
                )
                if receipt:
                    return receipt
                project = self._project(db, session_id)
                if project.state_version != request.expected_state_version:
                    raise StaleState("expected state version is no longer current")
                self._assert_legal(db, project, request.action)
                self._assert_reviewer(project, request.actor_id)
                clarification = active_clarification_request(db, project)
                if clarification is None:
                    raise IllegalAction(request.action, ())

                version = self._current_spec(db, project)
                if version is not None:
                    if request.payload.get("confirm_current_spec") is not True:
                        raise ValueError("skipping review clarification requires explicit PRD confirmation")
                    if request.payload.get("spec_version_id") != version.id:
                        raise StaleState("the confirmed PRD is no longer the current version")

                self._advance_project(db, project, request.expected_state_version)
                db.refresh(project)
                response = ClarificationResponse(
                    id=_new_id(),
                    project_id=project.id,
                    clarification_request_id=clarification.id,
                    response_slot="PRIMARY",
                    actor_id=request.actor_id,
                    answers={
                        "message": (
                            "The user explicitly skipped clarification and accepted "
                            "reasonable Agent assumptions for human PRD review."
                        ),
                        "decision": "SKIP_CLARIFICATION",
                        "assumption_policy": "AGENT_DISCRETION",
                    },
                )
                created_ids = [response.id]
                side_effect_refs = [f"clarification_response:{response.id}"]
                approval_payload: dict[str, object] = {}
                if version is None:
                    project.phase = ProjectPhase.SPECIFICATION.value
                else:
                    comments = "用户跳过当前澄清，接受未决项并确认当前 PRD，继续拆分子任务。"
                    response.answers = {
                        **response.answers,
                        "message": comments,
                        "approved_spec_version_id": version.id,
                    }
                    review = SpecReview(
                        id=_new_id(), project_id=project.id, spec_version_id=version.id,
                        kind=ReviewKind.HUMAN.value, reviewer_id=request.actor_id,
                        input_spec_hash=version.content_hash, verdict=ReviewVerdict.PASS.value,
                        findings=[], comments=comments, command_id=request.command_id,
                    )
                    db.add(review)
                    version.status = SpecStatus.APPROVED.value
                    project.phase = ProjectPhase.REVIEW.value
                    created_ids.append(review.id)
                    side_effect_refs.append(f"spec_review:{review.id}")
                    approval_payload = {
                        "spec_version_id": version.id,
                        "spec_review_id": review.id,
                        "decision": "SKIP_CLARIFICATION_AND_APPROVE",
                    }
                    db.add(AuditEvent(
                        id=_new_id(), project_id=project.id, session_id=session_id,
                        event_type="SPEC_HUMAN_APPROVED", actor_id=request.actor_id,
                        payload={
                            **approval_payload,
                            "command_id": request.command_id,
                            "clarification_request_id": clarification.id,
                            "clarification_response_id": response.id,
                            "prior_state_version": request.expected_state_version,
                            "new_state_version": project.state_version,
                            "comments": comments,
                        },
                    ))
                db.add(response)
                db.flush()
                state = self._state(db, project)
                output = CommandResult(
                    command_id=request.command_id,
                    state=state,
                    created_resource_ids=created_ids,
                )
                db.add(
                    ProcessedCommand(
                        id=_new_id(),
                        session_id=session_id,
                        command_id=request.command_id,
                        input_hash=input_hash,
                        state_version=state.state_version,
                        result=output.model_dump(mode="json"),
                        side_effect_refs=side_effect_refs,
                    )
                )
                db.add(
                    AuditEvent(
                        id=_new_id(),
                        project_id=project.id,
                        session_id=session_id,
                        event_type="CLARIFICATION_SKIPPED",
                        actor_id=request.actor_id,
                        payload={
                            **approval_payload,
                            "command_id": request.command_id,
                            "clarification_request_id": clarification.id,
                            "clarification_response_id": response.id,
                            "prior_state_version": request.expected_state_version,
                            "new_state_version": state.state_version,
                            "assumption_policy": "AGENT_DISCRETION",
                        },
                    )
                )
            return output

    def _validate_prepared_evidence(self, project_id: str, request: SessionCommandRequest, input_hash: str, prepared: PreparedCommand, *, success: bool) -> None:
        if prepared.agent_backed and not prepared.agent_call_ids: raise ValueError("agent-backed preparation requires AgentCall references")
        self._validate_agent_refs(project_id,request,input_hash,prepared.agent_call_ids,success=success)

    def _validate_failure_evidence(self, project_id: str, request: SessionCommandRequest, input_hash: str, ids: list[str]) -> None:
        self._validate_agent_refs(project_id,request,input_hash,ids,success=False)

    def _validate_agent_refs(self, project_id: str, request: SessionCommandRequest, input_hash: str, ids: list[str], *, success: bool) -> None:
        if not ids:return
        allowed={"SUCCEEDED","RESULT_READY","NO_CHANGE","COMPLETED"} if success else {"FAILED","PENDING","AMBIGUOUS","RESULT_READY"}
        with self._session_factory() as db:
            for call_id in ids:
                call=db.get(AgentCall,call_id)
                if call is None or call.project_id != project_id: raise ValueError("AgentCall reference is missing or cross-project")
                if call.request.get("command_id") != request.command_id or call.request.get("input_hash") != input_hash: raise ValueError("AgentCall is not bound to this command input")
                if call.status not in allowed: raise ValueError("AgentCall status is incompatible with command outcome")

    def _fail_attempt(self, attempt_id: str, failure: _Failure, error: Exception) -> None:
        with self._session_factory() as db:
            attempt=db.get(CommandAttempt,attempt_id)
            if attempt is not None:
                attempt.status, attempt.error, attempt.agent_call_ids = "FAILED", str(error), list(failure.agent_call_ids)
                attempt.prepare_owner_id = None
                attempt.prepare_owner_started_at = None
            db.commit()
        self._record_failure(failure,error,"COMMAND_HANDLER_FAILED")

    def _reject_attempt(self, attempt_id: str, rejection: _Failure, error: Exception) -> None:
        with self._session_factory() as db:
            attempt = db.get(CommandAttempt, attempt_id)
            if attempt is not None:
                attempt.status, attempt.error, attempt.agent_call_ids = "REJECTED", str(error), list(rejection.agent_call_ids)
                attempt.prepare_owner_id = None
                attempt.prepare_owner_started_at = None
            db.commit()
        self._record_failure(rejection, error, "COMMAND_HANDLER_REJECTED")

    def _record_failure(self, failure: _Failure, error: Exception, event_type: str) -> None:
        if failure.project_id is None:return
        payload={**failure.audit_payload,"command_id":failure.request.command_id,"action":failure.request.action.value,
                 "expected_state_version":failure.request.expected_state_version,"error_code":event_type,
                 "message":"The workflow command did not complete.","agent_call_ids":list(failure.agent_call_ids)}
        with self._session_factory() as db:
            db.add(AuditEvent(id=_new_id(),project_id=failure.project_id,session_id=failure.session_id,event_type=event_type,actor_id=failure.request.actor_id,payload=payload));db.commit()

    @classmethod
    def _register_preparation(cls, attempt_id: str, owner_id: str) -> None:
        with cls._ACTIVE_PREPARATIONS_LOCK:
            active = cls._ACTIVE_PREPARATIONS.get(attempt_id)
            if active is not None and active != owner_id:
                raise CommandInDoubt("command preparation has a live owner")
            cls._ACTIVE_PREPARATIONS[attempt_id] = owner_id

    @classmethod
    def _release_preparation(cls, attempt_id: str, owner_id: str) -> None:
        with cls._ACTIVE_PREPARATIONS_LOCK:
            if cls._ACTIVE_PREPARATIONS.get(attempt_id) == owner_id:
                cls._ACTIVE_PREPARATIONS.pop(attempt_id, None)

    @classmethod
    def _active_preparation_owner(cls, attempt_id: str) -> str | None:
        with cls._ACTIVE_PREPARATIONS_LOCK:
            return cls._ACTIVE_PREPARATIONS.get(attempt_id)

    @classmethod
    def _preparation_is_active(cls, attempt_id: str) -> bool:
        return cls._active_preparation_owner(attempt_id) is not None

    @staticmethod
    def _assert_recoverable_preparing(
        db: Session,
        attempt: CommandAttempt,
        request: SessionCommandRequest,
        input_hash: str,
    ) -> None:
        bound_calls = [
            call
            for call in db.query(AgentCall).filter_by(project_id=attempt.project_id).all()
            if isinstance(call.request, dict)
            and call.request.get("command_id") == request.command_id
            and call.request.get("input_hash") == input_hash
        ]
        unresolved = [
            call.id for call in bound_calls if call.status in {"PENDING", "AMBIGUOUS"}
        ]
        if unresolved:
            attempt.agent_call_ids = unresolved
            attempt.error = (
                "PENDING AgentCall ownership is unresolved: " + ", ".join(unresolved)
            )
            # This method is called within the claim transaction.  Committing
            # the diagnostic before raising keeps the recovery state durable.
            db.flush()
            db.commit()
            raise CommandInDoubt("command preparation has unresolved external work")

    @staticmethod
    def _prepared_from_attempt(attempt: CommandAttempt) -> PreparedCommand:
        value=attempt.prepared_payload or {}
        return PreparedCommand(payload=value.get("payload",{}),agent_backed=bool(value.get("agent_backed",False)),agent_call_ids=list(attempt.agent_call_ids or []),audit_payload=value.get("audit_payload",{}))

    def _guard(self, db: Session, project: Project) -> tuple[tuple[object,...], str | None, set[object]]:
        version=self._current_spec(db,project)
        return ((project.phase,project.current_spec_version_id,project.final_approver,tuple(project.project_manager_ids or []),tuple(project.root_owner_ids or [])),version.status if version else None,set(db.new))

    def _enforce_guard(self, db: Session, project: Project, guard: tuple[tuple[object,...],str|None,set[object]], reserved: int, allowed_agent_call_ids: set[str], allowed_review_task_ids: frozenset[str] = frozenset()) -> None:
        project_guard,spec_status,old_new=guard; version=self._current_spec(db,project)
        if project.state_version != reserved: raise ValueError("handler must not change Project.state_version")
        if (project.phase,project.current_spec_version_id,project.final_approver,tuple(project.project_manager_ids or []),tuple(project.root_owner_ids or [])) != project_guard: raise ValueError("handler must not directly mutate protected Project fields")
        if (version.status if version else None) != spec_status: raise ValueError("handler must not mutate an existing Spec")
        allowed=(SpecVersion,SpecReview,ClarificationRequest,ClarificationResponse,WorkItem,WorkItemDependency,AgentSpec,WorkItemRun)
        added=set(db.new)-old_new
        if any(not isinstance(item,allowed) for item in added): raise ValueError("handler created an unauthorized row")
        if any(getattr(item, "project_id", project.id) != project.id for item in added):
            raise ValueError("handler created a row for another project")
        for item in db.dirty:
            if item is project or item in added:
                continue
            if isinstance(item, AgentCall) and item.id in allowed_agent_call_ids:
                state = inspect(item)
                changed = {attribute.key for attribute in state.attrs if attribute.history.has_changes()}
                previous_status = state.attrs.status.history.deleted
                if changed <= {"status", "completed_at"} and previous_status == ["RESULT_READY"] and item.status == "SUCCEEDED":
                    continue
            if isinstance(item, ReviewTask) and item.id in allowed_review_task_ids:
                state = inspect(item)
                changed = {
                    attribute.key
                    for attribute in state.attrs
                    if attribute.history.has_changes()
                }
                old_spec = state.attrs.new_spec_version_id.history.deleted
                old_revision = state.attrs.new_version.history.deleted
                work_item = db.get(WorkItem, item.wi)
                if (
                    changed <= {"new_spec_version_id", "new_version"}
                    and old_spec == [None]
                    and old_revision == [None]
                    and item.new_spec_version_id is not None
                    and item.new_version is not None
                    and work_item is not None
                    and work_item.project_id == project.id
                ):
                    continue
            raise ValueError("handler modified an existing protected row")

    def _validate_result(self, db: Session, project: Project, action: CommandAction, before: WorkflowSnapshot, result: CommandHandlerResult) -> None:
        if result.spec_status is SpecStatus.APPROVED: raise ValueError("handler may not set APPROVED")
        version_id=result.current_spec_version_id or project.current_spec_version_id; version=db.get(SpecVersion,version_id) if version_id else None
        if result.current_spec_version_id and (version is None or version.project_id != project.id): raise ValueError("handler selected an unknown project Spec version")
        phase=result.phase or before.phase;status=result.spec_status or (SpecStatus(version.status) if version else None)
        if (phase,status) not in self._targets(action,before): raise ValueError("handler proposed an invalid workflow transition")

    def _targets(self, action: CommandAction, before: WorkflowSnapshot) -> set[tuple[ProjectPhase,SpecStatus|None]]:
        review={(ProjectPhase.REVIEW,x) for x in self._REVIEW}
        return { (CommandAction.MESSAGE,ProjectPhase.NEED_CLARIFICATION,None):{(ProjectPhase.NEED_CLARIFICATION,None),(ProjectPhase.SPECIFICATION,None)},
                 (CommandAction.MESSAGE,ProjectPhase.REVIEW,SpecStatus.NEED_CLARIFICATION):{(ProjectPhase.REVIEW,SpecStatus.NEED_CLARIFICATION),(ProjectPhase.REVIEW,SpecStatus.REWORK)},
                 (CommandAction.CREATE_SPEC,ProjectPhase.SPECIFICATION,None):review,
                 (CommandAction.CREATE_SPEC,ProjectPhase.REVIEW,SpecStatus.AUTO_REVIEW):review,
                 (CommandAction.REVISE,ProjectPhase.REVIEW,SpecStatus.REWORK):review,
                 (CommandAction.PUBLISH_REVIEW,ProjectPhase.REVIEW,SpecStatus.HUMAN_REVIEW):review,
                 (CommandAction.PUBLISH_REVIEW,ProjectPhase.REVIEW,SpecStatus.REWORK):review,
                 (CommandAction.RESTORE_SPEC_VERSION,ProjectPhase.REVIEW,SpecStatus.HUMAN_REVIEW):review,
                 (CommandAction.RESTORE_SPEC_VERSION,ProjectPhase.REVIEW,SpecStatus.REWORK):review,
                 (CommandAction.RESTORE_SPEC_VERSION,ProjectPhase.REVIEW,SpecStatus.NEED_CLARIFICATION):review,
                 (CommandAction.RESTORE_SPEC_VERSION,ProjectPhase.REVIEW,SpecStatus.APPROVED):review,
                 (CommandAction.RESTORE_SPEC_VERSION,ProjectPhase.REVIEW,SpecStatus.REJECTED):review,
                 (CommandAction.CONVERT_TO_WORK_ITEM,ProjectPhase.REVIEW,SpecStatus.APPROVED):{(ProjectPhase.AGENT_SPECS_READY,SpecStatus.APPROVED)},
                 # Execution advances WorkItem run state only, so every execution
                 # action is a self-loop on the terminal workflow snapshot.
                 **{(action,ProjectPhase.AGENT_SPECS_READY,SpecStatus.APPROVED):{(ProjectPhase.AGENT_SPECS_READY,SpecStatus.APPROVED)} for action in _EXECUTION_ACTIONS},
                 }.get((action,before.phase,before.spec_status),set())

    def _apply(self, db: Session, project: Project, result: CommandHandlerResult) -> None:
        if result.current_spec_version_id:project.current_spec_version_id=result.current_spec_version_id
        if result.phase:project.phase=result.phase.value
        if result.spec_status:
            version=self._current_spec(db,project)
            if version is None:raise ValueError("handler cannot set Spec status without current Spec")
            version.status=result.spec_status.value

    def _validate_audit(self,payload:Mapping[str,object])->None:
        reserved=self._RESERVED_AUDIT&set(payload)
        if reserved:raise ValueError(f"handler audit payload uses reserved keys: {sorted(reserved)!r}")
    @staticmethod
    def _advance(db:Session,project:Project,expected:int)->None:
        result=db.execute(update(Project).where(Project.id==project.id,Project.state_version==expected).values(state_version=Project.state_version+1,updated_at=_now()))
        if result.rowcount!=1:raise StaleState("expected state version is no longer current")
    _advance_project = _advance
    def _assert_legal(self,db:Session,project:Project,action:CommandAction)->None:
        v=self._current_spec(db,project);assert_action_allowed(WorkflowSnapshot(ProjectPhase(project.phase),SpecStatus(v.status) if v else None),action)
    def _before_initial_attempt_insert(self, session_id: str, command_id: str) -> None:
        """No-op production seam; tests use it to coordinate independent claim races."""
    def _before_materialization_cas(self, session_id: str, command_id: str) -> None:
        """No-op production seam; tests use it to coordinate independent CAS races."""
    def _before_stale_replay(self, session_id: str, command_id: str) -> None:
        """No-op production seam; tests observe receipt convergence after a CAS loss."""
    def _assert_current_command(self, db: Session, project: Project, request: SessionCommandRequest) -> None:
        if project.state_version != request.expected_state_version:
            raise StaleState("expected state version is no longer current")
        self._assert_legal(db, project, request.action)
    def _receipt_for(self,session_id:str,command_id:str,input_hash:str)->CommandResult|None:
        with self._session_factory() as db:return self._receipt(db,session_id,command_id,input_hash)
    @staticmethod
    def _receipt(db:Session,session_id:str,command_id:str,input_hash:str)->CommandResult|None:
        r=db.query(ProcessedCommand).filter_by(session_id=session_id,command_id=command_id).one_or_none()
        if not r:return None
        if r.input_hash!=input_hash:raise CommandConflict("command_id was used with different input")
        return CommandResult.model_validate(r.result)
    @staticmethod
    def _project(db:Session,session_id:str)->Project:
        p=db.query(Project).filter_by(session_id=session_id).one_or_none()
        if not p:raise KeyError(f"session not found: {session_id}")
        return p
    @staticmethod
    def _current_spec(db:Session,project:Project)->SpecVersion|None:return db.get(SpecVersion,project.current_spec_version_id) if project.current_spec_version_id else None
    @staticmethod
    def _comments(request:SessionCommandRequest)->str|None:return request.message.strip() if request.message else None
    @staticmethod
    def _assert_reviewer(project:Project,actor:str)->None:
        assert_reviewer(project, actor)
    def _state(self,db:Session,project:Project)->SessionState:
        return project_state(db, project)


__all__=["ActionScopedUnitOfWork","CallableTwoPhaseHandler","CommandConflict","CommandHandlerFailure","CommandHandlerRejected","CommandHandlerResult","CommandInDoubt","CommandService","ForbiddenActor","MaterializeContext","PreparedCommand","PrepareContext","StaleState","TwoPhaseCommandHandler","_canonical_hash","assert_reviewer"]
