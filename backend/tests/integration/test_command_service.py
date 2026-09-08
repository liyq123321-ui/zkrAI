"""Integration coverage for durable, human-authorized workflow commands."""

import asyncio
from threading import Barrier, Lock, Thread

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.database.database import Base, create_engine_for_url, make_session_factory
from app.database.models import AgentCall, AgentSession, AuditEvent, CommandAttempt, ProcessedCommand, Project, SpecReview, SpecVersion, WorkItem
from app.domain.types import CommandAction, ProjectPhase, ReviewKind, ReviewVerdict, SpecStatus
from app.schemas.workflow import SessionCommandRequest
from app.services.command_service import (
    CommandConflict,
    CommandHandlerFailure,
    CommandHandlerResult,
    CommandInDoubt,
    CallableTwoPhaseHandler,
    CommandService,
    ForbiddenActor,
    IllegalAction,
    PreparedCommand,
    StaleState,
    _canonical_hash,
)


def _staged(materialize, prepare=None):
    async def default_prepare(context):
        return PreparedCommand(payload={})

    return CallableTwoPhaseHandler(prepare or default_prepare, materialize)


@pytest.fixture
def human_review_project(db_session, complete_brief, valid_spec):
    """Create the persisted authority data which commands must consult."""
    project = Project(
        id="project-review-1",
        session_id="session-review-1",
        creation_request_id="request-review-1",
        brief=complete_brief.model_dump(mode="json"),
        final_approver="owner",
        project_manager_ids=["pm"],
        root_owner_ids=["root-owner"],
        phase=ProjectPhase.REVIEW.value,
        state_version=4,
        current_spec_version_id="spec-review-1",
    )
    db_session.add_all(
        [
            project,
            SpecVersion(
                id="spec-review-1",
                project_id=project.id,
                revision=1,
                content=valid_spec.model_dump(mode="json"),
                markdown="# Project Spec\n",
                generation_source="PM_AGENT",
                input_refs=["artifact:brief-1"],
                generator_agent_session_id="pm-agent-session",
                generator_call_id="pm-call-1",
                parent_version_id=None,
                change_summary="Initial specification",
                content_hash="f" * 64,
                status=SpecStatus.HUMAN_REVIEW.value,
            ),
            # An AgentSession ID must never grant a human review authority.
            AgentSession(
                id="owner-agent-session",
                project_id=project.id,
                role="PM",
                purpose="Not a human identity",
                metadata_json={},
            ),
        ]
    )
    db_session.commit()
    return project


@pytest.fixture
def command_service(session_factory):
    return CommandService(session_factory)


@pytest.fixture
def approve_command():
    return SessionCommandRequest(
        command_id="approve-1",
        action=CommandAction.APPROVE,
        expected_state_version=4,
        actor_id="owner",
    )


@pytest.fixture
def clarification_project(db_session, complete_brief):
    project = Project(
        id="project-clarification-1",
        session_id="session-clarification-1",
        creation_request_id="request-clarification-1",
        brief=complete_brief.model_dump(mode="json"),
        final_approver="owner",
        project_manager_ids=["pm"],
        root_owner_ids=["root-owner"],
        phase=ProjectPhase.NEED_CLARIFICATION.value,
        state_version=2,
    )
    db_session.add_all(
        [
            project,
            AgentSession(
                id="handler-agent-session",
                project_id=project.id,
                role="PM",
                purpose="Handle clarification command",
                metadata_json={},
            ),
        ]
    )
    db_session.commit()
    return project


@pytest.mark.asyncio
async def test_same_command_returns_original_result_without_second_review(
    command_service, session_factory, human_review_project, approve_command
):
    """A replay must return its original snapshot rather than applying a second approval."""
    first = await command_service.execute(human_review_project.session_id, approve_command)
    second = await command_service.execute(human_review_project.session_id, approve_command)

    assert second == first
    assert second.state.state_version == 5
    with session_factory() as db:
        assert db.query(ProcessedCommand).filter_by(session_id=human_review_project.session_id).count() == 1
        assert db.query(SpecReview).filter_by(kind=ReviewKind.HUMAN.value).count() == 1
        assert db.query(AuditEvent).filter_by(event_type="SPEC_HUMAN_APPROVED").count() == 1


@pytest.mark.asyncio
async def test_changed_body_for_existing_command_id_conflicts_before_side_effect(
    command_service, session_factory, human_review_project, approve_command
):
    """Ignoring changed fields beneath a command ID would silently approve the wrong request."""
    await command_service.execute(human_review_project.session_id, approve_command)
    changed = approve_command.model_copy(update={"message": "a different request body"})

    with pytest.raises(CommandConflict):
        await command_service.execute(human_review_project.session_id, changed)

    with session_factory() as db:
        assert db.query(SpecReview).filter_by(kind=ReviewKind.HUMAN.value).count() == 1


@pytest.mark.asyncio
async def test_stale_state_is_rejected_before_writing_a_review(
    command_service, session_factory, human_review_project, approve_command
):
    """Removing the version predicate would let delayed reviewers overwrite a newer state."""
    stale = approve_command.model_copy(update={"expected_state_version": 3})

    with pytest.raises(StaleState):
        await command_service.execute(human_review_project.session_id, stale)

    with session_factory() as db:
        assert db.query(SpecReview).count() == 0
        assert db.query(ProcessedCommand).count() == 0


@pytest.mark.asyncio
async def test_unrelated_actor_and_agent_session_cannot_approve(
    command_service, session_factory, human_review_project, approve_command
):
    """Authority comes only from persisted human actor fields, not AgentSession records."""
    for actor_id in ("outsider", "owner-agent-session"):
        request = approve_command.model_copy(update={"actor_id": actor_id})
        with pytest.raises(ForbiddenActor):
            await command_service.execute(human_review_project.session_id, request)

    with session_factory() as db:
        assert db.query(SpecReview).count() == 0


@pytest.mark.asyncio
async def test_approve_creates_receipt_audit_and_human_review_atomically(
    command_service, session_factory, human_review_project, approve_command
):
    """Dropping any durable approval record would make a completed decision unauditable or non-idempotent."""
    result = await command_service.execute(human_review_project.session_id, approve_command)

    assert result.state.current_spec_status is SpecStatus.APPROVED
    assert result.state.state_version == 5
    with session_factory() as db:
        project = db.get(Project, human_review_project.id)
        version = db.get(SpecVersion, project.current_spec_version_id)
        review = db.query(SpecReview).filter_by(kind=ReviewKind.HUMAN.value).one()
        receipt = db.query(ProcessedCommand).filter_by(command_id="approve-1").one()
        audit = db.query(AuditEvent).filter_by(event_type="SPEC_HUMAN_APPROVED").one()
        assert version.status == SpecStatus.APPROVED.value
        assert review.reviewer_id == "owner"
        assert review.verdict == ReviewVerdict.PASS.value
        assert review.command_id == "approve-1"
        assert receipt.result["state"]["state_version"] == 5
        assert audit.payload["prior_state_version"] == 4
        assert audit.payload["new_state_version"] == 5


