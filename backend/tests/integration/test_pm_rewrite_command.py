"""Two-phase publication of a frozen Gitea review into an immutable Spec revision."""

from collections import deque
from datetime import UTC, datetime, timedelta
import hashlib

import pytest

from app.database.models import (
    AgentCall,
    Artifact,
    AuditEvent,
    ClarificationResponse,
    CommandAttempt,
    PrdVersion,
    ProcessedCommand,
    Project,
    ReviewTask,
    SpecReview,
    SpecVersion,
    WorkItem,
)
from app.domain.types import (
    CommandAction,
    PrdRewriteOutput,
    ProjectPhase,
    ReviewKind,
    RewriteAction,
    SpecStatus,
    WorkItemKind,
)
from app.schemas.workflow import SessionCommandRequest
from app.services.command_service import (
    CommandHandlerFailure,
    CommandHandlerRejected,
    CommandService,
    ForbiddenActor,
    StaleState,
    _canonical_hash,
)
from app.services.pm_agent import (
    PmRewriteService,
    ReviewCommentSnapshot,
    ReviewSnapshot,
    snapshot_hash,
)
from tests.helpers.fake_agent import ScriptedAgentGateway


def _rewrite(valid_spec, *, summary="Applied the frozen Gitea review."):
    return PrdRewriteOutput(
        spec=valid_spec.model_copy(
            update={"main_flows": ["Submit, review, publish, and decompose the specification"]}
        ),
        responses=[
            {
                "comment_id": 101,
                "action": RewriteAction.MODIFIED,
                "note": "Added the requested publication step.",
            }
        ],
        change_summary=summary,
    )


def _add_review_task(db, complete_brief, valid_spec, *, status=SpecStatus.HUMAN_REVIEW):
    project = Project(
        id="rewrite-project",
        session_id="rewrite-session",
        creation_request_id="rewrite-request",
        brief=complete_brief.model_dump(mode="json"),
        final_approver="owner",
        project_manager_ids=["pm"],
        root_owner_ids=["root-owner"],
        phase=ProjectPhase.REVIEW.value,
        state_version=4,
        current_spec_version_id="rewrite-spec-1",
    )
    comment = ReviewCommentSnapshot(
        id=101,
        path="docs/prd/root-1/v1.md",
        line=12,
        body="Show the publication step.",
        user="reviewer",
        created_at="2026-09-01T08:00:00Z",
        resolved=False,
        replies=(),
    )
    snapshot = ReviewSnapshot(base_commit_sha="b" * 40, comments=(comment,))
    comment_data = {
        "id": comment.id,
        "path": comment.path,
        "line": comment.line,
        "body": comment.body,
        "user": comment.user,
        "created_at": comment.created_at,
        "resolved": comment.resolved,
        "replies": [],
    }
    db.add_all(
        [
            project,
            SpecVersion(
                id="rewrite-spec-1",
                project_id=project.id,
                revision=1,
                content=valid_spec.model_dump(mode="json"),
                markdown="# Project Spec\n",
                generation_source="PM_AGENT",
                input_refs=["artifact:rewrite-brief"],
                generator_agent_session_id="old-pm",
                generator_call_id="old-call",
                parent_version_id=None,
                change_summary="Initial specification",
                content_hash="a" * 64,
                status=status.value,
            ),
            WorkItem(
                id="root-1",
                project_id=project.id,
                local_key="ROOT",
                kind=WorkItemKind.ROOT.value,
                parent_id=None,
                executable=False,
            ),
            PrdVersion(
                wi="root-1",
                version=1,
                spec_version_id="rewrite-spec-1",
                filename="docs/prd/root-1/v1.md",
                pr_number=7,
                commit_sha=snapshot.base_commit_sha,
                content_hash=hashlib.sha256(b"# Project Spec\n").hexdigest(),
                spec_content_hash="a" * 64,
                change_summary="Initial specification",
            ),
            ReviewTask(
                id="review-task-1",
                wi="root-1",
                initiator_actor_id="owner",
                status="processing",
                base_version=1,
                base_commit_sha=snapshot.base_commit_sha,
                comment_ids=[101],
                comment_snapshot=[comment_data],
                comment_snapshot_hash=snapshot_hash(snapshot),
                reply_receipts={},
            ),
            Artifact(
                id="rewrite-brief",
                project_id=project.id,
                kind="ORIGINAL_REQUIREMENT",
                content=complete_brief.model_dump(mode="json"),
                content_hash="c" * 64,
                source_actor_id="owner",
            ),
        ]
    )
    db.commit()
    return project


