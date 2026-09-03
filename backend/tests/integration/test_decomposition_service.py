"""Atomic persistence behavior for approved-Spec decomposition."""

from collections import deque

import pytest
from sqlalchemy import event

from app.database.models import AgentCall, AgentSpec, AuditEvent, CommandAttempt, ProcessedCommand, Project, SpecVersion, WorkItem, WorkItemDependency
from app.domain.types import ProjectPhase, ReviewFinding, ReviewVerdict, SemanticReview, SpecStatus
from app.domain.types import CommandAction
from app.schemas.workflow import SessionCommandRequest
from app.agents.codex import build_node_prompt
from app.services.command_service import ActionScopedUnitOfWork, CommandHandlerFailure, CommandHandlerRejected, CommandService
from app.services.decomposition_service import BreakdownValidationError, DecompositionService
from tests.helpers.fake_agent import ScriptedAgentGateway
from tests.helpers.factories import make_valid_breakdown


def _approved_project(db, brief, spec):
    project = Project(
        id="project-decompose-1",
        session_id="session-decompose-1",
        creation_request_id="request-decompose-1",
        brief=brief.model_dump(mode="json"),
        final_approver=brief.final_approver,
        project_manager_ids=brief.project_manager_ids,
        root_owner_ids=brief.root_owner_ids,
        phase=ProjectPhase.REVIEW.value,
        state_version=7,
        current_spec_version_id="spec-decompose-1",
    )
    db.add_all([
        project,
        SpecVersion(
            id="spec-decompose-1", project_id=project.id, revision=1,
            content=spec.model_dump(mode="json"), markdown="# Project Spec\n",
            generation_source="PM_AGENT", input_refs=["artifact:brief-1"],
            generator_agent_session_id="pm-session-1", generator_call_id="pm-call-1",
            parent_version_id=None, change_summary="Initial specification",
            content_hash="d" * 64, status=SpecStatus.APPROVED.value,
        ),
    ])
    db.commit()
    return project


@pytest.mark.asyncio
async def test_invalid_proposal_rolls_back_all_rows_and_keeps_approved_state(
    session_factory, db_session, complete_brief, valid_spec, valid_breakdown
):
    """A late invalid leaf must not strand a project with a partial child-Agent plan."""
    project = _approved_project(db_session, complete_brief, valid_spec)
    valid_breakdown.agent_specs[1].outputs = []
    agent = ScriptedAgentGateway(decompose_results=deque([valid_breakdown]))

    with pytest.raises(BreakdownValidationError, match="MISSING_OUTPUT"):
        await DecompositionService(session_factory, agent).convert(project.id)

    with session_factory() as db:
        current = db.get(Project, project.id)
        assert db.query(WorkItem).filter_by(project_id=project.id).count() == 0
        assert db.query(WorkItemDependency).filter_by(project_id=project.id).count() == 0
        assert db.query(AgentSpec).filter_by(project_id=project.id).count() == 0
        assert current.phase == ProjectPhase.REVIEW.value
        assert current.state_version == 7
        assert db.get(SpecVersion, current.current_spec_version_id).status == SpecStatus.APPROVED.value