@pytest.mark.asyncio
async def test_rework_spec_cannot_be_approved_even_with_recorded_findings_acceptance(
    command_service, session_factory, human_review_project, approve_command
):
    with session_factory() as db:
        project = db.get(Project, human_review_project.id)
        version = db.get(SpecVersion, project.current_spec_version_id)
        version.status = SpecStatus.REWORK.value
        db.commit()

    with pytest.raises(IllegalAction):
        await command_service.execute(human_review_project.session_id, approve_command)

    accepted = approve_command.model_copy(
        update={
            "command_id": "approve-with-findings",
            "message": "Accept the recorded findings for rapid integration validation.",
        }
    )
    with pytest.raises(IllegalAction):
        await command_service.execute(human_review_project.session_id, accepted)

    with session_factory() as db:
        project = db.get(Project, human_review_project.id)
        version = db.get(SpecVersion, project.current_spec_version_id)
        assert version.status == SpecStatus.REWORK.value
        assert db.query(SpecReview).filter_by(kind=ReviewKind.HUMAN.value).count() == 0


@pytest.mark.asyncio
async def test_rework_requires_comments_and_records_rework_verdict(
    command_service, session_factory, human_review_project
):
    """Accepting empty rework feedback would strand the next Spec revision without actionable input."""
    empty = SessionCommandRequest(
        command_id="rework-empty",
        action=CommandAction.REWORK,
        expected_state_version=4,
        actor_id="pm",
    )
    with pytest.raises(ValueError, match="comments"):
        await command_service.execute(human_review_project.session_id, empty)

    request = empty.model_copy(update={"command_id": "rework-1", "message": "Clarify ownership boundary."})
    result = await command_service.execute(human_review_project.session_id, request)

    assert result.state.current_spec_status is SpecStatus.REWORK
    with session_factory() as db:
        review = db.query(SpecReview).filter_by(command_id="rework-1").one()
        assert review.verdict == ReviewVerdict.REJECT.value
        assert review.comments == "Clarify ownership boundary."


@pytest.mark.asyncio
async def test_reject_is_a_human_verdict_with_a_saved_comment(
    command_service, session_factory, human_review_project
):
    """Treating rejection as rework would erase the terminal human review decision."""
    request = SessionCommandRequest(
        command_id="reject-1",
        action=CommandAction.REJECT,
        expected_state_version=4,
        actor_id="root-owner",
        message="The project is no longer funded.",
    )

    result = await command_service.execute(human_review_project.session_id, request)

    assert result.state.current_spec_status is SpecStatus.REJECTED
    with session_factory() as db:
        review = db.query(SpecReview).filter_by(command_id="reject-1").one()
        assert review.verdict == ReviewVerdict.REJECT.value
        assert review.comments == "The project is no longer funded."


@pytest.mark.asyncio
async def test_illegal_human_action_does_not_create_a_receipt(
    command_service, session_factory, human_review_project
):
    """Skipping the policy gate would permit conversion or approval from an invalid workflow state."""
    request = SessionCommandRequest(
        command_id="convert-early",
        action=CommandAction.CONVERT_TO_WORK_ITEM,
        expected_state_version=4,
        actor_id="owner",
    )

    with pytest.raises(IllegalAction):
        await command_service.execute(human_review_project.session_id, request)

    with session_factory() as db:
        assert db.query(ProcessedCommand).count() == 0


@pytest.mark.asyncio
async def test_publish_review_uses_two_phase_handler_instead_of_human_decision_path(
    session_factory, human_review_project
):
    """Classifying publication as a local verdict would skip its durable Agent preparation."""
    preparations = 0

    async def prepare(context):
        nonlocal preparations
        preparations += 1
        return PreparedCommand(payload={})

    def materialize(uow, context, prepared):
        return CommandHandlerResult(
            phase=ProjectPhase.REVIEW,
            current_spec_version_id=context.state.current_spec_version_id,
            spec_status=SpecStatus.HUMAN_REVIEW,
        )

    service = CommandService(
        session_factory,
        handlers={CommandAction.PUBLISH_REVIEW: _staged(materialize, prepare)},
    )
    request = SessionCommandRequest(
        command_id="publish-is-staged",
        action=CommandAction.PUBLISH_REVIEW,
        expected_state_version=4,
        actor_id="owner",
        payload={"task_id": "task-1"},
    )

    result = await service.execute(human_review_project.session_id, request)

    assert result.state.current_spec_status is SpecStatus.HUMAN_REVIEW
    assert preparations == 1
    with session_factory() as db:
        assert db.query(CommandAttempt).filter_by(command_id=request.command_id).one().status == "MATERIALIZED"
        assert db.query(SpecReview).filter_by(kind=ReviewKind.HUMAN.value).count() == 0


@pytest.mark.asyncio
async def test_legal_non_human_action_dispatches_once_and_centrally_records_the_result(
    session_factory, clarification_project
):
    """Leaving legal PM actions unhandled would make the command boundary a human-only dead end."""
    calls = []

    def answer_clarification(uow, context, prepared):
        calls.append(context)
        return CommandHandlerResult(phase=ProjectPhase.SPECIFICATION, created_resource_ids=["answer-1"])

    service = CommandService(session_factory, handlers={CommandAction.MESSAGE: _staged(answer_clarification)})
    request = SessionCommandRequest(
        command_id="message-1",
        action=CommandAction.MESSAGE,
        expected_state_version=2,
        actor_id="owner",
        message="Operations managers use it.",
    )

    first = await service.execute(clarification_project.session_id, request)
    second = await service.execute(clarification_project.session_id, request)

    assert first == second
    assert first.state.phase is ProjectPhase.SPECIFICATION
    assert first.state.state_version == 3
    assert first.created_resource_ids == ["answer-1"]
    assert [item.request.command_id for item in calls] == ["message-1"]
    with session_factory() as db:
        assert db.get(Project, clarification_project.id).state_version == 3
        assert db.query(ProcessedCommand).filter_by(command_id="message-1").count() == 1