def _request(**updates):
    request = SessionCommandRequest(
        command_id="publish-review-1",
        action=CommandAction.PUBLISH_REVIEW,
        expected_state_version=4,
        actor_id="owner",
        payload={"task_id": "review-task-1"},
    )
    return request.model_copy(update=updates)


async def _start_failed_reviewer_attempt(
    session_factory,
    db_session,
    complete_brief,
    valid_spec,
    *later_review_results,
):
    project = _add_review_task(db_session, complete_brief, valid_spec)
    agent = ScriptedAgentGateway(
        rewrite_results=deque([_rewrite(valid_spec)]),
        review_results=deque(
            [RuntimeError("initial reviewer unavailable"), *later_review_results]
        ),
    )
    service = CommandService(
        session_factory,
        handlers={
            CommandAction.PUBLISH_REVIEW: PmRewriteService(
                session_factory, agent
            ).as_command_handler()
        },
    )
    with pytest.raises(RuntimeError, match="initial reviewer unavailable"):
        await service.execute(project.session_id, _request())
    return project, agent, service


@pytest.mark.asyncio
async def test_publish_review_materializes_one_external_revision_and_replays_receipt(
    session_factory,
    db_session,
    complete_brief,
    valid_spec,
    passing_semantic_review,
):
    """Dropping CAS, receipts, provenance, or task refs would publish an unauditable revision."""
    project = _add_review_task(db_session, complete_brief, valid_spec)
    agent = ScriptedAgentGateway(
        rewrite_results=deque([_rewrite(valid_spec)]),
        review_results=deque([passing_semantic_review]),
    )
    handler = PmRewriteService(session_factory, agent).as_command_handler()
    service = CommandService(
        session_factory, handlers={CommandAction.PUBLISH_REVIEW: handler}
    )

    first = await service.execute(project.session_id, _request())
    replay = await service.execute(project.session_id, _request())

    assert replay == first
    assert first.state.current_spec_status is SpecStatus.HUMAN_REVIEW
    assert [operation for operation, _ in agent.calls] == ["rewrite_prd", "review_spec"]
    with session_factory() as db:
        current = db.get(Project, project.id)
        old = db.get(SpecVersion, "rewrite-spec-1")
        revised = db.get(SpecVersion, current.current_spec_version_id)
        task = db.get(ReviewTask, "review-task-1")
        rewrite_call = db.query(AgentCall).filter_by(operation="rewrite_prd").one()
        reviews = db.query(SpecReview).filter_by(spec_version_id=revised.id).all()
        assert db.query(SpecVersion).filter_by(project_id=project.id).count() == 2
        assert old.status == SpecStatus.HUMAN_REVIEW.value
        assert revised.revision == 2
        assert revised.parent_version_id == old.id
        assert revised.generation_source == "GITEA_REVIEW_COMMENTS"
        assert revised.generator_call_id == rewrite_call.id
        assert revised.change_summary == "Applied the frozen Gitea review."
        assert {review.kind for review in reviews} == {
            ReviewKind.RULE.value,
            ReviewKind.AGENT.value,
        }
        assert all(review.input_spec_hash == revised.content_hash for review in reviews)
        assert task.new_spec_version_id == revised.id
        assert task.new_version == 2
        assert rewrite_call.request["command_id"] == _request().command_id
        assert rewrite_call.request["base_spec_version_id"] == old.id
        assert rewrite_call.status == "SUCCEEDED"
        assert db.query(ProcessedCommand).filter_by(command_id=_request().command_id).count() == 1