@pytest.mark.asyncio
async def test_conversion_persists_server_ids_dependencies_and_canonical_child_specs(
    session_factory, db_session, complete_brief, valid_spec, valid_breakdown
):
    """Trusting model IDs or omitting a source version would make child work unauditable."""
    project = _approved_project(db_session, complete_brief, valid_spec)
    agent = ScriptedAgentGateway(decompose_results=deque([valid_breakdown]))

    created = await DecompositionService(session_factory, agent).convert(project.id)

    assert len(created) == 2
    with session_factory() as db:
        current = db.get(Project, project.id)
        items = {item.local_key: item for item in db.query(WorkItem).filter_by(project_id=project.id)}
        api_spec = db.query(AgentSpec).filter_by(work_item_id=items["t-api"].id).one()
        edge = db.query(WorkItemDependency).filter_by(from_work_item_id=items["t-api"].id).one()
        assert current.phase == ProjectPhase.AGENT_SPECS_READY.value
        assert current.state_version == 8
        assert edge.to_work_item_id == items["t-domain"].id
        assert api_spec.source_spec_version_id == "spec-decompose-1"
        assert api_spec.dependency_work_item_ids == [items["t-domain"].id]
        assert api_spec.content["work_item_id"] == items["t-api"].id
        assert api_spec.content_hash
        api_item = items["t-api"]
        proposal = valid_breakdown.agent_specs[1]
        assert api_item.scope == proposal.scope
        assert api_item.exclusions == proposal.exclusions
        assert api_item.inputs == proposal.inputs
        assert api_item.outputs == [item.model_dump(mode="json") for item in proposal.outputs]
        assert api_item.acceptance_criteria == [
            item.model_dump(mode="json") for item in proposal.acceptance_criteria
        ]
        assert api_item.required_skills == proposal.required_skills
        assert api_item.responsible_role == proposal.responsible_role
        assert api_item.suggested_assignee == proposal.suggested_assignee


@pytest.mark.asyncio
async def test_two_phase_command_adapter_advances_only_after_all_child_specs_are_written(
    session_factory, db_session, complete_brief, valid_spec, valid_breakdown
):
    """Bypassing CommandService would lose the conversion receipt and CAS workflow gate."""
    project = _approved_project(db_session, complete_brief, valid_spec)
    agent = ScriptedAgentGateway(decompose_results=deque([valid_breakdown]))
    decomposition = DecompositionService(session_factory, agent)
    service = CommandService(session_factory, handlers={CommandAction.CONVERT_TO_WORK_ITEM: decomposition.as_command_handler()})

    result = await service.execute(project.session_id, SessionCommandRequest(
        command_id="convert-1", action=CommandAction.CONVERT_TO_WORK_ITEM,
        expected_state_version=7, actor_id="owner",
    ))

    assert result.state.phase is ProjectPhase.AGENT_SPECS_READY
    assert len(result.created_resource_ids) == 2
    with session_factory() as db:
        assert db.query(AgentSpec).filter_by(project_id=project.id).count() == 2
        assert db.query(AgentCall).filter_by(project_id=project.id, operation="decompose_spec", status="SUCCEEDED").count() == 1
        assert db.query(ProcessedCommand).filter_by(command_id="convert-1").count() == 1


@pytest.mark.asyncio
async def test_invalid_command_preparation_marks_agent_call_failed_and_leaves_no_attempt_receipt(
    session_factory, db_session, complete_brief, valid_spec, valid_breakdown
):
    """Saving invalid model output as PREPARED would make an unsafe final transaction possible."""
    project = _approved_project(db_session, complete_brief, valid_spec)
    valid_breakdown.agent_specs[1].outputs = []
    agent = ScriptedAgentGateway(decompose_results=deque([valid_breakdown]))
    decomposition = DecompositionService(session_factory, agent)
    service = CommandService(session_factory, handlers={CommandAction.CONVERT_TO_WORK_ITEM: decomposition.as_command_handler()})

    with pytest.raises(RuntimeError, match="MISSING_OUTPUT"):
        await service.execute(project.session_id, SessionCommandRequest(command_id="convert-invalid", action=CommandAction.CONVERT_TO_WORK_ITEM, expected_state_version=7, actor_id="owner"))

    with session_factory() as db:
        call = db.query(AgentCall).filter_by(project_id=project.id, operation="decompose_spec").one()
        assert call.status == "FAILED"
        attempt = db.query(CommandAttempt).filter_by(command_id="convert-invalid").one()
        assert attempt.agent_call_ids == [call.id]
        assert db.query(AgentSpec).filter_by(project_id=project.id).count() == 0
        assert db.query(ProcessedCommand).filter_by(command_id="convert-invalid").count() == 0