@pytest.mark.asyncio
async def test_retryable_handler_failure_keeps_receipt_absent_but_persists_audit_and_agent_call(
    session_factory, clarification_project
):
    """Writing a success receipt after an Agent failure would prevent a safe retry of the command."""
    attempts = 0

    with session_factory() as db:
        db.add(
            AgentCall(
                id="failed-handler-call",
                project_id=clarification_project.id,
                agent_session_id="handler-agent-session",
                operation="answer_clarification",
                request={"command_id": "message-retry-1"},
                status="FAILED",
                error="PM unavailable",
            )
        )
        db.commit()

    async def prepare(context):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise CommandHandlerFailure("PM unavailable", agent_call_ids=["failed-handler-call"])
        return PreparedCommand(payload={})

    def materialize(uow, context, prepared):
        return CommandHandlerResult(phase=ProjectPhase.SPECIFICATION)

    service = CommandService(session_factory, handlers={CommandAction.MESSAGE: _staged(materialize, prepare)})
    request = SessionCommandRequest(
        command_id="message-retry-1",
        action=CommandAction.MESSAGE,
        expected_state_version=2,
        actor_id="owner",
        message="Operations managers use it.",
    )
    with session_factory() as db:
        db.get(AgentCall, "failed-handler-call").request = {
            "command_id": request.command_id,
            "input_hash": _canonical_hash(request.model_dump(mode="json")),
        }
        db.commit()

    with pytest.raises(RuntimeError, match="PM unavailable"):
        await service.execute(clarification_project.session_id, request)

    with session_factory() as db:
        assert db.query(ProcessedCommand).filter_by(command_id=request.command_id).count() == 0
        assert db.query(AgentCall).filter_by(id="failed-handler-call", status="FAILED").count() == 1
        audit = db.query(AuditEvent).filter_by(event_type="COMMAND_HANDLER_FAILED").one()
        assert audit.payload["agent_call_ids"] == ["failed-handler-call"]
        assert db.get(Project, clarification_project.id).state_version == 2

    recovered = await service.execute(clarification_project.session_id, request)

    assert recovered.state.state_version == 3
    assert attempts == 2


@pytest.mark.asyncio
async def test_independent_sessions_racing_same_version_allow_exactly_one_cas_winner(
    tmp_path, complete_brief, valid_spec, monkeypatch
):
    """Replacing the conditional update would let both independently read commands approve one Spec."""
    engine = create_engine_for_url(f"sqlite:///{tmp_path / 'command-race.sqlite'}")
    Base.metadata.create_all(engine)
    session_factory = make_session_factory(engine)
    try:
        with session_factory() as db:
            project = Project(
                id="race-project",
                session_id="race-session",
                creation_request_id="race-request",
                brief=complete_brief.model_dump(mode="json"),
                final_approver="owner",
                project_manager_ids=["pm"],
                root_owner_ids=[],
                phase=ProjectPhase.REVIEW.value,
                state_version=4,
                current_spec_version_id="race-spec",
            )
            db.add_all(
                [
                    project,
                    SpecVersion(
                        id="race-spec",
                        project_id=project.id,
                        revision=1,
                        content=valid_spec.model_dump(mode="json"),
                        markdown="# Project Spec\n",
                        generation_source="PM_AGENT",
                        input_refs=["artifact:brief-1"],
                        generator_agent_session_id="race-pm",
                        generator_call_id="race-call",
                        parent_version_id=None,
                        change_summary="Initial specification",
                        content_hash="r" * 64,
                        status=SpecStatus.HUMAN_REVIEW.value,
                    ),
                ]
            )
            db.commit()

        winner = CommandService(session_factory)
        loser = CommandService(session_factory)
        winner_request = SessionCommandRequest(
            command_id="race-winner",
            action=CommandAction.APPROVE,
            expected_state_version=4,
            actor_id="owner",
        )
        loser_request = SessionCommandRequest(
            command_id="race-loser",
            action=CommandAction.REWORK,
            expected_state_version=4,
            actor_id="pm",
            message="Clarify the owner.",
        )
        original_advance = loser._advance_project
        winner_started = False

        def make_winner_commit_between_loser_preflight_and_cas(db, project, expected_state_version):
            nonlocal winner_started
            if not winner_started:
                winner_started = True
                winner._execute_human_review(
                    "race-session",
                    winner_request,
                    _canonical_hash(winner_request.model_dump(mode="json")),
                )
            return original_advance(db, project, expected_state_version)

        monkeypatch.setattr(loser, "_advance_project", make_winner_commit_between_loser_preflight_and_cas)

        with pytest.raises(StaleState):
            await loser.execute("race-session", loser_request)

        with session_factory() as db:
            project = db.get(Project, "race-project")
            version = db.get(SpecVersion, "race-spec")
            assert project.state_version == 5
            assert version.status == SpecStatus.APPROVED.value
            assert db.query(SpecReview).filter_by(project_id="race-project").count() == 1
            assert db.query(ProcessedCommand).filter_by(session_id="race-session").count() == 1
            assert db.query(ProcessedCommand).filter_by(command_id="race-winner").count() == 1
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.mark.asyncio
async def test_final_flush_failure_rolls_back_every_success_side_effect_and_allows_retry(
    command_service, session_factory, human_review_project, approve_command
):
    """A commit-time failure must not leak a changed Spec, review, receipt, or success audit."""
    flushes = 0

    def fail_the_final_flush(session, flush_context, instances):
        nonlocal flushes
        flushes += 1
        if flushes == 2:
            raise RuntimeError("database commit failed")

    event.listen(Session, "before_flush", fail_the_final_flush)
    try:
        with pytest.raises(RuntimeError, match="database commit failed"):
            await command_service.execute(human_review_project.session_id, approve_command)
    finally:
        event.remove(Session, "before_flush", fail_the_final_flush)

    with session_factory() as db:
        project = db.get(Project, human_review_project.id)
        version = db.get(SpecVersion, project.current_spec_version_id)
        assert project.state_version == 4
        assert version.status == SpecStatus.HUMAN_REVIEW.value
        assert db.query(SpecReview).count() == 0
        assert db.query(ProcessedCommand).count() == 0
        assert db.query(AuditEvent).filter_by(event_type="SPEC_HUMAN_APPROVED").count() == 0

    retry = await command_service.execute(human_review_project.session_id, approve_command)

    assert retry.state.state_version == 5