@pytest.mark.asyncio
async def test_publish_review_uses_the_same_rule_merge_policy_for_rework(
    session_factory, db_session, complete_brief, valid_spec
):
    """Bypassing structural review would expose an invalid rewrite for human approval."""
    project = _add_review_task(db_session, complete_brief, valid_spec)
    duplicate = valid_spec.functional_requirements[0].model_copy()
    invalid = valid_spec.model_copy(
        update={
            "functional_requirements": [duplicate, duplicate],
            "main_flows": ["Submit, review, and publish the specification"],
        }
    )
    agent = ScriptedAgentGateway(
        rewrite_results=deque([
            PrdRewriteOutput(
                spec=invalid,
                responses=[
                    {
                        "comment_id": 101,
                        "action": RewriteAction.MODIFIED,
                        "note": "Applied the requested flow change.",
                    }
                ],
                change_summary="Applied the review with a structural conflict.",
            )
        ])
    )
    service = CommandService(
        session_factory,
        handlers={
            CommandAction.PUBLISH_REVIEW: PmRewriteService(
                session_factory, agent
            ).as_command_handler()
        },
    )

    result = await service.execute(project.session_id, _request())

    assert result.state.current_spec_status is SpecStatus.REWORK
    assert [operation for operation, _ in agent.calls] == ["rewrite_prd"]
    with session_factory() as db:
        revised = db.get(SpecVersion, result.state.current_spec_version_id)
        receipts = db.query(SpecReview).filter_by(spec_version_id=revised.id).all()
        assert {receipt.kind for receipt in receipts} == {
            ReviewKind.RULE.value,
            ReviewKind.AGENT.value,
        }
        assert next(
            receipt for receipt in receipts if receipt.kind == ReviewKind.AGENT.value
        ).verdict == "SKIPPED"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("command_request", "error_type"),
    [
        (_request(actor_id="outsider"), ForbiddenActor),
        (_request(command_id="stale-publish", expected_state_version=3), StaleState),
    ],
)
async def test_publish_review_rejects_unauthorized_or_stale_commands_before_rewrite(
    session_factory,
    db_session,
    complete_brief,
    valid_spec,
    command_request,
    error_type,
):
    """Checking authority or CAS after rewrite would leak Agent work for an invalid command."""
    project = _add_review_task(db_session, complete_brief, valid_spec)
    agent = ScriptedAgentGateway(rewrite_results=deque([_rewrite(valid_spec)]))
    service = CommandService(
        session_factory,
        handlers={
            CommandAction.PUBLISH_REVIEW: PmRewriteService(
                session_factory, agent
            ).as_command_handler()
        },
    )

    with pytest.raises(error_type):
        await service.execute(project.session_id, command_request)

    assert agent.calls == []
    with session_factory() as db:
        assert db.query(AgentCall).filter_by(operation="rewrite_prd").count() == 0
        assert db.query(ProcessedCommand).count() == 0


