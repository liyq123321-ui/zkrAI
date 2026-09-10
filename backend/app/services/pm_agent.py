"""Pure contracts for PM rewrites driven by frozen Gitea review evidence."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, Callable

from sqlalchemy import desc, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.agents.gateway import AgentGateway
from app.database.models import (
    AgentCall,
    Artifact,
    AuditEvent,
    ClarificationResponse,
    CommandAttempt,
    PrdVersion,
    Project,
    ReviewTask,
    SpecReview,
    SpecVersion,
    WorkItem,
)
from app.domain.types import (
    CommandAction,
    PrdRewriteOutput,
    ReviewFinding,
    RewriteAction,
    WorkItemKind,
)
from app.schemas.prd_review import DiagramRevisionRequest, ReviewTaskRead
from app.schemas.workflow import SessionCommandRequest
from app.services.gitea import GiteaReply, GiteaThread


logger = logging.getLogger(__name__)


class NoUnresolvedComments(ValueError):
    """The authoritative Gitea review has no work for the PM."""

    code = "NO_UNRESOLVED_COMMENTS"


class NoReviewFindings(ValueError):
    """Automatic finding resolution was requested after the findings disappeared."""

    code = "NO_REVIEW_FINDINGS"


class _StaleReviewBase(RuntimeError):
    code = "STALE_REVIEW_BASE"


_PUBLIC_TASK_ERRORS = {
    "PROCESS_INTERRUPTED": "Review publication was interrupted. Submit it again to retry.",
    "STALE_REVIEW_BASE": "The reviewed PRD version changed before publication.",
    "GITEA_NOT_CONFIGURED": "Gitea is not configured.",
    "GITEA_FORBIDDEN": "Gitea access is forbidden.",
    "GITEA_RATE_LIMITED": "Gitea is rate limited.",
    "GITEA_TIMEOUT": "Gitea is temporarily unavailable.",
    "GITEA_UNAVAILABLE": "Gitea is temporarily unavailable.",
    "GITEA_INCOMPATIBLE": "Gitea is incompatible.",
    "GITEA_NOT_FOUND": "The required Gitea resource was not found.",
    "PRD_CONTENT_CONFLICT": "The PRD publication conflicts with stored review state.",
    "PM_REWRITE_FAILED": "The PM rewrite did not complete.",
    "AUTO_REVIEW_NO_CHANGE": (
        "The Agent returned the same PRD twice while resolving review findings. "
        "Add an explicit comment to guide the unresolved decision and retry."
    ),
    "REVIEW_PUBLISH_FAILED": "Review publication did not complete.",
}

_AUTO_REVIEW_NO_CHANGE = "AUTO_REVIEW_NO_CHANGE"

class RewriteCoverageError(ValueError):
    """The PM did not give exactly one outcome for every frozen comment."""


def _freeze(value: Any) -> Any:
    """Recursively detach JSON-like evidence from mutable caller-owned objects."""

    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _json_value(value: Any) -> Any:
    """Convert frozen evidence to deterministic JSON-compatible primitives."""

    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if is_dataclass(value):
        return {field.name: _json_value(getattr(value, field.name)) for field in fields(value)}
    if hasattr(value, "model_dump"):
        return _json_value(value.model_dump(mode="json"))
    return value


@dataclass(frozen=True, slots=True)
class ReviewReplySnapshot:
    """Immutable reply evidence under one Gitea review comment."""

    id: int
    body: str
    user: str
    created_at: str
    extra: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        if self.id <= 0:
            raise ValueError("review reply ID must be positive")
        object.__setattr__(self, "extra", _freeze(self.extra or {}))

    @classmethod
    def from_gitea(cls, reply: GiteaReply) -> "ReviewReplySnapshot":
        return cls(id=reply.id, body=reply.body, user=reply.user, created_at=reply.created_at)


@dataclass(frozen=True, slots=True)
class ReviewCommentSnapshot:
    """Immutable top-level Gitea comment and its display-ordered replies."""

    id: int
    path: str
    line: int
    body: str
    replies: tuple[ReviewReplySnapshot, ...]
    user: str = ""
    created_at: str = ""
    resolved: bool = False

    def __post_init__(self) -> None:
        if self.id <= 0:
            raise ValueError("review comment ID must be positive")
        if self.line <= 0:
            raise ValueError("review comment line must be positive")
        object.__setattr__(self, "replies", tuple(self.replies))

    @classmethod
    def from_gitea(cls, thread: GiteaThread) -> "ReviewCommentSnapshot":
        comment = thread.comment
        return cls(
            id=comment.id,
            path=comment.path,
            line=comment.line,
            body=comment.body,
            user=comment.user,
            created_at=comment.created_at,
            resolved=comment.resolved,
            replies=tuple(ReviewReplySnapshot.from_gitea(reply) for reply in thread.replies),
        )


@dataclass(frozen=True, slots=True)
class ReviewSnapshot:
    """An immutable review base; comment order remains Gitea's display order."""

    base_commit_sha: str
    comments: tuple[ReviewCommentSnapshot, ...]
    review_findings: tuple[Mapping[str, object], ...] = ()
    auto_resolve_findings: bool = False
    decision_history: tuple[ReviewCommentSnapshot, ...] = ()

    def __post_init__(self) -> None:
        if not self.base_commit_sha:
            raise ValueError("review snapshot base commit SHA is required")
        object.__setattr__(self, "comments", tuple(self.comments))
        object.__setattr__(self, "decision_history", tuple(self.decision_history))
        object.__setattr__(
            self,
            "review_findings",
            tuple(_freeze(dict(finding)) for finding in self.review_findings),
        )

    @classmethod
    def from_gitea(
        cls,
        base_commit_sha: str,
        threads: Sequence[GiteaThread],
        *,
        review_findings: Sequence[Mapping[str, object]] = (),
        auto_resolve_findings: bool = False,
        decision_history: Sequence[GiteaThread] = (),
    ) -> "ReviewSnapshot":
        return cls(
            base_commit_sha=base_commit_sha,
            comments=tuple(ReviewCommentSnapshot.from_gitea(thread) for thread in threads),
            review_findings=tuple(review_findings),
            auto_resolve_findings=auto_resolve_findings,
            decision_history=tuple(
                ReviewCommentSnapshot.from_gitea(thread)
                for thread in decision_history
            ),
        )