@pytest.mark.asyncio
async def test_failed_preparation_can_retry_same_command_id_with_new_agent_evidence(
    session_factory, db_session, complete_brief, valid_spec, valid_breakdown
):
    """A failed validation must leave the command retryable instead of permanently in doubt."""
    project = _approved_project(db_session, complete_brief, valid_spec)
    valid_breakdown.agent_specs[1].outputs = []
    agent = ScriptedAgentGateway(decompose_results=deque([valid_breakdown, make_valid_breakdown()]))
    decomposition = DecompositionService(session_factory, agent)
    service = CommandService(session_factory, handlers={CommandAction.CONVERT_TO_WORK_ITEM: decomposition.as_command_handler()})
    request = SessionCommandRequest(command_id="convert-retry", action=CommandAction.CONVERT_TO_WORK_ITEM, expected_state_version=7, actor_id="owner")

    with pytest.raises(CommandHandlerFailure, match="MISSING_OUTPUT"):
        await service.execute(project.session_id, request)
    recovered = await service.execute(project.session_id, request)

    assert recovered.state.phase is ProjectPhase.AGENT_SPECS_READY
    with session_factory() as db:
        assert db.query(CommandAttempt).filter_by(command_id=request.command_id, status="MATERIALIZED").count() == 1
        assert db.query(AgentCall).filter_by(project_id=project.id, operation="decompose_spec", status="FAILED").count() == 1
        assert db.query(AgentCall).filter_by(project_id=project.id, operation="decompose_spec", status="SUCCEEDED").count() == 1


@pytest.mark.asyncio
async def test_agent_exception_is_command_handler_failure_with_durable_call_reference(
    session_factory, db_session, complete_brief, valid_spec
):
    """Raw Agent exceptions would prevent CommandService from associating failure evidence."""
    project = _approved_project(db_session, complete_brief, valid_spec)
    agent = ScriptedAgentGateway(decompose_results=deque([RuntimeError("PM unavailable")]))
    decomposition = DecompositionService(session_factory, agent)
    service = CommandService(session_factory, handlers={CommandAction.CONVERT_TO_WORK_ITEM: decomposition.as_command_handler()})

    with pytest.raises(CommandHandlerFailure, match="PM unavailable") as error:
        await service.execute(project.session_id, SessionCommandRequest(command_id="convert-agent-failure", action=CommandAction.CONVERT_TO_WORK_ITEM, expected_state_version=7, actor_id="owner"))

    assert len(error.value.agent_call_ids) == 1
    with session_factory() as db:
        assert db.get(AgentCall, error.value.agent_call_ids[0]).status == "FAILED"


def test_action_uow_rejects_a_result_ready_call_from_another_operation(
    session_factory, db_session, complete_brief, valid_spec
):
    """A conversion handler must not complete a result belonging to another PM operation."""
    project = _approved_project(db_session, complete_brief, valid_spec)
    db_session.add(AgentCall(id="review-call", project_id=project.id, agent_session_id="pm-session-1", operation="review_spec", request={}, status="RESULT_READY"))
    db_session.commit()

    with session_factory() as db:
        with pytest.raises(ValueError, match="operation"):
            ActionScopedUnitOfWork(db, project.id, CommandAction.CONVERT_TO_WORK_ITEM).mark_decomposition_call_succeeded("review-call")
        with pytest.raises(AttributeError):
            getattr(ActionScopedUnitOfWork(db, project.id, CommandAction.CONVERT_TO_WORK_ITEM), "mark_agent_call_succeeded")
        db.rollback()

    with session_factory() as db:
        assert db.get(AgentCall, "review-call").status == "RESULT_READY"