@pytest.mark.asyncio
async def test_reviewer_retry_reuses_the_saved_rewrite_result(
    session_factory,
    db_session,
    complete_brief,
    valid_spec,
    passing_semantic_review,
):
    """Repeating rewrite after a semantic-review outage could publish a different proposal."""
    project = _add_review_task(db_session, complete_brief, valid_spec)
    agent = ScriptedAgentGateway(
        rewrite_results=deque([_rewrite(valid_spec)]),
        review_results=deque([RuntimeError("reviewer unavailable"), passing_semantic_review]),
    )
    service = CommandService(
        session_factory,
        handlers={
            CommandAction.PUBLISH_REVIEW: PmRewriteService(
                session_factory, agent
            ).as_command_handler()
        },
    )

    with pytest.raises(RuntimeError, match="reviewer unavailable"):
        await service.execute(project.session_id, _request())

    with session_factory() as db:
        rewrite_call = db.query(AgentCall).filter_by(operation="rewrite_prd").one()
        reviewer_call = db.query(AgentCall).filter_by(operation="review_spec").one()
        attempt = db.query(CommandAttempt).filter_by(
            command_id=_request().command_id
        ).one()
        audit = db.query(AuditEvent).filter_by(
            event_type="COMMAND_HANDLER_FAILED"
        ).one()
        task = db.get(ReviewTask, "review-task-1")
        assert rewrite_call.status == "RESULT_READY"
        assert reviewer_call.status == "FAILED"
        assert attempt.status == "FAILED"
        assert attempt.agent_call_ids == [rewrite_call.id, reviewer_call.id]
        assert audit.payload["agent_call_ids"] == [rewrite_call.id, reviewer_call.id]
        assert db.query(ProcessedCommand).filter_by(
            command_id=_request().command_id
        ).count() == 0
        assert db.query(SpecVersion).filter_by(project_id=project.id).count() == 1
        assert task.new_spec_version_id is None
        assert task.new_version is None
        db.add_all(
            [
                Artifact(
                    id="late-rewrite-artifact",
                    project_id=project.id,
                    kind="REFERENCE",
                    content={"late": True},
                    content_hash="d" * 64,
                    source_actor_id="owner",
                ),
                ClarificationResponse(
                    id="late-rewrite-clarification",
                    project_id=project.id,
                    clarification_request_id="late-request",
                    actor_id="owner",
                    answers={"message": "Ancillary evidence changed."},
                ),
            ]
        )
        db.commit()

    result = await service.execute(project.session_id, _request())

    assert result.state.current_spec_status is SpecStatus.HUMAN_REVIEW
    assert [operation for operation, _ in agent.calls] == [
        "rewrite_prd",
        "review_spec",
        "review_spec",
    ]
    with session_factory() as db:
        assert db.query(AgentCall).filter_by(operation="rewrite_prd").count() == 1
        assert db.query(AgentCall).filter_by(operation="review_spec", status="FAILED").count() == 1
        assert db.query(CommandAttempt).filter_by(command_id=_request().command_id).one().status == "MATERIALIZED"


@pytest.mark.parametrize("candidate_status", ["RESULT_READY", "PENDING", "AMBIGUOUS"])
@pytest.mark.asyncio
async def test_reviewer_retry_fails_closed_on_near_match_binding_evidence(
    session_factory,
    db_session,
    complete_brief,
    valid_spec,
    passing_semantic_review,
    candidate_status,
):
    """A command-bound reviewer receipt with a changed Spec binding is not absence of evidence."""
    project, agent, service = await _start_failed_reviewer_attempt(
        session_factory,
        db_session,
        complete_brief,
        valid_spec,
        passing_semantic_review,
    )
    with session_factory() as db:
        rewrite_call = db.query(AgentCall).filter_by(operation="rewrite_prd").one()
        candidate = db.query(AgentCall).filter_by(operation="review_spec").one()
        candidate.status = candidate_status
        candidate.response = (
            passing_semantic_review.model_dump(mode="json")
            if candidate_status == "RESULT_READY"
            else None
        )
        candidate.error = None
        candidate.request = {**candidate.request, "spec_hash": "f" * 64}
        rewrite_call_id = rewrite_call.id
        candidate_id = candidate.id
        db.commit()

    with pytest.raises(
        CommandHandlerFailure, match="semantic review result is unavailable"
    ) as captured:
        await service.execute(project.session_id, _request())

    assert captured.value.agent_call_ids == [rewrite_call_id, candidate_id]
    assert [operation for operation, _ in agent.calls] == [
        "rewrite_prd",
        "review_spec",
    ]
    with session_factory() as db:
        attempt = db.query(CommandAttempt).filter_by(
            command_id=_request().command_id
        ).one()
        assert attempt.status == "FAILED"
        assert attempt.agent_call_ids == [rewrite_call_id, candidate_id]
        assert db.query(AgentCall).filter_by(operation="review_spec").count() == 1