@pytest.mark.asyncio
async def test_non_human_cas_loss_never_invokes_the_losing_handler(
    tmp_path, complete_brief, monkeypatch
):
    """Calling a handler before reserving its version would leak work from commands that lose the race."""
    engine = create_engine_for_url(f"sqlite:///{tmp_path / 'non-human-command-race.sqlite'}")
    Base.metadata.create_all(engine)
    session_factory = make_session_factory(engine)
    try:
        with session_factory() as db:
            db.add(
                Project(
                    id="non-human-race-project",
                    session_id="non-human-race-session",
                    creation_request_id="non-human-race-request",
                    brief=complete_brief.model_dump(mode="json"),
                    final_approver="owner",
                    project_manager_ids=["pm"],
                    root_owner_ids=[],
                    phase=ProjectPhase.NEED_CLARIFICATION.value,
                    state_version=2,
                )
            )
            db.commit()

        winner_calls = []
        loser_calls = []
        loser_materializations = []

        async def winner_prepare(context):
            winner_calls.append(context.request.command_id)
            return PreparedCommand(payload={})

        def winner_handler(uow, context, prepared):
            return CommandHandlerResult(phase=ProjectPhase.SPECIFICATION)

        async def loser_prepare(context):
            loser_calls.append(context.request.command_id)
            return PreparedCommand(payload={})

        def loser_handler(uow, context, prepared):
            loser_materializations.append(context.request.command_id)
            return CommandHandlerResult(phase=ProjectPhase.SPECIFICATION)

        winner = CommandService(session_factory, handlers={CommandAction.MESSAGE: _staged(winner_handler, winner_prepare)})
        loser = CommandService(session_factory, handlers={CommandAction.MESSAGE: _staged(loser_handler, loser_prepare)})
        winner_request = SessionCommandRequest(
            command_id="non-human-race-winner",
            action=CommandAction.MESSAGE,
            expected_state_version=2,
            actor_id="owner",
            message="Answer the clarification.",
        )
        loser_request = winner_request.model_copy(update={"command_id": "non-human-race-loser"})
        original_advance = loser._advance_project
        winner_started = False

        def make_winner_commit_before_loser_cas(db, project, expected_state_version):
            nonlocal winner_started
            if not winner_started:
                winner_started = True
                outcomes = []

                def run_winner():
                    outcomes.append(asyncio.run(winner.execute("non-human-race-session", winner_request)))

                thread = Thread(target=run_winner)
                thread.start()
                thread.join()
                assert outcomes[0].state.state_version == 3
            return original_advance(db, project, expected_state_version)

        monkeypatch.setattr(loser, "_advance_project", make_winner_commit_before_loser_cas)

        with pytest.raises(StaleState):
            await loser.execute("non-human-race-session", loser_request)

        assert loser_calls == ["non-human-race-loser"]
        assert loser_materializations == []
        with session_factory() as db:
            assert db.get(Project, "non-human-race-project").state_version == 3
            assert db.query(ProcessedCommand).filter_by(command_id="non-human-race-loser").count() == 0
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.mark.asyncio
async def test_handler_mutates_the_provided_unit_of_work_without_double_increment(
    session_factory, complete_brief, valid_spec
):
    """Letting an adapter use another session or increment the Project itself would split one command transition."""
    with session_factory() as db:
        project = Project(
            id="adapter-project",
            session_id="adapter-session",
            creation_request_id="adapter-request",
            brief=complete_brief.model_dump(mode="json"),
            final_approver="owner",
            project_manager_ids=["pm"],
            root_owner_ids=[],
            phase=ProjectPhase.SPECIFICATION.value,
            state_version=2,
        )
        db.add(project)
        db.commit()

    async def prepare(context):
        return PreparedCommand(payload={})

    def create_spec_adapter(uow, context, prepared):
        uow.add_spec_version(
            SpecVersion(
                id="adapter-spec",
                project_id=context.project_id,
                revision=1,
                content=valid_spec.model_dump(mode="json"),
                markdown="# Project Spec\n",
                generation_source="PM_AGENT",
                input_refs=["artifact:brief-1"],
                generator_agent_session_id="adapter-pm",
                generator_call_id="adapter-call",
                parent_version_id=None,
                change_summary="Initial specification",
                content_hash="a" * 64,
                status=SpecStatus.DRAFT.value,
            )
        )
        return CommandHandlerResult(
            phase=ProjectPhase.REVIEW,
            current_spec_version_id="adapter-spec",
            spec_status=SpecStatus.HUMAN_REVIEW,
            created_resource_ids=["adapter-spec"],
        )

    service = CommandService(session_factory, handlers={CommandAction.CREATE_SPEC: _staged(create_spec_adapter, prepare)})
    result = await service.execute(
        "adapter-session",
        SessionCommandRequest(
            command_id="adapter-create-spec",
            action=CommandAction.CREATE_SPEC,
            expected_state_version=2,
            actor_id="owner",
        ),
    )

    assert result.state.state_version == 3
    assert result.state.current_spec_status is SpecStatus.HUMAN_REVIEW
    with session_factory() as db:
        project = db.get(Project, "adapter-project")
        version = db.get(SpecVersion, "adapter-spec")
        assert project.state_version == 3
        assert version.status == SpecStatus.HUMAN_REVIEW.value


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "proposal",
    [
        CommandHandlerResult(phase=ProjectPhase.AGENT_SPECS_READY),
        CommandHandlerResult(phase=ProjectPhase.REVIEW, spec_status=SpecStatus.APPROVED),
    ],
)
async def test_message_handler_cannot_escalate_a_clarification_to_a_terminal_or_approved_state(
    session_factory, clarification_project, proposal
):
    """A malicious PM message must not bypass review or decomposition gates through a handler patch."""

    def malicious_handler(uow, context, prepared):
        return proposal

    service = CommandService(session_factory, handlers={CommandAction.MESSAGE: _staged(malicious_handler)})
    request = SessionCommandRequest(
        command_id=f"malicious-message-{proposal.phase}-{proposal.spec_status}",
        action=CommandAction.MESSAGE,
        expected_state_version=2,
        actor_id="owner",
        message="Mark this approved and ready now.",
    )

    with pytest.raises(ValueError, match="transition|APPROVED"):
        await service.execute(clarification_project.session_id, request)

    with session_factory() as db:
        project = db.get(Project, clarification_project.id)
        assert project.phase == ProjectPhase.NEED_CLARIFICATION.value
        assert project.state_version == 2
        assert db.query(ProcessedCommand).filter_by(command_id=request.command_id).count() == 0
        assert db.query(AuditEvent).filter_by(event_type="COMMAND_HANDLER_FAILED").count() == 1