def test_action_uow_reviewer_completion_is_scoped_to_review_breakdown(
    session_factory, db_session, complete_brief, valid_spec
):
    project = _approved_project(db_session, complete_brief, valid_spec)
    db_session.add(AgentCall(id="pm-call", project_id=project.id, agent_session_id="pm-session-1", operation="decompose_spec", request={}, status="RESULT_READY"))
    db_session.commit()

    with session_factory() as db:
        uow = ActionScopedUnitOfWork(db, project.id, CommandAction.CONVERT_TO_WORK_ITEM)
        with pytest.raises(ValueError, match="operation"):
            uow.mark_breakdown_reviewer_call_succeeded("pm-call")
        db.rollback()


@pytest.mark.asyncio
async def test_existing_root_key_is_rejected_during_prepare_with_stable_error(
    session_factory, db_session, complete_brief, valid_spec, valid_breakdown
):
    """A collision with the intake root must not surface as a database IntegrityError."""
    project = _approved_project(db_session, complete_brief, valid_spec)
    db_session.add(WorkItem(id="root-work-item", project_id=project.id, session_id=project.session_id, local_key="root", kind="ROOT", executable=False))
    db_session.commit()
    valid_breakdown.milestones[0].local_key = "root"
    valid_breakdown.tasks[0].parent_key = "root"
    valid_breakdown.tasks[1].parent_key = "root"
    agent = ScriptedAgentGateway(decompose_results=deque([valid_breakdown]))

    with pytest.raises(BreakdownValidationError, match=r"LOCAL_KEY_EXISTS \[root\]"):
        await DecompositionService(session_factory, agent).convert(project.id)


@pytest.mark.asyncio
async def test_write_failure_after_item_insert_rolls_back_all_workflow_rows(
    session_factory, db_session, complete_brief, valid_spec, valid_breakdown
):
    """A database failure after initial inserts must not leave a partially decomposed project."""
    project = _approved_project(db_session, complete_brief, valid_spec)
    agent = ScriptedAgentGateway(decompose_results=deque([valid_breakdown]))
    inserted = 0

    def fail_after_second_item(mapper, connection, target):
        nonlocal inserted
        inserted += 1
        if inserted == 2:
            raise RuntimeError("write interrupted")

    event.listen(WorkItem, "after_insert", fail_after_second_item)
    try:
        with pytest.raises(RuntimeError, match="write interrupted"):
            await DecompositionService(session_factory, agent).convert(project.id)
    finally:
        event.remove(WorkItem, "after_insert", fail_after_second_item)

    with session_factory() as db:
        current = db.get(Project, project.id)
        assert db.query(WorkItem).filter_by(project_id=project.id).count() == 0
        assert db.query(WorkItemDependency).filter_by(project_id=project.id).count() == 0
        assert db.query(AgentSpec).filter_by(project_id=project.id).count() == 0
        assert current.phase == ProjectPhase.REVIEW.value
        assert current.state_version == 7
        assert db.get(SpecVersion, current.current_spec_version_id).status == SpecStatus.APPROVED.value


@pytest.mark.asyncio
async def test_agent_failure_is_durably_recorded_without_advancing_project(
    session_factory, db_session, complete_brief, valid_spec
):
    """Losing an external PM failure would make a later conversion retry unauditable."""
    project = _approved_project(db_session, complete_brief, valid_spec)
    agent = ScriptedAgentGateway(decompose_results=deque([RuntimeError("PM unavailable")]))

    with pytest.raises(RuntimeError, match="PM unavailable"):
        await DecompositionService(session_factory, agent).convert(project.id)

    with session_factory() as db:
        assert db.get(Project, project.id).phase == ProjectPhase.REVIEW.value
        call = db.query(AgentCall).filter_by(project_id=project.id, operation="decompose_spec").one()
        assert call.status == "FAILED"
        assert call.error == "PM unavailable"


@pytest.mark.asyncio
async def test_structural_failure_prevents_reviewer_execution(
    session_factory, db_session, complete_brief, valid_spec, valid_breakdown
):
    project = _approved_project(db_session, complete_brief, valid_spec)
    valid_breakdown.agent_specs[1].outputs = []
    agent = ScriptedAgentGateway(decompose_results=deque([valid_breakdown]))

    with pytest.raises(BreakdownValidationError, match="MISSING_OUTPUT"):
        await DecompositionService(session_factory, agent).convert(project.id)

    assert [operation for operation, _ in agent.calls] == ["decompose_spec"]