@pytest.mark.parametrize("unresolved_status", ["PENDING", "AMBIGUOUS"])
@pytest.mark.asyncio
async def test_reviewer_retry_fails_closed_when_newer_failure_hides_unresolved_exact_call(
    session_factory,
    db_session,
    complete_brief,
    valid_spec,
    passing_semantic_review,
    unresolved_status,
):
    """A later resolved failure cannot erase older in-doubt external work."""
    project, agent, service = await _start_failed_reviewer_attempt(
        session_factory,
        db_session,
        complete_brief,
        valid_spec,
        passing_semantic_review,
    )
    with session_factory() as db:
        rewrite_call = db.query(AgentCall).filter_by(operation="rewrite_prd").one()
        unresolved = db.query(AgentCall).filter_by(operation="review_spec").one()
        unresolved.status = unresolved_status
        unresolved.response = None
        unresolved.error = None
        unresolved.started_at = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)
        newer_failed = AgentCall(
            id=f"newer-failed-{unresolved_status.lower()}",
            project_id=project.id,
            agent_session_id="newer-failed-reviewer-session",
            operation="review_spec",
            request=dict(unresolved.request),
            status="FAILED",
            error="later reviewer failure",
            started_at=unresolved.started_at + timedelta(minutes=1),
        )
        db.add(newer_failed)
        rewrite_call_id = rewrite_call.id
        unresolved_id = unresolved.id
        db.commit()

    with pytest.raises(
        CommandHandlerFailure, match="semantic review result is unavailable"
    ) as captured:
        await service.execute(project.session_id, _request())

    assert captured.value.agent_call_ids == [rewrite_call_id, unresolved_id]
    assert [operation for operation, _ in agent.calls] == [
        "rewrite_prd",
        "review_spec",
    ]
    with session_factory() as db:
        attempt = db.query(CommandAttempt).filter_by(
            command_id=_request().command_id
        ).one()
        assert attempt.agent_call_ids == [rewrite_call_id, unresolved_id]
        assert db.query(AgentCall).filter_by(operation="review_spec").count() == 2


@pytest.mark.asyncio
async def test_reviewer_retry_reuses_older_ready_result_despite_newer_failure(
    session_factory,
    db_session,
    complete_brief,
    valid_spec,
    passing_semantic_review,
):
    """A later failed attempt cannot hide the newest reusable durable result."""
    project, agent, service = await _start_failed_reviewer_attempt(
        session_factory,
        db_session,
        complete_brief,
        valid_spec,
        passing_semantic_review,
    )
    with session_factory() as db:
        rewrite_call = db.query(AgentCall).filter_by(operation="rewrite_prd").one()
        ready = db.query(AgentCall).filter_by(operation="review_spec").one()
        ready.status = "RESULT_READY"
        ready.response = passing_semantic_review.model_dump(mode="json")
        ready.error = None
        ready.started_at = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)
        newer_failed = AgentCall(
            id="newer-failed-after-ready",
            project_id=project.id,
            agent_session_id="newer-failed-reviewer-session",
            operation="review_spec",
            request=dict(ready.request),
            status="FAILED",
            error="later reviewer failure",
            started_at=ready.started_at + timedelta(minutes=1),
        )
        db.add(newer_failed)
        rewrite_call_id = rewrite_call.id
        ready_id = ready.id
        ready_session_id = ready.agent_session_id
        newer_failed_id = newer_failed.id
        db.commit()

    result = await service.execute(project.session_id, _request())

    assert result.state.current_spec_status is SpecStatus.HUMAN_REVIEW
    assert [operation for operation, _ in agent.calls] == [
        "rewrite_prd",
        "review_spec",
    ]
    with session_factory() as db:
        attempt = db.query(CommandAttempt).filter_by(
            command_id=_request().command_id
        ).one()
        agent_review = db.query(SpecReview).filter_by(kind=ReviewKind.AGENT.value).one()
        assert attempt.agent_call_ids == [rewrite_call_id, ready_id]
        assert db.get(AgentCall, ready_id).status == "SUCCEEDED"
        assert db.get(AgentCall, newer_failed_id).status == "FAILED"
        assert agent_review.reviewer_id == ready_session_id
        assert db.query(AgentCall).filter_by(operation="review_spec").count() == 2