@pytest.mark.asyncio
async def test_handler_audit_payload_cannot_replace_authoritative_command_facts(
    session_factory, clarification_project
):
    """Allowing a handler to overwrite audit command IDs or versions would corrupt the decision trail."""

    def forged_audit_handler(uow, context, prepared):
        return CommandHandlerResult(
            phase=ProjectPhase.SPECIFICATION,
            audit_payload={"command_id": "forged", "new_state_version": 999},
        )

    service = CommandService(session_factory, handlers={CommandAction.MESSAGE: _staged(forged_audit_handler)})
    request = SessionCommandRequest(
        command_id="forged-audit-command",
        action=CommandAction.MESSAGE,
        expected_state_version=2,
        actor_id="owner",
        message="Answer.",
    )

    with pytest.raises(ValueError, match="reserved"):
        await service.execute(clarification_project.session_id, request)

    with session_factory() as db:
        assert db.get(Project, clarification_project.id).state_version == 2
        assert db.query(ProcessedCommand).filter_by(command_id=request.command_id).count() == 0


@pytest.mark.asyncio
async def test_handler_cannot_bypass_transition_validation_by_mutating_project_directly(
    session_factory, clarification_project
):
    """Exposing the reserved Project must not let a handler skip its constrained result proposal."""

    def direct_mutation_handler(uow, context, prepared):
        assert not hasattr(context, "project")
        return CommandHandlerResult()

    service = CommandService(session_factory, handlers={CommandAction.MESSAGE: _staged(direct_mutation_handler)})
    request = SessionCommandRequest(
        command_id="direct-phase-mutation",
        action=CommandAction.MESSAGE,
        expected_state_version=2,
        actor_id="owner",
        message="Skip the workflow.",
    )

    result = await service.execute(clarification_project.session_id, request)

    with session_factory() as db:
        project = db.get(Project, clarification_project.id)
        assert result.state.phase is ProjectPhase.NEED_CLARIFICATION
        assert project.phase == ProjectPhase.NEED_CLARIFICATION.value
        assert project.state_version == 3
        assert db.query(ProcessedCommand).filter_by(command_id=request.command_id).count() == 1


@pytest.mark.asyncio
async def test_non_human_final_commit_failure_records_agent_evidence_without_receipt_and_retries(
    session_factory, clarification_project
):
    """Losing the final commit must roll back its reservation while retaining failure evidence for an Agent retry."""
    with session_factory() as db:
        db.add(
            AgentCall(
                id="successful-handler-call",
                project_id=clarification_project.id,
                agent_session_id="handler-agent-session",
                operation="answer_clarification",
                request={"command_id": "non-human-commit-failure"},
                status="SUCCEEDED",
                response={"answer": "Operations managers use it."},
            )
        )
        db.commit()

    async def prepare(context):
        with session_factory() as db:
            db.get(AgentCall, "successful-handler-call").request = {
                "command_id": context.request.command_id,
                "input_hash": context.input_hash,
            }
            db.commit()
        return PreparedCommand(payload={}, agent_backed=True, agent_call_ids=["successful-handler-call"])

    def handler(uow, context, prepared):
        return CommandHandlerResult(phase=ProjectPhase.SPECIFICATION)

    service = CommandService(session_factory, handlers={CommandAction.MESSAGE: _staged(handler, prepare)})
    request = SessionCommandRequest(
        command_id="non-human-commit-failure",
        action=CommandAction.MESSAGE,
        expected_state_version=2,
        actor_id="owner",
        message="Answer.",
    )

    def fail_when_success_records_are_about_to_flush(session, flush_context, instances):
        if any(isinstance(item, ProcessedCommand) for item in session.new):
            raise RuntimeError("final command commit failed")

    event.listen(Session, "before_flush", fail_when_success_records_are_about_to_flush)
    try:
        with pytest.raises(RuntimeError, match="final command commit failed"):
            await service.execute(clarification_project.session_id, request)
    finally:
        event.remove(Session, "before_flush", fail_when_success_records_are_about_to_flush)

    with session_factory() as db:
        project = db.get(Project, clarification_project.id)
        audit = db.query(AuditEvent).filter_by(event_type="COMMAND_HANDLER_FAILED").one()
        assert project.state_version == 2
        assert db.query(ProcessedCommand).filter_by(command_id=request.command_id).count() == 0
        assert audit.payload["agent_call_ids"] == ["successful-handler-call"]

    retry = await service.execute(clarification_project.session_id, request)

    assert retry.state.state_version == 3