def snapshot_hash(snapshot: ReviewSnapshot) -> str:
    """Hash the frozen base independently of Gitea display ordering.

    Only the temporary hash projection is sorted.  ``ReviewSnapshot`` and the
    Agent payload retain the server's display order.
    """

    canonical_value = _json_value(snapshot)
    canonical_value["comments"] = sorted(
        (
            {
                **comment,
                "replies": sorted(comment["replies"], key=lambda reply: reply["id"]),
            }
            for comment in canonical_value["comments"]
        ),
        key=lambda comment: comment["id"],
    )
    canonical_value["decision_history"] = sorted(
        canonical_value["decision_history"],
        key=lambda comment: comment["id"],
    )
    # Preserve legacy task hashes when automatic finding resolution is not in
    # use, while binding opted-in tasks to the exact immutable finding set.
    if snapshot.auto_resolve_findings or snapshot.review_findings:
        canonical_value["review_findings"] = sorted(
            canonical_value["review_findings"],
            key=lambda finding: json.dumps(
                finding, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ),
        )
        canonical_value["auto_resolve_findings"] = snapshot.auto_resolve_findings
    else:
        canonical_value.pop("review_findings", None)
        canonical_value.pop("auto_resolve_findings", None)
    if not snapshot.decision_history:
        canonical_value.pop("decision_history", None)
    canonical = json.dumps(
        canonical_value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def prioritized_review_threads(
    current_filename: str,
    threads: Sequence[GiteaThread],
) -> tuple[list[GiteaThread], list[GiteaThread]]:
    """Split actionable current comments from newest-first historical decisions."""

    def latest_evidence(thread: GiteaThread) -> tuple[str, int]:
        timestamps = [thread.comment.created_at, *(reply.created_at for reply in thread.replies)]
        identifiers = [thread.comment.id, *(reply.id for reply in thread.replies)]
        return max(timestamps), max(identifiers)

    current = [
        thread
        for thread in threads
        if thread.comment.path == current_filename and not thread.comment.resolved
    ]
    history = [
        thread for thread in threads if thread.comment.path != current_filename
    ]
    current.sort(key=latest_evidence, reverse=True)
    history.sort(key=latest_evidence, reverse=True)
    return current, history


def validate_rewrite(output: PrdRewriteOutput, expected_ids: set[int]) -> PrdRewriteOutput:
    """Reject missing, duplicate, and invented comment outcomes before publication."""

    response_ids = [response.comment_id for response in output.responses]
    if len(response_ids) != len(set(response_ids)):
        raise RewriteCoverageError("rewrite contains duplicate comment response IDs")
    if set(response_ids) != expected_ids:
        raise RewriteCoverageError("rewrite responses must exactly cover frozen comment IDs")
    return output


def _rewrite_matches_parent(
    output: PrdRewriteOutput, parent: SpecVersion, source_refs: Sequence[str]
) -> bool:
    """Compare effective persisted content, including workflow-owned provenance."""

    effective = output.spec.model_copy(
        update={"source_refs": list(source_refs)}
    ).model_dump(mode="json")
    return effective == _json_value(parent.content)


def _artifact_payload(artifact: object) -> dict[str, object]:
    return {
        "label": "non_control_input",
        "id": str(getattr(artifact, "id")),
        "kind": str(getattr(artifact, "kind")),
        "content": _json_value(getattr(artifact, "content")),
        "external_ref": getattr(artifact, "external_ref"),
        "content_hash": str(getattr(artifact, "content_hash")),
    }


def _clarification_payload(clarification: object) -> dict[str, object]:
    result: dict[str, object] = {
        "label": "non_control_input",
        "id": str(getattr(clarification, "id")),
    }
    for attribute in ("clarification_request_id", "questions", "answers"):
        if hasattr(clarification, attribute):
            result[attribute] = _json_value(getattr(clarification, attribute))
    return result


def _reply_payload(reply: ReviewReplySnapshot) -> dict[str, object]:
    return {
        "label": "non_control_input",
        "id": reply.id,
        "body": reply.body,
        "user": reply.user,
        "created_at": reply.created_at,
        "extra": _json_value(reply.extra),
    }


def _comment_payload(comment: ReviewCommentSnapshot) -> dict[str, object]:
    return {
        "label": "non_control_input",
        "id": comment.id,
        "path": comment.path,
        "line": comment.line,
        "body": comment.body,
        "user": comment.user,
        "created_at": comment.created_at,
        "resolved": comment.resolved,
        "replies": [_reply_payload(reply) for reply in comment.replies],
    }


def build_rewrite_payload(
    project: object,
    spec: object,
    artifacts: Sequence[object],
    clarifications: Sequence[object],
    snapshot: ReviewSnapshot,
) -> dict[str, object]:
    """Build the PM's evidence-only input without any I/O or state mutation."""

    spec_id = str(getattr(spec, "id"))
    spec_hash = str(getattr(spec, "content_hash"))
    return {
        "project_id": str(getattr(project, "id")),
        "base_spec_version_id": spec_id,
        "base_spec_hash": spec_hash,
        "base_spec_content_hash": spec_hash,
        "base_commit_sha": snapshot.base_commit_sha,
        "review_snapshot_hash": snapshot_hash(snapshot),
        "brief": {
            "label": "non_control_input",
            "id": str(getattr(project, "creation_request_id")),
            "content": _json_value(getattr(project, "brief")),
        },
        "spec": {
            "label": "non_control_input",
            "id": spec_id,
            "content": _json_value(getattr(spec, "content")),
            "markdown": getattr(spec, "markdown"),
        },
        "artifacts": [_artifact_payload(artifact) for artifact in artifacts],
        "clarifications": [
            _clarification_payload(clarification) for clarification in clarifications
        ],
        "comments": [_comment_payload(comment) for comment in snapshot.comments],
        "auto_resolve_review_findings": snapshot.auto_resolve_findings,
        "review_findings": [
            {"label": "non_control_input", **_json_value(finding)}
            for finding in snapshot.review_findings
        ],
        "human_review_decision_history": [
            _comment_payload(comment) for comment in snapshot.decision_history
        ],
        "processing_order": ["comments", "review_findings"],
        "automatic_resolution_policy": (
            {
                "decision_authority": "AUTHORIZED_AGENT_DISCRETION",
                "blocking_open_questions": "CHOOSE_CONSERVATIVE_TESTABLE_DEFAULT",
                "required_result": "CONCRETE_SPEC_CHANGE",
                "comment_provenance": "GENERATION_SNAPSHOT_NOT_SOURCE_REFS",
                "unknown_responsible_actor": {
                    "required_format": "EXPLICIT_ROLE_OR_ACTOR_PLUS_CONCRETE_DUTY",
                    "forbidden_forms": [
                        "actorless obligation such as approval is required",
                        "actorless verb phrase such as approve the release",
                        "placeholder actor such as TBD, unknown, or unassigned",
                    ],
                    "repair_method": (
                        "Preserve the duty and name a conservative accountable role "
                        "supported by the Brief or the duty domain. Use forms such as "
                        "Project Manager owns requirement approval, QA verifies acceptance "
                        "results, Release Manager approves release requests, or Operations "
                        "owns incident response. Do not invent a person's identity."
                    ),
                },
            }
            if snapshot.auto_resolve_findings
            else None
        ),
    }


class PmRewriteService:
    """Adapt one frozen Gitea review task to the two-phase command boundary."""

    def __init__(
        self, session_factory: Callable[[], Session], agent: AgentGateway
    ) -> None:
        from app.services.spec_service import SpecService

        self._session_factory = session_factory
        self._agent = agent
        self._specs = SpecService(session_factory, agent)

    def as_command_handler(self):
        service = self

        class _Handler:
            async def prepare(self, context):
                return await service._prepare(context)

            def materialize(self, uow, context, prepared):
                return service._materialize(uow, context, prepared)

        return _Handler()

    async def _prepare(self, context):
        from app.services.command_service import (
            CommandHandlerFailure,
            CommandHandlerRejected,
            PreparedCommand,
            assert_reviewer,
        )
        from app.services.spec_service import AgentCallInDoubt

        task_id = context.request.payload.get("task_id")
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("publish_review requires a task_id")
        task_id = task_id.strip()

        with self._session_factory() as db:
            task, project, parent, snapshot = self._validated_base(
                db, context, task_id
            )
            assert_reviewer(project, context.request.actor_id)
            artifacts = (
                db.query(Artifact)
                .filter_by(project_id=project.id)
                .order_by(Artifact.created_at, Artifact.id)
                .all()
            )
            clarifications = (
                db.query(ClarificationResponse)
                .filter_by(project_id=project.id)
                .order_by(ClarificationResponse.created_at, ClarificationResponse.id)
                .all()
            )
            payload = build_rewrite_payload(
                project, parent, artifacts, clarifications, snapshot
            )
            stable_binding = self._stable_call_binding(
                context, task, project, parent
            )
            payload.update(stable_binding)
            calls = [
                item
                for item in db.query(AgentCall)
                .filter_by(project_id=project.id, operation="rewrite_prd")
                .order_by(desc(AgentCall.started_at), desc(AgentCall.id))
                .all()
                if self._same_command_binding(item.request, stable_binding)
            ]
            for existing in calls:
                try:
                    self._validate_saved_call_binding(
                        existing, stable_binding, payload
                    )
                except ValueError as error:
                    if existing.status in {
                        "RESULT_READY",
                        "SUCCEEDED",
                        "NO_CHANGE",
                        "COMPLETED",
                    }:
                        raise CommandHandlerRejected(
                            str(error),
                            agent_call_ids=[existing.id],
                            audit_payload={"review_task_id": task.id},
                        ) from error
                    raise CommandHandlerFailure(
                        str(error), agent_call_ids=[existing.id]
                    ) from error
            if any(item.status == "PENDING" for item in calls):
                call = next(item for item in calls if item.status == "PENDING")
                raise AgentCallInDoubt(
                    f"rewrite Agent call {call.id} is pending; result must be recovered before retry"
                )
            call = next(
                (item for item in calls if item.status == "RESULT_READY"), None
            )
            if call is not None:
                output = PrdRewriteOutput.model_validate(call.response)
            else:
                pm_session = self._specs._agent_session(
                    db, project.id, "PM", "Rewrite PRD from frozen Gitea review"
                )
                call = AgentCall(
                    id=_new_service_id(),
                    project_id=project.id,
                    agent_session_id=pm_session.id,
                    operation="rewrite_prd",
                    request=payload,
                    status="PENDING",
                )
                db.add(call)
                db.commit()
                output = None
            call_id = call.id
            expected_ids = set(task.comment_ids)
            parent_refs = list(parent.input_refs)

        if output is None:
            try:
                output = await self._agent.rewrite_prd(payload)
            except Exception as error:
                self._specs._record_call_failure(
                    context.project_id,
                    call_id,
                    error,
                    "PRD_REWRITE_FAILED",
                )
                raise CommandHandlerFailure(
                    str(error), agent_call_ids=[call_id]
                ) from error
            self._specs._record_call_result(
                context.project_id, call_id, output.model_dump(mode="json")
            )

        try:
            validate_rewrite(output, expected_ids)
        except RewriteCoverageError as error:
            raise CommandHandlerRejected(
                str(error),
                agent_call_ids=[call_id],
                audit_payload={"review_task_id": task_id},
            ) from error

        prior_no_change_call_id = call.request.get("prior_no_change_call_id")
        superseded_rewrite_call_ids: list[str] = (
            [prior_no_change_call_id]
            if call.request.get("repair_attempt") == 1
            and isinstance(prior_no_change_call_id, str)
            else []
        )
        if (
            task.auto_resolve_findings
            and task.finding_snapshot
            and _rewrite_matches_parent(output, parent, parent_refs)
        ):
            if call.request.get("repair_attempt") == 1:
                raise CommandHandlerRejected(
                    _AUTO_REVIEW_NO_CHANGE,
                    agent_call_ids=[*superseded_rewrite_call_ids, call_id],
                    audit_payload={"review_task_id": task_id},
                )
            superseded_rewrite_call_ids.append(call_id)
            repair_payload = {
                **payload,
                "repair_attempt": 1,
                "prior_no_change_call_id": call_id,
                "repair_feedback": {
                    "reason": "The prior rewrite was semantically identical to the base PRD.",
                    "required_action": (
                        "Apply every review finding, choose conservative defaults for "
                        "delegated decisions, and remove resolved blocking open questions."
                    ),
                },
            }
            with self._session_factory() as db:
                repair_calls = (
                    db.query(AgentCall)
                    .filter_by(project_id=context.project_id, operation="rewrite_prd")
                    .order_by(desc(AgentCall.started_at), desc(AgentCall.id))
                    .all()
                )
                repair_call = next(
                    (item for item in repair_calls if item.request == repair_payload),
                    None,
                )
                if repair_call is not None and repair_call.status == "PENDING":
                    raise AgentCallInDoubt(
                        f"rewrite Agent repair call {repair_call.id} is pending; "
                        "result must be recovered before retry"
                    )
                if repair_call is not None and repair_call.status == "RESULT_READY":
                    repaired_output = PrdRewriteOutput.model_validate(repair_call.response)
                else:
                    pm_session = self._specs._agent_session(
                        db,
                        context.project_id,
                        "PM",
                        "Repair unchanged automatic PRD review rewrite",
                    )
                    repair_call = AgentCall(
                        id=_new_service_id(),
                        project_id=context.project_id,
                        agent_session_id=pm_session.id,
                        operation="rewrite_prd",
                        request=repair_payload,
                        status="PENDING",
                    )
                    db.add(repair_call)
                    db.commit()
                    repaired_output = None
                repair_call_id = repair_call.id
            if repaired_output is None:
                try:
                    repaired_output = await self._agent.rewrite_prd(repair_payload)
                except Exception as error:
                    self._specs._record_call_failure(
                        context.project_id,
                        repair_call_id,
                        error,
                        "PRD_REWRITE_FAILED",
                    )
                    raise CommandHandlerFailure(
                        str(error),
                        agent_call_ids=[repair_call_id],
                    ) from error
                self._specs._record_call_result(
                    context.project_id,
                    repair_call_id,
                    repaired_output.model_dump(mode="json"),
                )
            try:
                validate_rewrite(repaired_output, expected_ids)
            except RewriteCoverageError as error:
                raise CommandHandlerRejected(
                    str(error),
                    agent_call_ids=[*superseded_rewrite_call_ids, repair_call_id],
                    audit_payload={"review_task_id": task_id},
                ) from error
            if _rewrite_matches_parent(repaired_output, parent, parent_refs):
                raise CommandHandlerRejected(
                    _AUTO_REVIEW_NO_CHANGE,
                    agent_call_ids=[*superseded_rewrite_call_ids, repair_call_id],
                    audit_payload={"review_task_id": task_id},
                )
            output = repaired_output
            call_id = repair_call_id

        prepared = await self._specs.prepare_external_revision(
            context,
            output.spec,
            parent,
            call_id,
            output.change_summary,
            "GITEA_REVIEW_COMMENTS",
            source_refs=parent_refs,
            require_new_revision=True,
            forced_clarification_questions=[
                {
                    "question_id": f"GITEA-{response.comment_id}-CONFIRM",
                    "question": (
                        f"Please confirm the requested decision for Gitea review "
                        f"comment {response.comment_id}: {response.note}"
                    ),
                    "reason": "The PM marked this frozen review comment as requiring human confirmation.",
                    "affected_areas": ["gitea_review"],
                    "blocking": True,
                }
                for response in output.responses
                if response.action is RewriteAction.NEEDS_HUMAN_CONFIRMATION
            ],
        )
        return PreparedCommand(
            payload={
                **dict(prepared.payload),
                "review_task_id": task_id,
                "superseded_rewrite_call_ids": superseded_rewrite_call_ids,
            },
            agent_backed=prepared.agent_backed,
            agent_call_ids=list(prepared.agent_call_ids),
            audit_payload={
                **dict(prepared.audit_payload),
                "review_task_id": task_id,
            },
        )

    @staticmethod
    def _stable_call_binding(context, task, project, parent) -> dict[str, object]:
        return {
            "command_id": context.request.command_id,
            "input_hash": context.input_hash,
            "task_id": task.id,
            "session_id": context.session_id,
            "project_id": project.id,
            "base_spec_version_id": parent.id,
            "base_spec_hash": parent.content_hash,
            "base_spec_content_hash": parent.content_hash,
            "base_commit_sha": task.base_commit_sha,
            "review_snapshot_hash": task.comment_snapshot_hash,
            "comment_ids": list(task.comment_ids),
            "auto_resolve_review_findings": task.auto_resolve_findings,
        }

    @staticmethod
    def _same_command_binding(
        request: Mapping[str, object], binding: Mapping[str, object]
    ) -> bool:
        return all(
            request.get(key) == binding[key]
            for key in ("command_id", "input_hash", "task_id", "session_id")
        )

    @staticmethod
    def _validate_saved_call_binding(
        call: AgentCall,
        binding: Mapping[str, object],
        current_payload: Mapping[str, object],
    ) -> None:
        stable_keys = (
            "command_id",
            "input_hash",
            "task_id",
            "session_id",
            "project_id",
            "base_spec_version_id",
            "base_spec_hash",
            "base_spec_content_hash",
            "base_commit_sha",
            "review_snapshot_hash",
            "comment_ids",
            "auto_resolve_review_findings",
        )
        if (
            any(call.request.get(key) != binding[key] for key in stable_keys)
            or call.request.get("comments") != current_payload.get("comments")
            or call.request.get("review_findings") != current_payload.get("review_findings")
            or call.request.get("human_review_decision_history")
            != current_payload.get("human_review_decision_history")
            or call.request.get("spec") != current_payload.get("spec")
        ):
            raise ValueError("saved rewrite Agent call binding is inconsistent")

    def _materialize(self, uow, context, prepared):
        result = self._specs.materialize_revision(uow, context, prepared)
        if prepared.payload.get("no_change"):
            version_id = str(prepared.payload["parent_version_id"])
            revision = int(prepared.payload["parent_revision"])
        else:
            version_id = str(prepared.payload["version_id"])
            revision = int(prepared.payload["revision"])
        uow.set_review_task_revision(
            str(prepared.payload["review_task_id"]), version_id, revision
        )
        return result

    @staticmethod
    def _validated_base(db: Session, context, task_id: str):
        from app.services.prd_review import _markdown_hash

        task = db.get(ReviewTask, task_id)
        if task is None:
            raise KeyError("review task not found")
        item = db.get(WorkItem, task.wi)
        project = db.get(Project, context.project_id)
        if (
            item is None
            or item.kind != WorkItemKind.ROOT.value
            or item.parent_id is not None
            or item.project_id != context.project_id
            or project is None
        ):
            raise ValueError("review task belongs to another project")
        binding = db.get(PrdVersion, (task.wi, task.base_version))
        parent = db.get(SpecVersion, binding.spec_version_id) if binding else None
        if (
            binding is None
            or parent is None
            or parent.project_id != project.id
            or project.current_spec_version_id != parent.id
            or context.state.current_spec_version_id != parent.id
            or parent.revision != task.base_version
            or binding.content_hash != _markdown_hash(parent.markdown)
            or binding.spec_content_hash != parent.content_hash
            or binding.commit_sha != task.base_commit_sha
            or task.status not in {"pending", "processing"}
            or task.new_spec_version_id is not None
            or task.new_version is not None
        ):
            raise ValueError("review task base is stale or inconsistent")
        snapshot = _task_snapshot(task)
        ids = [comment.id for comment in snapshot.comments]
        if (
            list(task.comment_ids) != ids
            or len(ids) != len(set(ids))
            or snapshot_hash(snapshot) != task.comment_snapshot_hash
        ):
            raise ValueError("review task snapshot is inconsistent")
        return task, project, parent, snapshot


def _task_snapshot(task: ReviewTask) -> ReviewSnapshot:
    def parse_comments(raw_comments: object) -> tuple[ReviewCommentSnapshot, ...]:
        comments: list[ReviewCommentSnapshot] = []
        if not isinstance(raw_comments, list):
            raise ValueError("review task snapshot is invalid")
        for raw_comment in raw_comments:
            if not isinstance(raw_comment, Mapping):
                raise ValueError("review task snapshot is invalid")
            raw_replies = raw_comment.get("replies", [])
            if not isinstance(raw_replies, list):
                raise ValueError("review task snapshot is invalid")
            replies = tuple(
                ReviewReplySnapshot(**dict(raw_reply))
                for raw_reply in raw_replies
                if isinstance(raw_reply, Mapping)
            )
            if len(replies) != len(raw_replies):
                raise ValueError("review task snapshot is invalid")
            comments.append(
                ReviewCommentSnapshot(
                    id=int(raw_comment["id"]),
                    path=str(raw_comment["path"]),
                    line=int(raw_comment["line"]),
                    body=str(raw_comment["body"]),
                    user=str(raw_comment.get("user", "")),
                    created_at=str(raw_comment.get("created_at", "")),
                    resolved=bool(raw_comment.get("resolved", False)),
                    replies=replies,
                )
            )
        return tuple(comments)

    comments = parse_comments(task.comment_snapshot)
    decision_history = parse_comments(task.decision_history_snapshot or [])
    return ReviewSnapshot(
        base_commit_sha=task.base_commit_sha,
        comments=comments,
        review_findings=tuple(task.finding_snapshot or ()),
        auto_resolve_findings=bool(task.auto_resolve_findings),
        decision_history=decision_history,
    )


def _new_service_id() -> str:
    from uuid import uuid4

    return str(uuid4())


class ReviewPublishCoordinator:
    """Freeze and run one durable, idempotent Gitea review publication."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        gitea,
        agent: AgentGateway,
        *,
        prototype_service=None,
    ) -> None:
        from app.services.prd_review import PrdReviewService

        self._session_factory = session_factory
        self._gitea = gitea
        self._agent = agent
        if prototype_service is None:
            from app.services.prd_prototype import PrdPrototypeService

            prototype_service = PrdPrototypeService(session_factory, agent)
        self._prototypes = prototype_service
        self._reviews = PrdReviewService(session_factory, gitea)
        from app.services.diagram_revision import DiagramRevisionService

        self._diagram_revisions = DiagramRevisionService(session_factory, agent)

    async def create_or_resume_diagram_revision(
        self,
        wi: str,
        diagram_id: str,
        actor_id: str,
        request: DiagramRevisionRequest,
    ) -> tuple[ReviewTask, bool]:
        """Freeze one authenticated external draw.io save into the shared task ledger."""

        from app.services.diagram_revision import (
            DiagramRevisionSnapshot,
            diagram_revision_snapshot,
        )
        from app.services.drawio_diagrams import (
            normalize_drawio_xml,
            validate_drawio_xml,
        )
        from app.services.prd_review import PrdContentConflict, PrdForbidden, PrdNotFound

        normalized = normalize_drawio_xml(request.drawio_xml)
        snapshot = DiagramRevisionSnapshot(
            diagram_id=diagram_id,
            drawio_xml=normalized,
            change_summary=request.change_summary,
        )
        digest = snapshot.digest()
        with self._session_factory() as db:
            existing = (
                db.query(ReviewTask)
                .filter_by(
                    wi=wi,
                    base_version=request.base_version,
                    comment_snapshot_hash=digest,
                )
                .one_or_none()
            )
            if existing is not None:
                if (
                    diagram_revision_snapshot(existing) != snapshot
                    or existing.base_commit_sha != request.base_commit_sha
                ):
                    raise PrdContentConflict("diagram revision task snapshot conflicts")
                if existing.initiator_actor_id != actor_id:
                    raise PrdForbidden("only the initiating reviewer may resume this diagram revision")
                existing_id = existing.id
                existing_status = existing.status
                no_change = existing.status == "done" and existing.new_spec_version_id is None
            else:
                existing_id = None
                existing_status = None
                no_change = False

        if existing_id is not None:
            self._revalidate_initiator(existing_id)
            if existing_status == "error":
                with self._session_factory() as db:
                    with db.begin():
                        task = db.get(ReviewTask, existing_id)
                        binding = (
                            db.get(PrdVersion, (wi, task.base_version))
                            if task is not None
                            else None
                        )
                        if task is None or binding is None:
                            raise PrdContentConflict("diagram revision base is unavailable")
                        self._validate_task_base(db, binding, actor_id, task.id)
                        task = self._reset_error_task(db, task.id)
                    return task, False
            with self._session_factory() as db:
                task = db.get(ReviewTask, existing_id)
                if task is None:
                    raise PrdContentConflict("diagram revision task disappeared")
                return task, no_change

        binding, document = await self._reviews.diagram_revision_base(wi, actor_id)
        if (
            binding.version != request.base_version
            or binding.commit_sha != request.base_commit_sha
        ):
            raise PrdContentConflict("diagram revision base changed")
        current = next(
            (diagram for diagram in document.er_diagrams if diagram.diagram_id == diagram_id),
            None,
        )
        if current is None:
            raise PrdNotFound("diagram revision target was not found")
        validate_drawio_xml(normalized)
        no_change = normalize_drawio_xml(current.drawio_xml) == normalized

        with self._session_factory() as db:
            try:
                with db.begin():
                    self._validate_task_base(db, binding, actor_id)
                    task = ReviewTask(
                        id=_new_service_id(),
                        wi=wi,
                        initiator_actor_id=actor_id,
                        status="done" if no_change else "pending",
                        base_version=binding.version,
                        base_commit_sha=str(binding.commit_sha),
                        comment_ids=[],
                        comment_snapshot=snapshot.as_list(),
                        comment_snapshot_hash=digest,
                        auto_resolve_findings=False,
                        finding_snapshot=[],
                        decision_history_snapshot=[],
                        reply_receipts={},
                    )
                    db.add(task)
                    if no_change:
                        item = db.get(WorkItem, wi)
                        project = db.get(Project, item.project_id) if item is not None else None
                        if project is None:
                            raise PrdContentConflict("diagram revision project is unavailable")
                        xml_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
                        db.add(
                            AuditEvent(
                                id=_new_service_id(),
                                project_id=project.id,
                                session_id=project.session_id,
                                event_type="ER_DIAGRAM_REVISION_NO_CHANGE",
                                actor_id=actor_id,
                                payload={
                                    "review_task_id": task.id,
                                    "diagram_id": diagram_id,
                                    "diagram_hash": xml_hash,
                                    "base_version": binding.version,
                                },
                            )
                        )
                    db.flush()
                return task, no_change
            except IntegrityError:
                db.rollback()

        with self._session_factory() as db:
            task = (
                db.query(ReviewTask)
                .filter_by(
                    wi=wi,
                    base_version=request.base_version,
                    comment_snapshot_hash=digest,
                )
                .one()
            )
            if task.initiator_actor_id != actor_id or diagram_revision_snapshot(task) != snapshot:
                raise PrdForbidden("diagram revision task belongs to another reviewer")
            return task, task.status == "done" and task.new_spec_version_id is None

    async def create_or_resume(
        self,
        wi: str,
        actor_id: str,
        *,
        auto_resolve_findings: bool = False,
    ) -> ReviewTask:
        """Freeze external evidence, then create/reset its one durable task."""

        recovery = self._retry_candidate(wi, actor_id, auto_resolve_findings)
        recovery_id = recovery[0] if recovery is not None else None
        recovery_binding = recovery[1] if recovery is not None else None
        if recovery is not None:
            # A retry consumes the originally frozen snapshot.  Live Gitea
            # comments may have changed after the task's 202 receipt and are
            # evidence for a later task, never this recovery.
            with self._session_factory() as db:
                with db.begin():
                    self._validate_task_base(
                        db, recovery_binding, actor_id, recovery_id
                    )
                    task = db.get(ReviewTask, recovery_id)
                    if task is None or task.initiator_actor_id != actor_id:
                        from app.services.prd_review import PrdForbidden

                        raise PrdForbidden(
                            "only the initiating reviewer may resume this publication"
                        )
                    return self._reset_error_task(db, task.id)
        binding, threads = await self._reviews.publication_threads(
            wi, actor_id, recovery_binding, recovery_id
        )
        threads = self._without_firstflight_replies(wi, threads)
        current_threads, decision_history = prioritized_review_threads(
            binding.filename, threads
        )
        finding_snapshot = (
            self._current_review_findings(binding)
            if auto_resolve_findings
            else []
        )
        if auto_resolve_findings and not finding_snapshot:
            raise NoReviewFindings("no current review findings")
        snapshot = ReviewSnapshot.from_gitea(
            binding.commit_sha,
            current_threads,
            review_findings=finding_snapshot,
            auto_resolve_findings=auto_resolve_findings,
            decision_history=decision_history,
        )
        if not snapshot.comments and not snapshot.review_findings:
            raise NoUnresolvedComments("no unresolved Gitea review comments")
        digest = snapshot_hash(snapshot)
        if recovery is not None and recovery[2] != digest:
            from app.services.prd_review import PrdContentConflict

            raise PrdContentConflict(
                "failed publication evidence changed before it could be resumed"
            )
        comment_ids = [comment.id for comment in snapshot.comments]
        frozen_snapshot = _json_value(snapshot)
        frozen_comments = frozen_snapshot["comments"]
        frozen_decision_history = frozen_snapshot["decision_history"]

        with self._session_factory() as db:
            try:
                with db.begin():
                    self._validate_task_base(db, binding, actor_id, recovery_id)
                    task = (
                        db.query(ReviewTask)
                        .filter_by(
                            wi=wi,
                            base_version=binding.version,
                            comment_snapshot_hash=digest,
                        )
                        .one_or_none()
                    )
                    if task is None:
                        task = ReviewTask(
                            id=_new_service_id(),
                            wi=wi,
                            initiator_actor_id=actor_id,
                            status="pending",
                            base_version=binding.version,
                            base_commit_sha=binding.commit_sha,
                            comment_ids=comment_ids,
                            comment_snapshot=frozen_comments,
                            comment_snapshot_hash=digest,
                            auto_resolve_findings=auto_resolve_findings,
                            finding_snapshot=_json_value(snapshot.review_findings),
                            decision_history_snapshot=frozen_decision_history,
                            reply_receipts={},
                        )
                        db.add(task)
                        db.flush()
                    else:
                        if task.initiator_actor_id != actor_id:
                            from app.services.prd_review import PrdForbidden

                            raise PrdForbidden(
                                "only the initiating reviewer may resume this publication"
                            )
                        task = self._reset_error_task(db, task.id)
                return task
            except IntegrityError:
                db.rollback()

        # A concurrent request may have inserted the same unique snapshot.
        with self._session_factory() as db:
            with db.begin():
                self._validate_task_base(db, binding, actor_id, recovery_id)
                task = (
                    db.query(ReviewTask)
                    .filter_by(
                        wi=wi,
                        base_version=binding.version,
                        comment_snapshot_hash=digest,
                    )
                    .one()
                )
                if task.initiator_actor_id != actor_id:
                    from app.services.prd_review import PrdForbidden

                    raise PrdForbidden(
                        "only the initiating reviewer may resume this publication"
                    )
                task = self._reset_error_task(db, task.id)
            return task

    @staticmethod
    def _reset_error_task(db: Session, task_id: str) -> ReviewTask:
        """CAS-reset one error task without overwriting a concurrent claim."""

        task = db.get(ReviewTask, task_id)
        if task is not None and task.status == "error":
            ReviewPublishCoordinator._settle_interrupted_agent_calls(db, task)
            db.flush()

        result = db.execute(
            update(ReviewTask)
            .where(ReviewTask.id == task_id, ReviewTask.status == "error")
            .values(status="pending", error_code=None, error=None)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount not in {0, 1}:
            raise RuntimeError("review task reset affected an invalid row count")
        db.expire_all()
        task = db.get(ReviewTask, task_id, populate_existing=True)
        if task is None:
            raise RuntimeError("review task disappeared during reset")
        return task

    @staticmethod
    def _settle_interrupted_agent_calls(db: Session, task: ReviewTask) -> None:
        """Close model calls orphaned by an interrupted review publisher.

        A retry is an explicit request to rerun the deterministic generation step.
        Calls still marked PENDING have no live owner once their enclosing review
        task is in the error state, so keeping them unresolved permanently blocks
        the command preparation lease.
        """

        item = db.get(WorkItem, task.wi)
        if item is None or item.project_id is None:
            return
        command_id = f"publish-review:{task.id}"
        attempt = (
            db.query(CommandAttempt)
            .filter_by(project_id=item.project_id, command_id=command_id)
            .one_or_none()
        )
        if attempt is None or attempt.status != "PREPARING":
            return
        now = datetime.now(UTC)
        for call in db.query(AgentCall).filter_by(project_id=item.project_id).all():
            if (
                call.status == "PENDING"
                and isinstance(call.request, dict)
                and call.request.get("command_id") == command_id
                and call.request.get("input_hash") == attempt.input_hash
            ):
                call.status = "FAILED"
                call.error = "Review publication Agent call was interrupted"
                call.response = {"error_code": "PROCESS_INTERRUPTED"}
                call.completed_at = now

    def _retry_candidate(
        self, wi: str, actor_id: str, auto_resolve_findings: bool
    ) -> tuple[str, PrdVersion, str] | None:
        """Select an error task that still owns the current local revision."""

        from app.services.prd_review import PrdReviewService

        with self._session_factory() as db:
            item = db.get(WorkItem, wi)
            project = db.get(Project, item.project_id) if item is not None else None
            current = (
                db.get(SpecVersion, project.current_spec_version_id)
                if project is not None and project.current_spec_version_id
                else None
            )
            if item is None or project is None or current is None:
                return None
            if item.kind != WorkItemKind.ROOT.value or item.parent_id is not None:
                return None
            PrdReviewService.assert_reviewer(project, actor_id)
            candidates = (
                db.query(ReviewTask)
                .filter_by(wi=wi, status="error")
                .order_by(desc(ReviewTask.updated_at), desc(ReviewTask.created_at))
                .all()
            )
            for task in candidates:
                from app.services.diagram_revision import is_diagram_revision_task

                if is_diagram_revision_task(task):
                    continue
                if task.initiator_actor_id != actor_id:
                    continue
                if bool(task.auto_resolve_findings) != auto_resolve_findings:
                    continue
                is_current_base = (
                    task.new_spec_version_id is None
                    and task.base_version == current.revision
                )
                is_partial_revision = task.new_spec_version_id == current.id
                if not (is_current_base or is_partial_revision):
                    continue
                binding = db.get(PrdVersion, (wi, task.base_version))
                if (
                    binding is not None
                    and binding.commit_sha == task.base_commit_sha
                    and (
                        is_current_base
                        or (
                            task.new_version is not None
                            and task.new_version == current.revision
                            and current.parent_version_id == binding.spec_version_id
                        )
                    )
                ):
                    return task.id, binding, task.comment_snapshot_hash
        return None

    def _current_review_findings(self, binding: PrdVersion) -> list[dict[str, object]]:
        """Freeze the current version's authoritative auto-review findings."""

        with self._session_factory() as db:
            reviews = (
                db.query(SpecReview)
                .filter_by(spec_version_id=binding.spec_version_id)
                .order_by(SpecReview.created_at, SpecReview.id)
                .all()
            )
            return [
                ReviewFinding.model_validate(finding).model_dump(mode="json")
                for review in reviews
                for finding in (review.findings or [])
            ]

    def _without_firstflight_replies(
        self, wi: str, threads: Sequence[GiteaThread],
    ) -> list[GiteaThread]:
        """Coordinator receipts are effects, not new human review evidence."""

        verified_ids = self._reviews._verified_agent_reply_ids(wi, list(threads))
        return [
            GiteaThread(
                comment=thread.comment,
                replies=tuple(
                    reply
                    for reply in thread.replies
                    if reply.id not in verified_ids
                ),
            )
            for thread in threads
        ]

    @staticmethod
    def _validate_task_base(
        db: Session,
        binding: PrdVersion,
        actor_id: str,
        recovery_task_id: str | None = None,
    ) -> None:
        from app.services.prd_review import PrdContentConflict, PrdReviewService

        item = db.get(WorkItem, binding.wi)
        project = db.get(Project, item.project_id) if item is not None else None
        current = (
            db.get(SpecVersion, project.current_spec_version_id)
            if project is not None and project.current_spec_version_id
            else None
        )
        persisted = db.get(PrdVersion, (binding.wi, binding.version))
        recovery_task = (
            db.get(ReviewTask, recovery_task_id) if recovery_task_id is not None else None
        )
        current_is_base = (
            persisted is not None
            and current is not None
            and persisted.spec_version_id == current.id
            and current.revision == binding.version
        )
        current_is_partial_revision = (
            persisted is not None
            and current is not None
            and recovery_task is not None
            and recovery_task.status == "error"
            and recovery_task.new_spec_version_id == current.id
            and recovery_task.new_version is not None
            and recovery_task.new_version == current.revision
            and current.parent_version_id == persisted.spec_version_id
            and recovery_task.base_version == binding.version
            and recovery_task.base_commit_sha == binding.commit_sha
        )
        if (
            item is None
            or item.kind != WorkItemKind.ROOT.value
            or item.parent_id is not None
            or project is None
            or current is None
            or persisted is None
            or not (current_is_base or current_is_partial_revision)
            or persisted.commit_sha != binding.commit_sha
            or persisted.content_hash != binding.content_hash
            or persisted.spec_content_hash != binding.spec_content_hash
            or (
                current.status not in {"HUMAN_REVIEW", "REWORK"}
                and not (
                    current.status in {"NEED_CLARIFICATION", "AUTO_REVIEW"}
                    and current_is_partial_revision
                )
            )
        ):
            raise PrdContentConflict(
                "PRD review base changed while its task snapshot was being frozen"
            )
        PrdReviewService.assert_reviewer(project, actor_id)

    async def run(self, task_id: str) -> None:
        """Run one claimed task once; failures become durable retry evidence."""

        if not self._claim(task_id):
            return
        try:
            await self._run_claimed(task_id)
            self._complete(task_id)
        except Exception as error:
            self._fail(task_id, self._error_code(error), str(error))

    def task(self, task_id: str) -> ReviewTaskRead:
        """Return stable public progress without internal failure detail."""

        with self._session_factory() as db:
            task = db.get(ReviewTask, task_id)
            if task is None:
                raise KeyError("review task not found")
            return ReviewTaskRead(
                task_id=task.id,
                wi=task.wi,
                status=task.status,
                base_version=task.base_version,
                new_version=task.new_version,
                new_commit_sha=task.new_commit_sha,
                error=(
                    _PUBLIC_TASK_ERRORS.get(
                        task.error_code or "", "Review publication did not complete."
                    )
                    if task.status == "error"
                    else None
                ),
            )

    def mark_interrupted_tasks(self) -> int:
        """Make abandoned work explicit without retrying an external effect."""

        with self._session_factory() as db:
            result = db.execute(
                update(ReviewTask)
                .where(ReviewTask.status.in_(("pending", "processing")))
                .values(
                    status="error",
                    error_code="PROCESS_INTERRUPTED",
                    error="review publication process was interrupted",
                )
            )
            db.commit()
            return int(result.rowcount or 0)

    def _claim(self, task_id: str) -> bool:
        with self._session_factory() as db:
            result = db.execute(
                update(ReviewTask)
                .where(ReviewTask.id == task_id, ReviewTask.status == "pending")
                .values(status="processing", error_code=None, error=None)
            )
            db.commit()
            return result.rowcount == 1

    async def _run_claimed(self, task_id: str) -> None:
        from app.services.command_service import CommandService

        self._revalidate_initiator(task_id)
        checker = getattr(self._gitea, "check_capabilities", None)
        if checker is not None:
            await checker()
        await self._assert_external_base(task_id)
        with self._session_factory() as db:
            task = db.get(ReviewTask, task_id)
            from app.services.diagram_revision import is_diagram_revision_task

            diagram_revision = is_diagram_revision_task(task)
        if diagram_revision:
            await self._diagram_revisions.materialize_and_review(task_id)
            await self._ensure_current_prototype(task_id)
            await self._publish_file(task_id)
            self._project_version(task_id)
            return
        request, session_id = self._command_request(task_id)
        commands = CommandService(
            self._session_factory,
            handlers={
                CommandAction.PUBLISH_REVIEW: PmRewriteService(
                    self._session_factory, self._agent
                ).as_command_handler()
            },
        )
        await commands.execute(session_id, request)
        await self._ensure_current_prototype(task_id)
        await self._publish_file(task_id)
        self._project_version(task_id)
        await self._publish_replies(task_id)

    async def _ensure_current_prototype(self, task_id: str) -> None:
        """Generate a review aid without making PRD publication depend on it."""

        with self._session_factory() as db:
            task = db.get(ReviewTask, task_id)
            item = db.get(WorkItem, task.wi) if task is not None else None
            project_id = item.project_id if item is not None else None
        if project_id is None:
            return
        if not self._prototypes.automatic_generation_allowed(project_id):
            return
        try:
            await self._prototypes.ensure_for_project(project_id)
        except Exception:
            # The prototype service records normal Agent/MCP failures. Keep
            # publication recoverable if an unexpected auxiliary error occurs.
            logger.exception(
                "Could not generate PRD HTML prototype during review publication",
                extra={"project_id": project_id, "review_task_id": task_id},
            )
            return

    async def _assert_external_base(self, task_id: str) -> None:
        """Reject a branch that drifted beyond the last durable task receipt."""

        from app.services.prd_review import _markdown_hash

        with self._session_factory() as db:
            task = db.get(ReviewTask, task_id)
            if task is None or task.status != "processing":
                raise _StaleReviewBase("review task is no longer processing")
            branch = self._review_branch(task.wi)
            expected_sha = task.new_commit_sha or task.base_commit_sha
            revised = (
                db.get(SpecVersion, task.new_spec_version_id)
                if task.new_spec_version_id is not None
                else None
            )
            unrecorded_path = (
                f"docs/prd/{task.wi}/v{task.new_version}.md"
                if revised is not None
                and task.new_version is not None
                and task.new_commit_sha is None
                else None
            )
        current = await self._gitea.ensure_branch(branch)
        if current.sha == expected_sha:
            return
        if revised is not None and unrecorded_path is not None:
            try:
                existing = await self._gitea.get_file(unrecorded_path, branch)
            except Exception as error:
                from app.services.gitea import GiteaError

                if not isinstance(error, GiteaError) or error.code != "GITEA_NOT_FOUND":
                    raise
            else:
                if (
                    existing.path == unrecorded_path
                    and _markdown_hash(existing.content) == _markdown_hash(revised.markdown)
                ):
                    self._record_commit(task_id, current.sha)
                    return
        raise _StaleReviewBase("Gitea review branch changed from the task base")

    def _command_request(self, task_id: str) -> tuple[SessionCommandRequest, str]:
        """Validate the frozen base before any Agent work and rebuild its command."""

        from app.services.prd_review import _markdown_hash

        with self._session_factory() as db:
            task = db.get(ReviewTask, task_id)
            item = db.get(WorkItem, task.wi) if task is not None else None
            project = db.get(Project, item.project_id) if item is not None else None
            base = db.get(PrdVersion, (task.wi, task.base_version)) if task else None
            parent = db.get(SpecVersion, base.spec_version_id) if base is not None else None
            current = (
                db.get(SpecVersion, project.current_spec_version_id)
                if project is not None and project.current_spec_version_id
                else None
            )
            revised = (
                db.get(SpecVersion, task.new_spec_version_id)
                if task is not None and task.new_spec_version_id
                else None
            )
            if (
                task is None
                or task.status != "processing"
                or item is None
                or item.kind != WorkItemKind.ROOT.value
                or item.parent_id is not None
                or project is None
                or base is None
                or parent is None
                or current is None
                or base.commit_sha != task.base_commit_sha
                or base.version != task.base_version
                or parent.revision != task.base_version
                or base.content_hash != _markdown_hash(parent.markdown)
                or parent.content_hash != base.spec_content_hash
                or not task.initiator_actor_id
            ):
                raise _StaleReviewBase("review task base is stale")
            if task.new_spec_version_id is None:
                if current.id != parent.id:
                    raise _StaleReviewBase("current Spec changed from the review base")
            elif (
                revised is None
                or current.id != revised.id
                or revised.parent_version_id != parent.id
            ):
                raise _StaleReviewBase("current Spec changed from the published revision")

            command_id = self._command_id(task.id)
            attempt = (
                db.query(CommandAttempt)
                .filter_by(session_id=project.session_id, command_id=command_id)
                .one_or_none()
            )
            expected_state_version = (
                attempt.expected_state_version
                if attempt is not None
                else project.state_version
            )
            return (
                SessionCommandRequest(
                    command_id=command_id,
                    action=CommandAction.PUBLISH_REVIEW,
                    expected_state_version=expected_state_version,
                    actor_id=task.initiator_actor_id,
                    payload={"task_id": task.id},
                ),
                project.session_id,
            )

    async def _publish_file(self, task_id: str) -> None:
        from app.services.prd_review import PrdContentConflict, _markdown_hash

        with self._session_factory() as db:
            task = db.get(ReviewTask, task_id)
            revised = (
                db.get(SpecVersion, task.new_spec_version_id)
                if task is not None and task.new_spec_version_id
                else None
            )
            if task is None or task.status != "processing" or revised is None:
                raise RuntimeError("review command did not record its Spec revision")
            path = f"docs/prd/{task.wi}/v{task.new_version}.md"
            branch = self._review_branch(task.wi)
            expected_hash = _markdown_hash(revised.markdown)
            recorded_commit = task.new_commit_sha
            markdown = revised.markdown
            wi = task.wi
            new_version = task.new_version
            base_commit_sha = task.base_commit_sha

        if recorded_commit is not None:
            existing = await self._gitea.get_file(path, recorded_commit)
            if existing.path != path or _markdown_hash(existing.content) != expected_hash:
                raise PrdContentConflict("published Gitea file conflicts with its task")
            return

        head = await self._gitea.ensure_branch(branch)
        try:
            existing = await self._gitea.get_file(path, branch)
        except Exception as error:
            from app.services.gitea import GiteaError

            if not isinstance(error, GiteaError) or error.code != "GITEA_NOT_FOUND":
                raise
            current = await self._gitea.ensure_branch(branch)
            if current.sha != base_commit_sha:
                raise _StaleReviewBase(
                    "Gitea review branch changed immediately before file publication"
                )
            commit_sha = (
                await self._gitea.put_file(
                    path,
                    markdown,
                    branch,
                    f"Publish PRD {wi} v{new_version}",
                )
            ).sha
        else:
            if existing.path != path or _markdown_hash(existing.content) != expected_hash:
                raise PrdContentConflict("Gitea PRD path already contains different content")
            commit_sha = head.sha
        self._record_commit(task_id, commit_sha)

    def _record_commit(self, task_id: str, commit_sha: str) -> None:
        with self._session_factory() as db:
            task = db.get(ReviewTask, task_id)
            if task is None or task.status != "processing":
                raise RuntimeError("review task is no longer processing")
            if task.new_commit_sha is not None and task.new_commit_sha != commit_sha:
                raise RuntimeError("review task has conflicting Gitea commit evidence")
            task.new_commit_sha = commit_sha
            db.commit()

    def _project_version(self, task_id: str) -> None:
        from app.services.prd_review import PrdContentConflict, _markdown_hash

        with self._session_factory() as db:
            with db.begin():
                task = db.get(ReviewTask, task_id)
                base = db.get(PrdVersion, (task.wi, task.base_version)) if task else None
                revised = (
                    db.get(SpecVersion, task.new_spec_version_id)
                    if task is not None and task.new_spec_version_id
                    else None
                )
                if (
                    task is None
                    or task.status != "processing"
                    or task.new_version is None
                    or task.new_commit_sha is None
                    or base is None
                    or revised is None
                ):
                    raise RuntimeError("review publication evidence is incomplete")
                expected = {
                    "spec_version_id": revised.id,
                    "filename": f"docs/prd/{task.wi}/v{task.new_version}.md",
                    "pr_number": base.pr_number,
                    "commit_sha": task.new_commit_sha,
                    "content_hash": _markdown_hash(revised.markdown),
                    "spec_content_hash": revised.content_hash,
                    "change_summary": revised.change_summary,
                }
                projection = db.get(PrdVersion, (task.wi, task.new_version))
                if projection is None:
                    db.add(
                        PrdVersion(
                            wi=task.wi,
                            version=task.new_version,
                            **expected,
                        )
                    )
                    db.flush()
                elif any(
                    getattr(projection, key) != value for key, value in expected.items()
                ):
                    raise PrdContentConflict("PRD version projection conflicts with publication")

    async def _publish_replies(self, task_id: str) -> None:
        with self._session_factory() as db:
            task = db.get(ReviewTask, task_id)
            item = db.get(WorkItem, task.wi) if task is not None else None
            project = db.get(Project, item.project_id) if item is not None else None
            revised = (
                db.get(SpecVersion, task.new_spec_version_id)
                if task is not None and task.new_spec_version_id
                else None
            )
            base = db.get(PrdVersion, (task.wi, task.base_version)) if task else None
            calls = (
                db.query(AgentCall)
                .filter_by(project_id=project.id, operation="rewrite_prd")
                .order_by(desc(AgentCall.started_at), desc(AgentCall.id))
                .all()
                if project is not None
                else []
            )
            call = next(
                (
                    candidate
                    for candidate in calls
                    if candidate.request.get("task_id") == task_id
                    and candidate.status in {"SUCCEEDED", "RESULT_READY"}
                ),
                None,
            )
            if (
                task is None
                or task.status != "processing"
                or revised is None
                or base is None
                or call is None
                or task.new_version is None
                or task.new_commit_sha is None
            ):
                raise RuntimeError("review reply evidence is incomplete")
            output = PrdRewriteOutput.model_validate(call.response)
            validate_rewrite(output, set(task.comment_ids))
            responses = {response.comment_id: response for response in output.responses}
            review_outcome = self._review_outcome(revised.status)
            pr_number = base.pr_number
            project_id = project.id
            session_id = project.session_id
            actor_id = task.initiator_actor_id
            new_version = task.new_version
            commit_sha = task.new_commit_sha
            comment_ids = list(task.comment_ids)
            receipts = dict(task.reply_receipts or {})

        identity = getattr(self._gitea, "service_identity", None)
        if identity is None:
            raise RuntimeError("Gitea service identity is unavailable")

        for comment_id in comment_ids:
            response = responses[comment_id]
            body = self._signed_reply_body(
                task_id,
                comment_id,
                response.action,
                response.note,
                new_version,
                commit_sha,
                review_outcome,
            )
            threads = await self._gitea.list_comment_threads(pr_number)
            thread = next(
                (thread for thread in threads if thread.comment.id == comment_id), None
            )
            if thread is None:
                raise RuntimeError(f"frozen Gitea comment {comment_id} is missing")
            verified = [
                reply
                for reply in thread.replies
                if reply.user_id == identity.id
                and reply.body == body
                and self._reviews._valid_receipt_body(body, task_id, comment_id)
            ]
            stored = receipts.get(str(comment_id))
            if stored is not None:
                match = next(
                    (
                        reply
                        for reply in verified
                        if reply.id == stored.get("reply_id")
                        and stored.get("user_id") == identity.id
                        and stored.get("body") == body
                    ),
                    None,
                )
                if match is None:
                    raise RuntimeError(
                        f"stored reply receipt for comment {comment_id} no longer matches Gitea"
                    )
                self._record_reply_evidence(
                    project_id,
                    session_id,
                    actor_id,
                    task_id,
                    comment_id,
                    match,
                    body,
                    new_version,
                    commit_sha,
                )
                continue
            if verified:
                reply = verified[0]
            else:
                reply = await self._gitea.reply_comment(pr_number, comment_id, body)
                if (
                    reply is None
                    or reply.user_id != identity.id
                    or reply.body != body
                ):
                    raise RuntimeError("Gitea reply receipt does not match the service identity")
            self._record_reply_evidence(
                project_id,
                session_id,
                actor_id,
                task_id,
                comment_id,
                reply,
                body,
                new_version,
                commit_sha,
            )

    def _record_reply_evidence(
        self,
        project_id: str,
        session_id: str,
        actor_id: str,
        task_id: str,
        comment_id: int,
        reply: GiteaReply,
        body: str,
        new_version: int,
        commit_sha: str,
    ) -> None:
        with self._session_factory() as db:
            task = db.get(ReviewTask, task_id)
            if task is None or task.status != "processing":
                raise RuntimeError("review task is no longer processing")
            receipts = dict(task.reply_receipts or {})
            receipt = {
                "reply_id": reply.id,
                "user_id": reply.user_id,
                "body": body,
            }
            previous = receipts.get(str(comment_id))
            if previous is not None and previous != receipt:
                raise RuntimeError("review task has conflicting reply evidence")
            receipts[str(comment_id)] = receipt
            task.reply_receipts = receipts
            existing = next(
                (
                    event
                    for event in db.query(AuditEvent)
                    .filter_by(
                        project_id=project_id,
                        event_type="REVIEW_COMMENT_REPLIED",
                    )
                    .all()
                    if event.payload.get("review_task_id") == task_id
                    and event.payload.get("comment_id") == comment_id
                ),
                None,
            )
            if existing is None:
                db.add(
                    AuditEvent(
                        id=_new_service_id(),
                        project_id=project_id,
                        session_id=session_id,
                        event_type="REVIEW_COMMENT_REPLIED",
                        actor_id=actor_id,
                        payload={
                            "review_task_id": task_id,
                            "comment_id": comment_id,
                            "reply_id": reply.id,
                            "reply_user_id": reply.user_id,
                            "reply_body_hash": hashlib.sha256(body.encode("utf-8")).hexdigest(),
                            "new_version": new_version,
                            "new_commit_sha": commit_sha,
                        },
                    )
                )
            db.commit()

    def _complete(self, task_id: str) -> None:
        with self._session_factory() as db:
            task = db.get(ReviewTask, task_id)
            item = db.get(WorkItem, task.wi) if task is not None else None
            project = db.get(Project, item.project_id) if item is not None else None
            result = db.execute(
                update(ReviewTask)
                .where(ReviewTask.id == task_id, ReviewTask.status == "processing")
                .values(status="done", error_code=None, error=None)
            )
            if result.rowcount != 1 or project is None:
                db.rollback()
                raise RuntimeError("review task completion lost its processing claim")
            db.add(
                AuditEvent(
                    id=_new_service_id(),
                    project_id=project.id,
                    session_id=project.session_id,
                    event_type="REVIEW_PUBLICATION_COMPLETED",
                    actor_id=task.initiator_actor_id,
                    payload={
                        "review_task_id": task_id,
                        "new_version": task.new_version,
                        "new_commit_sha": task.new_commit_sha,
                    },
                )
            )
            db.commit()

    def _fail(self, task_id: str, error_code: str, detail: str) -> None:
        with self._session_factory() as db:
            db.execute(
                update(ReviewTask)
                .where(ReviewTask.id == task_id, ReviewTask.status == "processing")
                .values(status="error", error_code=error_code, error=detail)
            )
            db.commit()

    @staticmethod
    def _error_code(error: Exception) -> str:
        from app.services.command_service import (
            CommandHandlerFailure,
            CommandHandlerRejected,
            StaleState,
        )
        from app.services.gitea import GiteaError
        from app.services.prd_review import PrdContentConflict

        if isinstance(error, (_StaleReviewBase, StaleState)):
            return "STALE_REVIEW_BASE"
        if isinstance(error, GiteaError):
            return error.code
        if isinstance(error, PrdContentConflict):
            return "PRD_CONTENT_CONFLICT"
        if isinstance(error, CommandHandlerRejected) and str(error) == _AUTO_REVIEW_NO_CHANGE:
            return _AUTO_REVIEW_NO_CHANGE
        if isinstance(error, CommandHandlerFailure):
            return "PM_REWRITE_FAILED"
        return "REVIEW_PUBLISH_FAILED"

    @staticmethod
    def _command_id(task_id: str) -> str:
        return f"publish-review:{task_id}"

    def _revalidate_initiator(self, task_id: str) -> None:
        from app.services.prd_review import PrdReviewService

        with self._session_factory() as db:
            task = db.get(ReviewTask, task_id)
            item = db.get(WorkItem, task.wi) if task is not None else None
            if (
                task is None
                or item is None
                or item.kind != WorkItemKind.ROOT.value
                or item.parent_id is not None
            ):
                raise _StaleReviewBase("review task root is unavailable")
            project = db.get(Project, item.project_id)
            if project is None:
                raise _StaleReviewBase("review task project is unavailable")
            PrdReviewService.assert_reviewer(project, task.initiator_actor_id)

    def _review_branch(self, wi: str) -> str:
        builder = getattr(self._gitea, "review_branch", None)
        return str(builder(wi)) if builder is not None else f"prd-review/{wi}"

    @staticmethod
    def _review_outcome(status: str) -> str:
        return {
            "HUMAN_REVIEW": "自动审核已通过，待人工评审",
            "REWORK": "未通过自动审核，需返工",
            "NEED_CLARIFICATION": "自动审核需要补充信息，待确认",
        }.get(status, f"自动审核状态：{status}")

    @staticmethod
    def _reply_body(
        action: RewriteAction,
        note: str,
        version: int,
        commit_sha: str,
        review_outcome: str,
    ) -> str:
        prefix = {
            RewriteAction.MODIFIED: "已修改",
            RewriteAction.CLARIFIED: "已澄清",
            RewriteAction.NOT_ACCEPTED: "未采纳",
            RewriteAction.NEEDS_HUMAN_CONFIRMATION: "待确认",
        }[action]
        return (
            f"{prefix}：{action.value} — {note}；见 v{version}（commit {commit_sha}）。"
            f"审核结果：{review_outcome}。"
        )

    def _signed_reply_body(
        self,
        task_id: str,
        comment_id: int,
        action: RewriteAction,
        note: str,
        version: int,
        commit_sha: str,
        review_outcome: str,
    ) -> str:
        visible = self._reply_body(
            action, note, version, commit_sha, review_outcome
        )
        signer = getattr(self._gitea, "sign_receipt", None)
        if signer is None:
            raise RuntimeError("Gitea reply signing is unavailable")
        signature = signer(f"{task_id}\n{comment_id}\n{visible}")
        return (
            f"{visible}\n<!-- firstflight-receipt:v1:{task_id}:"
            f"{comment_id}:{signature} -->"
        )