@pytest.mark.asyncio
async def test_reviewer_retry_fails_closed_on_malformed_ready_result(
    session_factory,
    db_session,
    complete_brief,
    valid_spec,
):
    """Malformed durable reviewer evidence must not be ignored or sent to materialization."""
    project, agent, service = await _start_failed_reviewer_attempt(
        session_factory,
        db_session,
        complete_brief,
        valid_spec,
    )
    with session_factory() as db:
        rewrite_call = db.query(AgentCall).filter_by(operation="rewrite_prd").one()
        malformed = db.query(AgentCall).filter_by(operation="review_spec").one()
        malformed.status = "RESULT_READY"
        malformed.response = {"verdict": "PASS", "findings": "not-an-array"}
        malformed.error = None
        rewrite_call_id = rewrite_call.id
        malformed_id = malformed.id
        db.commit()

    with pytest.raises(
        CommandHandlerFailure, match="semantic review result is unavailable"
    ) as captured:
        await service.execute(project.session_id, _request())

    assert captured.value.agent_call_ids == [rewrite_call_id, malformed_id]
    assert [operation for operation, _ in agent.calls] == [
        "rewrite_prd",
        "review_spec",
    ]
    with session_factory() as db:
        attempt = db.query(CommandAttempt).filter_by(
            command_id=_request().command_id
        ).one()
        assert attempt.agent_call_ids == [rewrite_call_id, malformed_id]
        assert db.query(AgentCall).filter_by(operation="review_spec").count() == 1


@pytest.mark.asyncio
async def test_reviewer_retry_rejects_a_tampered_saved_rewrite_binding_without_rewrite(
    session_factory, db_session, complete_brief, valid_spec
):
    """A command-bound result must not be reused if its frozen base binding was altered."""
    project = _add_review_task(db_session, complete_brief, valid_spec)
    agent = ScriptedAgentGateway(
        rewrite_results=deque([_rewrite(valid_spec)]),
        review_results=deque([RuntimeError("reviewer unavailable")]),
    )
    service = CommandService(
        session_factory,
        handlers={
            CommandAction.PUBLISH_REVIEW: PmRewriteService(
                session_factory, agent
            ).as_command_handler()
        },
    )
    with pytest.raises(RuntimeError, match="reviewer unavailable"):
        await service.execute(project.session_id, _request())
    with session_factory() as db:
        rewrite_call = db.query(AgentCall).filter_by(operation="rewrite_prd").one()
        rewrite_call.request = {
            **rewrite_call.request,
            "base_commit_sha": "e" * 40,
        }
        db.commit()

    with pytest.raises(CommandHandlerRejected, match="binding"):
        await service.execute(project.session_id, _request())

    assert [operation for operation, _ in agent.calls] == [
        "rewrite_prd",
        "review_spec",
    ]
    with session_factory() as db:
        rewrite_call = db.query(AgentCall).filter_by(operation="rewrite_prd").one()
        rejected = db.query(AuditEvent).filter_by(
            event_type="COMMAND_HANDLER_REJECTED"
        ).one()
        assert rejected.payload["agent_call_ids"] == [rewrite_call.id]
        assert db.query(AgentCall).filter_by(operation="rewrite_prd").count() == 1


@pytest.mark.asyncio
async def test_publish_review_cas_loser_never_creates_a_revision_or_updates_the_task(
    session_factory,
    db_session,
    complete_brief,
    valid_spec,
    passing_semantic_review,
    monkeypatch,
):
    """A concurrent human winner must make a prepared rewrite stale before materialization."""
    project = _add_review_task(db_session, complete_brief, valid_spec)
    agent = ScriptedAgentGateway(
        rewrite_results=deque([_rewrite(valid_spec)]),
        review_results=deque([passing_semantic_review]),
    )
    loser = CommandService(
        session_factory,
        handlers={
            CommandAction.PUBLISH_REVIEW: PmRewriteService(
                session_factory, agent
            ).as_command_handler()
        },
    )
    winner = CommandService(session_factory)
    approval = SessionCommandRequest(
        command_id="concurrent-approval",
        action=CommandAction.APPROVE,
        expected_state_version=4,
        actor_id="owner",
    )

    def approve_before_rewrite_cas(session_id, command_id):
        winner._execute_human_review(
            session_id,
            approval,
            _canonical_hash(approval.model_dump(mode="json")),
        )

    monkeypatch.setattr(loser, "_before_materialization_cas", approve_before_rewrite_cas)

    with pytest.raises(StaleState):
        await loser.execute(project.session_id, _request())

    with session_factory() as db:
        current = db.get(Project, project.id)
        task = db.get(ReviewTask, "review-task-1")
        assert current.current_spec_version_id == "rewrite-spec-1"
        assert db.get(SpecVersion, "rewrite-spec-1").status == SpecStatus.APPROVED.value
        assert db.query(SpecVersion).filter_by(project_id=project.id).count() == 1
        assert task.new_spec_version_id is None
        assert task.new_version is None
        assert db.query(ProcessedCommand).filter_by(command_id=approval.command_id).count() == 1