@pytest.mark.asyncio
async def test_semantic_reviewer_blocks_without_persisting_breakdown_rows(
    session_factory, db_session, complete_brief, valid_spec, valid_breakdown
):
    project = _approved_project(db_session, complete_brief, valid_spec)
    valid_breakdown.agent_specs[1].fixed_constraints = ["Build External-deployment automation"]
    blocked = SemanticReview(
        verdict=ReviewVerdict.REJECT,
        findings=[ReviewFinding(code="EXCLUSION_CONTRADICTION", severity="BLOCKER", spec_path="agent_specs[t-api].fixed_constraints[0]", message="Excluded scope was reintroduced", suggested_resolution="Remove it", blocks_progress=True)],
    )
    agent = ScriptedAgentGateway(
        decompose_results=deque([valid_breakdown] * 3), review_breakdown_results=deque([blocked] * 3)
    )

    with pytest.raises(BreakdownValidationError, match=r"SEMANTIC_REVIEW_BLOCKED \[t-api\]"):
        await DecompositionService(session_factory, agent).convert(project.id)

    with session_factory() as db:
        assert db.query(WorkItem).filter_by(project_id=project.id).count() == 0
        assert db.query(AgentSpec).filter_by(project_id=project.id).count() == 0
        assert db.query(AgentCall).filter_by(project_id=project.id, operation="decompose_spec", status="RESULT_READY").count() == 3
        assert db.query(AgentCall).filter_by(project_id=project.id, operation="review_breakdown", status="RESULT_READY").count() == 3


@pytest.mark.asyncio
async def test_command_path_persists_and_completes_both_agent_calls(
    session_factory, db_session, complete_brief, valid_spec, valid_breakdown
):
    project = _approved_project(db_session, complete_brief, valid_spec)
    agent = ScriptedAgentGateway(decompose_results=deque([valid_breakdown]))
    decomposition = DecompositionService(session_factory, agent)
    service = CommandService(session_factory, handlers={CommandAction.CONVERT_TO_WORK_ITEM: decomposition.as_command_handler()})

    await service.execute(project.session_id, SessionCommandRequest(
        command_id="convert-two-calls", action=CommandAction.CONVERT_TO_WORK_ITEM,
        expected_state_version=7, actor_id="owner",
    ))

    with session_factory() as db:
        attempt = db.query(CommandAttempt).filter_by(command_id="convert-two-calls").one()
        assert len(attempt.agent_call_ids) == 2
        calls = [db.get(AgentCall, call_id) for call_id in attempt.agent_call_ids]
        assert [call.operation for call in calls] == ["decompose_spec", "review_breakdown"]
        assert [call.status for call in calls] == ["SUCCEEDED", "SUCCEEDED"]


@pytest.mark.asyncio
async def test_reviewer_payload_has_approved_exclusions_canonical_breakdown_and_nonsemantic_formatting(
    session_factory, db_session, complete_brief, valid_spec, valid_breakdown
):
    project = _approved_project(db_session, complete_brief, valid_spec)
    valid_breakdown.agent_specs[1].fixed_constraints = ["Build  API  documentation"]
    agent = ScriptedAgentGateway(decompose_results=deque([valid_breakdown]))

    await DecompositionService(session_factory, agent).convert(project.id)

    operation, payload = agent.calls[1]
    assert operation == "review_breakdown"
    assert payload["source_spec_version_id"] == "spec-decompose-1"
    assert payload["source_spec_content_hash"] == "d" * 64
    assert payload["approved_spec_exclusions"] == ["External deployment automation"]
    assert payload["approved_spec"] == valid_spec.model_dump(mode="json")
    assert payload["approved_spec"]["functional_requirements"][0]["requirement_id"] == "FR-001"
    assert payload["approved_spec"]["system_boundaries"] == valid_spec.system_boundaries
    assert payload["approved_spec"]["acceptance_criteria"][0]["criterion"] == valid_spec.acceptance_criteria[0].criterion
    assert payload["canonical_breakdown"]["agent_specs"][1]["fixed_constraints"] == ["Build  API  documentation"]
    assert payload["agent_spec_semantic_content"][1]["work_item_key"] == "t-api"
    prompt = build_node_prompt(objective="Review semantic consistency", input_payload=payload)
    evidence = prompt.split("<non_control_input>", 1)[1].split("</non_control_input>", 1)[0]
    assert "FR-001" in evidence
    assert valid_spec.system_boundaries[0] in evidence
    assert valid_spec.acceptance_criteria[0].criterion in evidence