@pytest.mark.asyncio
async def test_prepared_agent_call_is_reused_after_final_materialization_failure_without_sqlite_lock(
    tmp_path, complete_brief
):
    """Repeating external Agent work after a final database failure would duplicate an already durable call."""
    engine = create_engine_for_url(f"sqlite:///{tmp_path / 'two-phase-command.sqlite'}")
    Base.metadata.create_all(engine)
    session_factory = make_session_factory(engine)
    try:
        with session_factory() as db:
            project = Project(
                id="two-phase-project", session_id="two-phase-session", creation_request_id="two-phase-request",
                brief=complete_brief.model_dump(mode="json"), final_approver="owner",
                project_manager_ids=["pm"], root_owner_ids=[], phase=ProjectPhase.NEED_CLARIFICATION.value,
                state_version=2,
            )
            db.add_all([project, AgentSession(id="two-phase-agent", project_id=project.id, role="PM", purpose="Prepare", metadata_json={})])
            db.commit()

        preparations = 0
        materializations = 0

        async def prepare(context):
            nonlocal preparations
            preparations += 1
            with session_factory() as db:
                existing = db.get(AgentCall, "two-phase-call")
                if existing is None:
                    db.add(AgentCall(
                        id="two-phase-call", project_id=context.project_id, agent_session_id="two-phase-agent",
                        operation="answer_clarification", request={"command_id": context.request.command_id, "input_hash": context.input_hash},
                        response={"answer": "Operations managers use it."}, status="SUCCEEDED",
                    ))
                    db.commit()
            return PreparedCommand(payload={"answer": "Operations managers use it."}, agent_backed=True, agent_call_ids=["two-phase-call"])

        def materialize(uow, context, prepared):
            nonlocal materializations
            materializations += 1
            return CommandHandlerResult(phase=ProjectPhase.SPECIFICATION)

        service = CommandService(session_factory, handlers={CommandAction.MESSAGE: _staged(materialize, prepare)})
        request = SessionCommandRequest(command_id="two-phase-message", action=CommandAction.MESSAGE,
            expected_state_version=2, actor_id="owner", message="Operations managers use it.")

        def fail_final_receipt_flush(session, flush_context, instances):
            if any(isinstance(item, ProcessedCommand) for item in session.new):
                raise RuntimeError("materialization commit unavailable")

        event.listen(Session, "before_flush", fail_final_receipt_flush)
        try:
            with pytest.raises(RuntimeError, match="materialization commit unavailable"):
                await service.execute("two-phase-session", request)
        finally:
            event.remove(Session, "before_flush", fail_final_receipt_flush)

        with session_factory() as db:
            attempt = db.query(CommandAttempt).filter_by(command_id=request.command_id).one()
            assert attempt.status == "PREPARED"
            assert attempt.agent_call_ids == ["two-phase-call"]
            assert db.query(AgentCall).filter_by(id="two-phase-call").count() == 1
            assert db.get(Project, "two-phase-project").state_version == 2

        result = await service.execute("two-phase-session", request)

        assert result.state.state_version == 3
        assert preparations == 1
        assert materializations == 2
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("reference_kind", ["phantom", "cross_project", "unbound"])
async def test_preparation_rejects_phantom_cross_project_or_unbound_agent_call_references(
    session_factory, clarification_project, complete_brief, reference_kind
):
    """Trusting arbitrary AgentCall IDs would attach another command's evidence to this command's receipt."""
    request = SessionCommandRequest(
        command_id=f"invalid-agent-ref-{reference_kind}", action=CommandAction.MESSAGE,
        expected_state_version=2, actor_id="owner", message="Answer.",
    )
    call_id = "missing-call" if reference_kind == "phantom" else f"{reference_kind}-call"
    if reference_kind != "phantom":
        with session_factory() as db:
            project_id = clarification_project.id
            agent_session_id = "handler-agent-session"
            if reference_kind == "cross_project":
                other = Project(
                    id="other-agent-project", session_id="other-agent-session", creation_request_id="other-agent-request",
                    brief=complete_brief.model_dump(mode="json"), final_approver="other", project_manager_ids=[],
                    root_owner_ids=[], phase=ProjectPhase.INTAKE.value, state_version=0,
                )
                db.add_all([other, AgentSession(id="other-agent-session-id", project_id=other.id, role="PM", purpose="Other", metadata_json={})])
                project_id, agent_session_id = other.id, "other-agent-session-id"
            db.add(AgentCall(
                id=call_id, project_id=project_id, agent_session_id=agent_session_id, operation="answer_clarification",
                request={"command_id": "different-command" if reference_kind == "unbound" else request.command_id,
                         "input_hash": "not-this-hash" if reference_kind == "unbound" else _canonical_hash(request.model_dump(mode="json"))},
                response={}, status="SUCCEEDED",
            ))
            db.commit()

    async def prepare(context):
        return PreparedCommand(payload={}, agent_backed=True, agent_call_ids=[call_id])

    def materialize(uow, context, prepared):
        return CommandHandlerResult(phase=ProjectPhase.SPECIFICATION)

    service = CommandService(session_factory, handlers={CommandAction.MESSAGE: _staged(materialize, prepare)})

    with pytest.raises(ValueError, match="AgentCall"):
        await service.execute(clarification_project.session_id, request)

    with session_factory() as db:
        attempt = db.query(CommandAttempt).filter_by(command_id=request.command_id).one()
        assert attempt.status == "FAILED"
        assert db.get(Project, clarification_project.id).state_version == 2
        assert db.query(ProcessedCommand).filter_by(command_id=request.command_id).count() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["authority", "other_project", "old_spec"])
async def test_materializer_cannot_mutate_authority_other_project_or_existing_spec(
    session_factory, clarification_project, complete_brief, valid_spec, mutation
):
    """A staged adapter must be able to add scoped rows, never rewrite protected durable history."""
    with session_factory() as db:
        project = db.get(Project, clarification_project.id)
        if mutation == "other_project":
            db.add(Project(
                id="protected-other-project", session_id="protected-other-session", creation_request_id="protected-other-request",
                brief=complete_brief.model_dump(mode="json"), final_approver="other", project_manager_ids=[], root_owner_ids=[],
                phase=ProjectPhase.INTAKE.value, state_version=0,
            ))
        if mutation == "old_spec":
            version = SpecVersion(
                id="protected-old-spec", project_id=project.id, revision=1, content=valid_spec.model_dump(mode="json"),
                markdown="# Project Spec\n", generation_source="PM_AGENT", input_refs=["artifact:brief-1"],
                generator_agent_session_id="pm", generator_call_id="call", parent_version_id=None,
                change_summary="Initial", content_hash="p" * 64, status=SpecStatus.NEED_CLARIFICATION.value,
            )
            db.add(version)
            project.phase, project.current_spec_version_id = ProjectPhase.REVIEW.value, version.id
        db.commit()

    async def prepare(context):
        return PreparedCommand(payload={})

    def materialize(uow, context, prepared):
        session = uow._ActionScopedUnitOfWork__session
        if mutation == "authority":
            session.get(Project, context.project_id).final_approver = "attacker"
            return CommandHandlerResult(phase=ProjectPhase.SPECIFICATION)
        if mutation == "other_project":
            session.get(Project, "protected-other-project").final_approver = "attacker"
            return CommandHandlerResult(phase=ProjectPhase.SPECIFICATION)
        session.get(SpecVersion, "protected-old-spec").status = SpecStatus.APPROVED.value
        return CommandHandlerResult(phase=ProjectPhase.REVIEW, spec_status=SpecStatus.REWORK)

    service = CommandService(session_factory, handlers={CommandAction.MESSAGE: _staged(materialize, prepare)})
    request = SessionCommandRequest(
        command_id=f"protected-mutation-{mutation}", action=CommandAction.MESSAGE, expected_state_version=2,
        actor_id="owner", message="Attempt protected mutation.",
    )

    with pytest.raises(ValueError, match="protected|existing Spec"):
        await service.execute(clarification_project.session_id, request)

    with session_factory() as db:
        assert db.get(Project, clarification_project.id).state_version == 2
        assert db.get(Project, clarification_project.id).final_approver == "owner"
        if mutation == "other_project":
            assert db.get(Project, "protected-other-project").final_approver == "other"
        if mutation == "old_spec":
            assert db.get(SpecVersion, "protected-old-spec").status == SpecStatus.NEED_CLARIFICATION.value