@pytest.mark.asyncio
async def test_agent_failure_is_durable_without_a_revision_or_receipt(
    session_factory, db_session, complete_brief, valid_spec
):
    project = _add_review_task(db_session, complete_brief, valid_spec)
    agent = ScriptedAgentGateway(
        rewrite_results=deque([RuntimeError("rewrite unavailable")])
    )
    service = CommandService(
        session_factory,
        handlers={
            CommandAction.PUBLISH_REVIEW: PmRewriteService(
                session_factory, agent
            ).as_command_handler()
        },
    )

    with pytest.raises(RuntimeError, match="rewrite unavailable"):
        await service.execute(project.session_id, _request())

    with session_factory() as db:
        failed = db.query(AgentCall).filter_by(operation="rewrite_prd").one()
        assert failed.status == "FAILED"
        assert failed.request["command_id"] == _request().command_id
        assert db.query(SpecVersion).filter_by(project_id=project.id).count() == 1
        assert db.query(ProcessedCommand).count() == 0
        audit = db.query(AuditEvent).filter_by(event_type="COMMAND_HANDLER_FAILED").one()
        assert audit.payload["agent_call_ids"] == [failed.id]


@pytest.mark.asyncio
async def test_publish_review_rejects_invented_comment_coverage_with_saved_evidence(
    session_factory, db_session, complete_brief, valid_spec
):
    project = _add_review_task(db_session, complete_brief, valid_spec)
    invented = PrdRewriteOutput(
        spec=_rewrite(valid_spec).spec,
        responses=[
            {
                "comment_id": 999,
                "action": RewriteAction.MODIFIED,
                "note": "Invented an unrelated response.",
            }
        ],
        change_summary="Applied an invented response.",
    )
    agent = ScriptedAgentGateway(rewrite_results=deque([invented]))
    service = CommandService(
        session_factory,
        handlers={
            CommandAction.PUBLISH_REVIEW: PmRewriteService(
                session_factory, agent
            ).as_command_handler()
        },
    )

    with pytest.raises(CommandHandlerRejected, match="exactly cover"):
        await service.execute(project.session_id, _request())

    with session_factory() as db:
        call = db.query(AgentCall).filter_by(operation="rewrite_prd").one()
        assert call.status == "RESULT_READY"
        assert db.query(CommandAttempt).filter_by(command_id=_request().command_id).one().status == "REJECTED"
        assert db.query(SpecVersion).filter_by(project_id=project.id).count() == 1
        assert db.query(ProcessedCommand).count() == 0


@pytest.mark.asyncio
async def test_publish_review_detects_snapshot_tampering_before_rewrite(
    session_factory, db_session, complete_brief, valid_spec
):
    project = _add_review_task(db_session, complete_brief, valid_spec)
    task = db_session.get(ReviewTask, "review-task-1")
    changed = list(task.comment_snapshot)
    changed[0] = {**changed[0], "body": "Changed after freezing."}
    task.comment_snapshot = changed
    db_session.commit()
    agent = ScriptedAgentGateway(rewrite_results=deque([_rewrite(valid_spec)]))
    service = CommandService(
        session_factory,
        handlers={
            CommandAction.PUBLISH_REVIEW: PmRewriteService(
                session_factory, agent
            ).as_command_handler()
        },
    )

    with pytest.raises(ValueError, match="snapshot"):
        await service.execute(project.session_id, _request())

    assert agent.calls == []
    with session_factory() as db:
        assert db.query(AgentCall).filter_by(operation="rewrite_prd").count() == 0