@pytest.mark.asyncio
async def test_reviewer_execution_failure_is_durable_command_evidence_and_retries_safely(
    session_factory, db_session, complete_brief, valid_spec, valid_breakdown
):
    project = _approved_project(db_session, complete_brief, valid_spec)
    agent = ScriptedAgentGateway(
        decompose_results=deque([valid_breakdown, make_valid_breakdown()]),
        review_breakdown_results=deque([RuntimeError("Reviewer unavailable"), SemanticReview(verdict=ReviewVerdict.PASS, findings=[])]),
    )
    decomposition = DecompositionService(session_factory, agent)
    service = CommandService(session_factory, handlers={CommandAction.CONVERT_TO_WORK_ITEM: decomposition.as_command_handler()})
    request = SessionCommandRequest(command_id="convert-review-retry", action=CommandAction.CONVERT_TO_WORK_ITEM, expected_state_version=7, actor_id="owner")

    with pytest.raises(CommandHandlerFailure, match="Reviewer unavailable") as error:
        await service.execute(project.session_id, request)
    assert len(error.value.agent_call_ids) == 2
    recovered = await service.execute(project.session_id, request)
    assert recovered.state.phase is ProjectPhase.AGENT_SPECS_READY

    with session_factory() as db:
        failed_attempt = db.query(CommandAttempt).filter_by(command_id=request.command_id).one()
        assert len(failed_attempt.agent_call_ids) == 2
        assert db.get(AgentCall, error.value.agent_call_ids[0]).status == "RESULT_READY"
        assert db.get(AgentCall, error.value.agent_call_ids[1]).status == "FAILED"
        assert db.query(AgentSpec).filter_by(project_id=project.id).count() == 2


@pytest.mark.asyncio
async def test_passing_verdict_with_blocker_finding_is_semantically_rejected(
    session_factory, db_session, complete_brief, valid_spec, valid_breakdown
):
    project = _approved_project(db_session, complete_brief, valid_spec)
    contradictory = SemanticReview(
        verdict=ReviewVerdict.PASS,
        findings=[ReviewFinding(code="BOUNDARY_CONTRADICTION", severity="BLOCKER", spec_path="agent_specs[t-api].fixed_constraints[0]", message="Blocked", suggested_resolution="Remove", blocks_progress=False)],
    )
    agent = ScriptedAgentGateway(decompose_results=deque([valid_breakdown] * 3), review_breakdown_results=deque([contradictory] * 3))

    with pytest.raises(BreakdownValidationError, match=r"SEMANTIC_REVIEW_BLOCKED \[t-api\]"):
        await DecompositionService(session_factory, agent).convert(project.id)

    with session_factory() as db:
        assert db.query(AgentCall).filter_by(project_id=project.id, operation="review_breakdown", status="RESULT_READY").count() == 3