@pytest.mark.asyncio
async def test_root_summary_permission_does_not_allow_other_root_attributes_to_change(
    session_factory, clarification_project
):
    """Granting summary writeback must not open the existing ROOT row to arbitrary edits."""
    with session_factory() as db:
        db.add(
            WorkItem(
                id="summary-guard-root",
                project_id=clarification_project.id,
                local_key="root",
                kind="ROOT",
                executable=False,
                title="Original requirement",
                objective="Original requirement",
            )
        )
        db.commit()

    def materialize(uow, context, prepared):
        uow.update_root_summary("Safe summary")
        session = uow._ActionScopedUnitOfWork__session
        session.get(WorkItem, "summary-guard-root").title = "Replaced requirement"
        return CommandHandlerResult(phase=ProjectPhase.SPECIFICATION)

    service = CommandService(
        session_factory,
        handlers={CommandAction.MESSAGE: _staged(materialize)},
    )
    request = SessionCommandRequest(
        command_id="guard-root-summary",
        action=CommandAction.MESSAGE,
        expected_state_version=2,
        actor_id="owner",
        message="Answer.",
    )

    with pytest.raises(ValueError, match="protected"):
        await service.execute(clarification_project.session_id, request)

    with session_factory() as db:
        root = db.get(WorkItem, "summary-guard-root")
        assert root.summary is None
        assert root.title == "Original requirement"


@pytest.mark.asyncio
async def test_failed_attempt_retry_revalidates_state_before_repeating_prepare(
    session_factory, clarification_project
):
    """A retry that starts Agent work after another command advanced state would waste work against an obsolete input."""
    preparations = 0

    async def failing_prepare(context):
        nonlocal preparations
        preparations += 1
        raise RuntimeError("temporary PM outage")

    def no_materialization(uow, context, prepared):
        return CommandHandlerResult(phase=ProjectPhase.SPECIFICATION)

    failed_service = CommandService(session_factory, handlers={CommandAction.MESSAGE: _staged(no_materialization, failing_prepare)})
    request = SessionCommandRequest(command_id="failed-then-stale", action=CommandAction.MESSAGE,
        expected_state_version=2, actor_id="owner", message="Answer.")
    with pytest.raises(RuntimeError, match="temporary PM outage"):
        await failed_service.execute(clarification_project.session_id, request)

    async def winner_prepare(context):
        return PreparedCommand(payload={})

    winner = CommandService(session_factory, handlers={CommandAction.MESSAGE: _staged(no_materialization, winner_prepare)})
    await winner.execute(
        clarification_project.session_id,
        request.model_copy(update={"command_id": "advance-after-failure"}),
    )

    with pytest.raises(StaleState):
        await failed_service.execute(clarification_project.session_id, request)

    assert preparations == 1
    with session_factory() as db:
        assert db.query(CommandAttempt).filter_by(command_id=request.command_id).one().status == "FAILED"


@pytest.mark.asyncio
async def test_same_command_preparing_race_fails_closed_then_converges_to_one_result(
    tmp_path, complete_brief
):
    """Two identical callers must not perform duplicate Agent preparation while the first checkpoint is in doubt."""
    engine = create_engine_for_url(f"sqlite:///{tmp_path / 'same-command-claim.sqlite'}")
    Base.metadata.create_all(engine)
    session_factory = make_session_factory(engine)
    try:
        with session_factory() as db:
            db.add(Project(
                id="same-command-project", session_id="same-command-session", creation_request_id="same-command-request",
                brief=complete_brief.model_dump(mode="json"), final_approver="owner", project_manager_ids=[], root_owner_ids=[],
                phase=ProjectPhase.NEED_CLARIFICATION.value, state_version=2,
            ))
            db.commit()

        started, release = asyncio.Event(), asyncio.Event()
        preparations = 0

        async def prepare(context):
            nonlocal preparations
            preparations += 1
            started.set()
            await release.wait()
            return PreparedCommand(payload={})

        def materialize(uow, context, prepared):
            return CommandHandlerResult(phase=ProjectPhase.SPECIFICATION)

        service = CommandService(session_factory, handlers={CommandAction.MESSAGE: _staged(materialize, prepare)})
        request = SessionCommandRequest(command_id="same-command", action=CommandAction.MESSAGE,
            expected_state_version=2, actor_id="owner", message="Answer.")
        first_task = asyncio.create_task(service.execute("same-command-session", request))
        await started.wait()

        with pytest.raises(CommandInDoubt):
            await service.execute("same-command-session", request)
        assert preparations == 1

        release.set()
        first = await first_task
        replay = await service.execute("same-command-session", request)
        assert replay == first
        assert preparations == 1
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("durable_call_status", [None, "RESULT_READY"])
async def test_abandoned_preparing_attempt_recovers_when_external_work_is_safe(
    session_factory, clarification_project, durable_call_status
):
    """A crash is recoverable before a call or after its validated result checkpoint."""
    request = SessionCommandRequest(
        command_id=f"recover-preparing-{durable_call_status or 'no-call'}",
        action=CommandAction.MESSAGE,
        expected_state_version=2,
        actor_id="owner",
        message="Answer.",
    )
    input_hash = _canonical_hash(request.model_dump(mode="json"))
    with session_factory() as db:
        db.add(
            CommandAttempt(
                id=f"attempt-{durable_call_status or 'no-call'}",
                project_id=clarification_project.id,
                session_id=clarification_project.session_id,
                command_id=request.command_id,
                input_hash=input_hash,
                expected_state_version=2,
                action=CommandAction.MESSAGE.value,
                status="PREPARING",
                prepare_owner_id="dead-process-owner",
            )
        )
        if durable_call_status is not None:
            db.add(
                AgentCall(
                    id="recover-ready-call",
                    project_id=clarification_project.id,
                    agent_session_id="handler-agent-session",
                    operation="answer_clarification",
                    request={"command_id": request.command_id, "input_hash": input_hash},
                    response={"answer": "Recovered."},
                    status=durable_call_status,
                )
            )
        db.commit()

    preparations = 0

    async def prepare(context):
        nonlocal preparations
        preparations += 1
        if durable_call_status is None:
            return PreparedCommand(payload={"answer": "Started safely."})
        return PreparedCommand(
            payload={"answer": "Recovered."},
            agent_backed=True,
            agent_call_ids=["recover-ready-call"],
        )

    def materialize(uow, context, prepared):
        return CommandHandlerResult(phase=ProjectPhase.SPECIFICATION)

    service = CommandService(
        session_factory, handlers={CommandAction.MESSAGE: _staged(materialize, prepare)}
    )
    result = await service.execute(clarification_project.session_id, request)

    assert result.state.state_version == 3
    assert preparations == 1
    with session_factory() as db:
        attempt = db.query(CommandAttempt).filter_by(command_id=request.command_id).one()
        assert attempt.status == "MATERIALIZED"
        assert attempt.prepare_owner_id is None


