"""Automatic repairs still obey the version, review, and atomic command gates."""

import pytest

from app.database.models import AgentCall, AgentSpec, ProcessedCommand, Project, SpecVersion, WorkItem
from app.domain.types import CommandAction, ProjectPhase, ReviewVerdict, SemanticReview
from app.schemas.workflow import SessionCommandRequest
from app.services.command_service import CommandHandlerFailure, CommandService
from app.services.decomposition_service import DecompositionService, DecompositionAgentFailure, SemanticReviewRejected
from app.services.spec_service import SpecService
from tests.helpers.scripted_codex import gateway_with_outputs
from tests.integration.test_decomposition_service import _approved_project
from tests.integration.test_spec_service import _add_specification_project


@pytest.mark.asyncio
async def test_generated_repairs_are_saved_as_one_reviewed_version(
    tmp_path, monkeypatch, session_factory, db_session, complete_brief, valid_spec, passing_semantic_review
):
    project = _add_specification_project(db_session, complete_brief)
    valid_spec.source_refs = ["artifact:original-artifact-1"]
    bad = valid_spec.model_copy(deep=True)
    bad.acceptance_criteria[0].requirement_ids = ["deliverable-001"]
    gateway, _ = gateway_with_outputs(tmp_path, monkeypatch, [bad, valid_spec, passing_semantic_review])

    specs = SpecService(session_factory, gateway)
    commands = CommandService(session_factory, handlers={CommandAction.CREATE_SPEC: specs.as_command_handler()})
    result = await commands.execute(project.session_id, SessionCommandRequest(
        command_id="create-repaired", action=CommandAction.CREATE_SPEC, expected_state_version=1, actor_id="owner",
    ))

    assert result.state.current_spec_status == "HUMAN_REVIEW"
    with session_factory() as db:
        version = db.query(SpecVersion).filter_by(project_id=project.id).one()
        assert version.content["acceptance_criteria"][0]["requirement_ids"] == ["FR-001"]
        assert db.query(AgentCall).filter_by(project_id=project.id, operation="review_spec").count() == 1


@pytest.mark.asyncio
async def test_repaired_generation_keeps_state_and_versions_atomic_when_reviewer_fails(
    tmp_path, monkeypatch, session_factory, db_session, complete_brief, valid_spec
):
    from app.agents.codex import AgentExecutionError

    project = _add_specification_project(db_session, complete_brief)
    valid_spec.source_refs = ["artifact:original-artifact-1"]
    bad = valid_spec.model_copy(deep=True)
    bad.acceptance_criteria[0].requirement_ids = ["deliverable-001"]
    gateway, _ = gateway_with_outputs(tmp_path, monkeypatch, [bad, valid_spec, AgentExecutionError("Reviewer unavailable")])
    specs = SpecService(session_factory, gateway)
    commands = CommandService(session_factory, handlers={CommandAction.CREATE_SPEC: specs.as_command_handler()})

    with pytest.raises(CommandHandlerFailure):
        await commands.execute(project.session_id, SessionCommandRequest(
            command_id="create-review-fails", action=CommandAction.CREATE_SPEC, expected_state_version=1, actor_id="owner",
        ))

    with session_factory() as db:
        current = db.get(Project, project.id)
        assert current.state_version == 1
        assert current.phase == "SPECIFICATION"
        assert current.current_spec_version_id is None
        assert db.query(SpecVersion).count() == 0
        assert db.query(ProcessedCommand).count() == 0


@pytest.mark.asyncio
async def test_repaired_breakdown_is_atomic_and_replay_does_not_repeat_agent_work(
    tmp_path, monkeypatch, session_factory, db_session, complete_brief, valid_spec, valid_breakdown, passing_semantic_review
):
    valid_spec.acceptance_criteria[0].requirement_ids = ["deliverable-001"]
    project = _approved_project(db_session, complete_brief, valid_spec)
    bad = valid_breakdown.model_copy(deep=True)
    bad.agent_specs[0].acceptance_criteria[0].requirement_ids = ["deliverable-001"]
    gateway, prompts = gateway_with_outputs(tmp_path, monkeypatch, [bad, valid_breakdown, passing_semantic_review])
    decomposition = DecompositionService(session_factory, gateway)
    commands = CommandService(session_factory, handlers={CommandAction.CONVERT_TO_WORK_ITEM: decomposition.as_command_handler()})
    request = SessionCommandRequest(command_id="auto-repaired", action=CommandAction.CONVERT_TO_WORK_ITEM,
                                    expected_state_version=7, actor_id="owner")

    first = await commands.execute(project.session_id, request)
    replay = await commands.execute(project.session_id, request)

    assert first.state.phase is ProjectPhase.AGENT_SPECS_READY
    assert replay.created_resource_ids == first.created_resource_ids
    assert len(prompts) == 3
    with session_factory() as db:
        assert db.query(AgentSpec).count() == 2
        assert db.query(ProcessedCommand).filter_by(command_id="auto-repaired").count() == 1
        approved = db.get(SpecVersion, project.current_spec_version_id)
        assert approved.content == valid_spec.model_dump(mode="json")
        assert approved.content_hash == "d" * 64


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["repair_exhausted", "semantic_rejection"])
async def test_failed_repair_or_review_keeps_approved_state_and_no_partial_tasks(
    tmp_path, monkeypatch, session_factory, db_session, complete_brief, valid_spec, valid_breakdown, failure
):
    project = _approved_project(db_session, complete_brief, valid_spec)
    bad = valid_breakdown.model_copy(deep=True)
    bad.agent_specs[0].acceptance_criteria[0].requirement_ids = ["deliverable-001"]
    outputs = [bad, bad, bad] if failure == "repair_exhausted" else [
        bad, valid_breakdown, SemanticReview(verdict=ReviewVerdict.REJECT, findings=[]),
    ]
    gateway, _ = gateway_with_outputs(tmp_path, monkeypatch, outputs)
    expected = DecompositionAgentFailure if failure == "repair_exhausted" else SemanticReviewRejected

    with pytest.raises(expected):
        await DecompositionService(session_factory, gateway).convert(project.id)

    with session_factory() as db:
        assert db.get(Project, project.id).state_version == 7
        assert db.get(SpecVersion, project.current_spec_version_id).status == "APPROVED"
        assert db.query(AgentSpec).count() == 0
        assert db.query(WorkItem).count() == 0