@pytest.mark.asyncio
async def test_command_semantic_rejection_keeps_successful_calls_and_records_rejected_attempt(
    session_factory, db_session, complete_brief, valid_spec, valid_breakdown
):
    project = _approved_project(db_session, complete_brief, valid_spec)
    review = SemanticReview(
        verdict=ReviewVerdict.NEED_INFO,
        findings=[ReviewFinding(code="NEEDS_DECISION", severity="MAJOR", spec_path="agent_specs[t-api].extension_points[0]", message="Need owner decision", suggested_resolution="Clarify", blocks_progress=False)],
    )
    agent = ScriptedAgentGateway(decompose_results=deque([valid_breakdown]), review_breakdown_results=deque([review]))
    service = CommandService(session_factory, handlers={CommandAction.CONVERT_TO_WORK_ITEM: DecompositionService(session_factory, agent).as_command_handler()})
    request = SessionCommandRequest(command_id="convert-semantic-reject", action=CommandAction.CONVERT_TO_WORK_ITEM, expected_state_version=7, actor_id="owner")

    with pytest.raises(CommandHandlerRejected, match="SEMANTIC_REVIEW_BLOCKED"):
        await service.execute(project.session_id, request)

    with session_factory() as db:
        attempt = db.query(CommandAttempt).filter_by(command_id=request.command_id).one()
        assert attempt.status == "REJECTED"
        assert len(attempt.agent_call_ids) == 2
        assert [db.get(AgentCall, call_id).status for call_id in attempt.agent_call_ids] == ["RESULT_READY", "RESULT_READY"]
        assert db.query(ProcessedCommand).filter_by(command_id=request.command_id).count() == 0
        assert db.query(WorkItem).filter_by(project_id=project.id).count() == 0
        assert db.query(AuditEvent).filter_by(project_id=project.id, event_type="COMMAND_HANDLER_REJECTED").count() == 1


@pytest.mark.asyncio
async def test_pass_with_nonblocking_major_finding_materializes_normally(
    session_factory, db_session, complete_brief, valid_spec, valid_breakdown
):
    project = _approved_project(db_session, complete_brief, valid_spec)
    review = SemanticReview(
        verdict=ReviewVerdict.PASS,
        findings=[ReviewFinding(code="FOLLOW_UP", severity="MAJOR", spec_path="agent_specs[t-api].risks[0]", message="Track later", suggested_resolution="Record risk", blocks_progress=False)],
    )
    agent = ScriptedAgentGateway(decompose_results=deque([valid_breakdown]), review_breakdown_results=deque([review]))

    created = await DecompositionService(session_factory, agent).convert(project.id)

    assert len(created) == 2


@pytest.mark.asyncio
async def test_semantic_rejection_is_retryable_with_new_review_evidence(
    session_factory, db_session, complete_brief, valid_spec, valid_breakdown
):
    project = _approved_project(db_session, complete_brief, valid_spec)
    rejected = SemanticReview(verdict=ReviewVerdict.NEED_INFO, findings=[])
    agent = ScriptedAgentGateway(
        decompose_results=deque([valid_breakdown, make_valid_breakdown()]),
        review_breakdown_results=deque([rejected, SemanticReview(verdict=ReviewVerdict.PASS, findings=[])]),
    )
    service = CommandService(session_factory, handlers={CommandAction.CONVERT_TO_WORK_ITEM: DecompositionService(session_factory, agent).as_command_handler()})
    request = SessionCommandRequest(command_id="convert-semantic-retry", action=CommandAction.CONVERT_TO_WORK_ITEM, expected_state_version=7, actor_id="owner")

    with pytest.raises(CommandHandlerRejected):
        await service.execute(project.session_id, request)
    with session_factory() as db:
        rejected_ids = list(db.query(CommandAttempt).filter_by(command_id=request.command_id).one().agent_call_ids)

    result = await service.execute(project.session_id, request)

    assert result.state.phase is ProjectPhase.AGENT_SPECS_READY
    with session_factory() as db:
        attempt = db.query(CommandAttempt).filter_by(command_id=request.command_id).one()
        assert attempt.status == "MATERIALIZED"
        assert attempt.agent_call_ids != rejected_ids