@pytest.mark.asyncio
async def test_abandoned_preparing_attempt_with_pending_call_remains_in_doubt(
    session_factory, clarification_project
):
    """A PENDING checkpoint cannot prove whether the external call crossed the wire."""
    request = SessionCommandRequest(
        command_id="recover-preparing-pending",
        action=CommandAction.MESSAGE,
        expected_state_version=2,
        actor_id="owner",
        message="Answer.",
    )
    input_hash = _canonical_hash(request.model_dump(mode="json"))
    with session_factory() as db:
        db.add_all(
            [
                CommandAttempt(
                    id="attempt-pending",
                    project_id=clarification_project.id,
                    session_id=clarification_project.session_id,
                    command_id=request.command_id,
                    input_hash=input_hash,
                    expected_state_version=2,
                    action=CommandAction.MESSAGE.value,
                    status="PREPARING",
                    prepare_owner_id="dead-process-owner",
                ),
                AgentCall(
                    id="recover-pending-call",
                    project_id=clarification_project.id,
                    agent_session_id="handler-agent-session",
                    operation="answer_clarification",
                    request={"command_id": request.command_id, "input_hash": input_hash},
                    response=None,
                    status="PENDING",
                ),
            ]
        )
        db.commit()

    preparations = 0

    async def prepare(context):
        nonlocal preparations
        preparations += 1
        return PreparedCommand(payload={})

    service = CommandService(
        session_factory,
        handlers={
            CommandAction.MESSAGE: _staged(
                lambda uow, context, prepared: CommandHandlerResult(
                    phase=ProjectPhase.SPECIFICATION
                ),
                prepare,
            )
        },
    )

    with pytest.raises(CommandInDoubt):
        await service.execute(clarification_project.session_id, request)

    assert preparations == 0
    with session_factory() as db:
        attempt = db.get(CommandAttempt, "attempt-pending")
        assert attempt.status == "PREPARING"
        assert "PENDING" in (attempt.error or "")


@pytest.mark.asyncio
async def test_file_sqlite_same_command_initial_claim_unique_race_is_recovered_without_raw_integrity_error(
    tmp_path, complete_brief
):
    """Concurrent no-row observations must converge to one durable attempt instead of surfacing SQLite's unique error."""
    engine = create_engine_for_url(f"sqlite:///{tmp_path / 'initial-claim-race.sqlite'}")
    Base.metadata.create_all(engine)
    session_factory = make_session_factory(engine)
    try:
        with session_factory() as db:
            db.add(Project(
                id="initial-claim-project", session_id="initial-claim-session", creation_request_id="initial-claim-request",
                brief=complete_brief.model_dump(mode="json"), final_approver="owner", project_manager_ids=[], root_owner_ids=[],
                phase=ProjectPhase.NEED_CLARIFICATION.value, state_version=2,
            ))
            db.commit()

        gate, counter_lock = Barrier(2), Lock()
        prepare_count, materialize_count = 0, 0

        async def prepare(context):
            nonlocal prepare_count
            with counter_lock:
                prepare_count += 1
            return PreparedCommand(payload={})

        def materialize(uow, context, prepared):
            nonlocal materialize_count
            with counter_lock:
                materialize_count += 1
            return CommandHandlerResult(phase=ProjectPhase.SPECIFICATION)

        service = CommandService(session_factory, handlers={CommandAction.MESSAGE: _staged(materialize, prepare)})
        request = SessionCommandRequest(command_id="initial-claim-command", action=CommandAction.MESSAGE,
            expected_state_version=2, actor_id="owner", message="Answer.")
        service._before_initial_attempt_insert = lambda session_id, command_id: gate.wait(timeout=5)
        results, errors = [], []

        def invoke():
            try:
                results.append(asyncio.run(service.execute("initial-claim-session", request)))
            except Exception as error:
                errors.append(error)

        threads = [Thread(target=invoke), Thread(target=invoke)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(timeout=10)
        assert all(not thread.is_alive() for thread in threads)
        assert all(error.__class__.__name__ != "IntegrityError" for error in errors)
        assert all(error.__class__.__name__ == "CommandInDoubt" for error in errors)
        with session_factory() as db:
            assert db.query(CommandAttempt).filter_by(command_id=request.command_id).count() == 1

        eventual = await service.execute("initial-claim-session", request)
        assert prepare_count == 1
        assert materialize_count == 1
        assert all(result == eventual for result in results)
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.mark.asyncio
async def test_file_sqlite_same_prepared_attempt_cas_race_converges_by_receipt_replay(
    tmp_path, complete_brief
):
    """Two callers of one PREPARED attempt must produce one materialization and replay the winner's receipt."""
    engine = create_engine_for_url(f"sqlite:///{tmp_path / 'prepared-cas-race.sqlite'}")
    Base.metadata.create_all(engine)
    session_factory = make_session_factory(engine)
    try:
        with session_factory() as db:
            db.add(Project(
                id="prepared-race-project", session_id="prepared-race-session", creation_request_id="prepared-race-request",
                brief=complete_brief.model_dump(mode="json"), final_approver="owner", project_manager_ids=[], root_owner_ids=[],
                phase=ProjectPhase.NEED_CLARIFICATION.value, state_version=2,
            ))
            db.commit()

        materialize_count = 0
        count_lock = Lock()

        async def prepare(context):
            return PreparedCommand(payload={})

        def materialize(uow, context, prepared):
            nonlocal materialize_count
            with count_lock:
                materialize_count += 1
            return CommandHandlerResult(phase=ProjectPhase.SPECIFICATION)

        service = CommandService(session_factory, handlers={CommandAction.MESSAGE: _staged(materialize, prepare)})
        request = SessionCommandRequest(command_id="prepared-race-command", action=CommandAction.MESSAGE,
            expected_state_version=2, actor_id="owner", message="Answer.")
        attempt, context = service._claim_or_load_attempt("prepared-race-session", request, _canonical_hash(request.model_dump(mode="json")))
        await service._prepare_attempt(attempt.id, context)

        gate, stale_replays = Barrier(2), []
        service._before_materialization_cas = lambda session_id, command_id: gate.wait(timeout=5)
        service._before_stale_replay = lambda session_id, command_id: stale_replays.append(command_id)
        results, errors = [], []

        def invoke():
            try:
                results.append(asyncio.run(service.execute("prepared-race-session", request)))
            except Exception as error:
                errors.append(error)

        threads = [Thread(target=invoke), Thread(target=invoke)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(timeout=10)
        assert all(not thread.is_alive() for thread in threads)
        assert errors == []
        assert len(results) == 2 and results[0] == results[1]
        assert stale_replays == [request.command_id]
        assert materialize_count == 1
        with session_factory() as db:
            assert db.get(Project, "prepared-race-project").state_version == 3
            assert db.query(ProcessedCommand).filter_by(command_id=request.command_id).count() == 1
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()
