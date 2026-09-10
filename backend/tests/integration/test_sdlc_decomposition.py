from collections import deque
from datetime import UTC, datetime

import pytest

from app.database.models import AgentCall, AgentSpec, SdlcRouteDecision, WorkItem, WorkItemDependency
from app.domain.sdlc import LifecycleAssessment
from app.domain.types import (
    CommandAction,
    ProjectPhase,
    ProjectSpecPayload,
    ReviewFinding,
    ReviewVerdict,
    SemanticReview,
    WorkBreakdownRevision,
)
from app.schemas.workflow import SessionCommandRequest
from app.agents.output_validation import validate_node_output, merge_breakdown_revision, OutputConsistencyError
from app.services.command_service import CommandService
from app.services.decomposition_service import (
    BreakdownValidationError,
    DecompositionPlanningFailure,
    DecompositionService,
    SemanticReviewRejected,
    validate_breakdown,
)
from app.services.sdlc_rules import planning_context, recommend_models
from tests.helpers.fake_agent import ScriptedAgentGateway
from tests.helpers.sdlc import lifecycle_fixture
from tests.integration.test_decomposition_service import _approved_project, _plan_for


def _approved_sdlc_project(db, brief, approved, model='strict_waterfall'):
    project = _approved_project(db, brief, ProjectSpecPayload.model_validate(approved))
    yes = {'strict_waterfall': {1, 2, 3, 4}, 'overlapping_waterfall': {5, 6, 7},
           'iterative_incremental': {8, 9, 10}}[model]
    assessment = LifecycleAssessment(
        answers=[{'question_id': f'q{i}', 'answer': 'yes' if i in yes else 'no',
                  'evidence': f'Synthetic q{i} route evidence'} for i in range(1, 11)],
        summary='Synthetic route confirmation for lifecycle integration tests',
    )
    decision = recommend_models(assessment)
    db.add(SdlcRouteDecision(
        id=f'route-{project.id}', project_id=project.id,
        rules_version=decision.rules_version, rules_hash=decision.rules_hash,
        assessment=assessment.model_dump(mode='json'),
        options=[option.model_dump(mode='json') for option in decision.options],
        selected_model=model, selected_by='owner', selected_at=datetime.now(UTC),
    ))
    db.commit()
    return project


@pytest.mark.asyncio
@pytest.mark.parametrize('model', ['strict_waterfall', 'overlapping_waterfall', 'iterative_incremental'])
async def test_default_service_persists_valid_lifecycle_and_task_gate_edges(
    session_factory, db_session, complete_brief, model,
):
    approved, breakdown = lifecycle_fixture(model)
    project = _approved_sdlc_project(db_session, complete_brief, approved, model)
    agent = ScriptedAgentGateway(decompose_results=deque([breakdown]),
                                 plan_results=deque(_plan_for(t) for t in breakdown.agent_specs))
    specs = await DecompositionService(session_factory, agent).convert(project.id)
    assert len(specs) == len(breakdown.tasks)
    assert all(s.content['sdlc']['model'] == model for s in specs)
    assert all(s.content['sdlc']['rules_hash'] == breakdown.lifecycle.rules_hash for s in specs)
    assert all('sdlc_rules' in payload for _, payload in agent.calls)
    assert all(payload['task_lifecycle']['model'] == model for op, payload in agent.calls if op == 'plan_task')
    with session_factory() as db:
        items = {i.local_key: i for i in db.query(WorkItem).filter_by(project_id=project.id)}
        for task in breakdown.tasks:
            dependencies = {d.to_work_item_id for d in db.query(WorkItemDependency).filter_by(from_work_item_id=items[task.local_key].id)}
            assert dependencies == {items[key].id for key in task.dependency_keys}


@pytest.mark.asyncio
async def test_command_path_and_idempotency_keep_rule_metadata(session_factory, db_session, complete_brief):
    approved, breakdown = lifecycle_fixture()
    project = _approved_sdlc_project(db_session, complete_brief, approved)
    agent = ScriptedAgentGateway(decompose_results=deque([breakdown]),
                                 plan_results=deque(_plan_for(t) for t in breakdown.agent_specs))
    service = CommandService(session_factory, handlers={CommandAction.CONVERT_TO_WORK_ITEM:
                             DecompositionService(session_factory, agent).as_command_handler()})
    request = SessionCommandRequest(command_id='sdlc-convert', action=CommandAction.CONVERT_TO_WORK_ITEM,
                                    expected_state_version=7, actor_id='owner')
    first = await service.execute(project.session_id, request)
    assert first.state.phase is ProjectPhase.AGENT_SPECS_READY
    count = len(agent.calls)
    await service.execute(project.session_id, request)
    assert len(agent.calls) == count
    with session_factory() as db:
        assert db.query(AgentSpec).count() == len(breakdown.tasks)
        assert all(s.content['sdlc']['model'] == 'strict_waterfall' for s in db.query(AgentSpec))


@pytest.mark.asyncio
async def test_new_command_inherits_review_and_replans_numeric_index_target(
    session_factory, db_session, complete_brief, passing_semantic_review,
):
    """A fresh command retains review lessons and reuses unaffected checkpoints."""

    approved, breakdown = lifecycle_fixture()
    project = _approved_sdlc_project(db_session, complete_brief, approved)
    base = breakdown.model_copy(deep=True)
    for task in base.agent_specs:
        task.implementation_plan = None
    target_index = len(base.agent_specs) - 1
    target = base.agent_specs[target_index]
    rejected = SemanticReview(verdict=ReviewVerdict.REJECT, findings=[
        ReviewFinding(
            code='INDEXED_PLAN_CONTRACT_MISMATCH',
            severity='BLOCKER',
            spec_path=f'agent_specs[{target_index}].implementation_plan.steps[0]',
            message='The indexed task plan violates its assigned contract.',
            suggested_resolution='Regenerate that task plan from this feedback.',
            blocks_progress=True,
        )
    ])
    first_agent = ScriptedAgentGateway(
        decompose_results=deque([base]),
        plan_results=deque([
            *(_plan_for(task) for task in base.agent_specs),
            _plan_for(target),
        ]),
        review_breakdown_results=deque([rejected, rejected]),
    )
    with pytest.raises(SemanticReviewRejected):
        await DecompositionService(session_factory, first_agent).prepare(
            project.id, command_id='old-command', input_hash='old-input',
        )

    second_agent = ScriptedAgentGateway(
        plan_results=deque([_plan_for(target)]),
        review_breakdown_results=deque([passing_semantic_review]),
    )
    prepared = await DecompositionService(session_factory, second_agent).prepare(
        project.id, command_id='new-command', input_hash='new-input',
    )

    assert len(prepared.plan_agent_call_ids) == len(base.agent_specs)
    assert [operation for operation, _ in second_agent.calls] == [
        'plan_task', 'review_breakdown',
    ]
    repair_payload = second_agent.calls[0][1]
    assert repair_payload['task_spec']['work_item_key'] == target.work_item_key
    assert repair_payload['previous_plan'] is not None
    assert repair_payload['review_feedback'] == rejected.model_dump(mode='json')
    assert repair_payload['previous_review_call_id']


@pytest.mark.asyncio
async def test_new_command_passes_prior_task_validation_failure_to_model(
    session_factory, db_session, complete_brief, passing_semantic_review,
):
    approved, breakdown = lifecycle_fixture()
    project = _approved_sdlc_project(db_session, complete_brief, approved)
    base = breakdown.model_copy(deep=True)
    for task in base.agent_specs:
        task.implementation_plan = None
    target = base.agent_specs[-1]
    previous_error = (
        "UNASSIGNED_IMPLEMENTATION_REQUIREMENT: step references FR-009 "
        "outside this task"
    )
    first_agent = ScriptedAgentGateway(
        decompose_results=deque([base]),
        plan_results=deque([
            *(_plan_for(task) for task in base.agent_specs[:-1]),
            RuntimeError(previous_error),
        ]),
    )
    with pytest.raises(DecompositionPlanningFailure):
        await DecompositionService(session_factory, first_agent).prepare(
            project.id, command_id='failed-command', input_hash='failed-input',
        )

    second_agent = ScriptedAgentGateway(
        plan_results=deque([_plan_for(target)]),
        review_breakdown_results=deque([passing_semantic_review]),
    )
    await DecompositionService(session_factory, second_agent).prepare(
        project.id, command_id='retry-command', input_hash='retry-input',
    )

    assert [operation for operation, _ in second_agent.calls] == [
        'plan_task', 'review_breakdown',
    ]
    retry_payload = second_agent.calls[0][1]
    assert retry_payload['task_spec']['work_item_key'] == target.work_item_key
    assert retry_payload['prior_validation_failure']['error'] == previous_error


@pytest.mark.asyncio
@pytest.mark.parametrize('invalid', ['missing_plan', 'missing_dependency', 'stale_rules'])
async def test_invalid_plan_stops_before_task_llm_calls_and_writes(
    session_factory, db_session, complete_brief, invalid,
):
    approved, breakdown = lifecycle_fixture()
    project = _approved_sdlc_project(db_session, complete_brief, approved)
    if invalid == 'missing_plan':
        breakdown.lifecycle = None
    elif invalid == 'stale_rules':
        breakdown.lifecycle.rules_hash = 'old'
    else:
        breakdown.tasks[2].dependency_keys.clear()
        breakdown.agent_specs[2].dependency_keys.clear()
    agent = ScriptedAgentGateway(decompose_results=deque([breakdown]))
    with pytest.raises(BreakdownValidationError, match='SDLC_'):
        await DecompositionService(session_factory, agent).convert(project.id)
    assert [op for op, _ in agent.calls] == ['decompose_spec']
    with session_factory() as db:
        assert db.query(AgentSpec).count() == db.query(WorkItem).count() == 0
        call = db.query(AgentCall).one()
        assert call.status == 'FAILED'
        assert 'SDLC_' in str(call.error)


@pytest.mark.asyncio
async def test_tampering_after_prepare_is_revalidated_before_commit(session_factory, db_session, complete_brief):
    approved, breakdown = lifecycle_fixture()
    project = _approved_sdlc_project(db_session, complete_brief, approved)
    agent = ScriptedAgentGateway(decompose_results=deque([breakdown]),
                                 plan_results=deque(_plan_for(t) for t in breakdown.agent_specs))
    service = DecompositionService(session_factory, agent)
    prepared = await service.prepare(project.id)
    prepared.breakdown.lifecycle = None
    with pytest.raises(BreakdownValidationError, match='SDLC_PLAN_REQUIRED'):
        service._persist_direct(prepared)
    with session_factory() as db:
        assert db.query(AgentSpec).count() == db.query(WorkItem).count() == 0


def test_node_repair_keeps_lifecycle_and_diagnoses_invalid_gates():
    approved, breakdown = lifecycle_fixture()
    payload = {'approved_spec': approved, 'input_refs': approved['source_refs'],
               'decomposition_stage': 'base', 'sdlc_rules': planning_context(),
               'previous_breakdown': breakdown.model_dump(mode='json')}
    merged = merge_breakdown_revision(WorkBreakdownRevision(), payload)
    assert merged.lifecycle == breakdown.lifecycle
    validate_node_output(merged, payload)
    merged.lifecycle = None
    with pytest.raises(OutputConsistencyError, match='SDLC_PLAN_REQUIRED'):
        validate_node_output(merged, payload)


@pytest.mark.parametrize('model', ['strict_waterfall', 'overlapping_waterfall'])
def test_node_normalizes_waterfall_stage_numbers_mistaken_for_iterations(model):
    approved, breakdown = lifecycle_fixture(model)
    for stage_number, phase in enumerate(breakdown.lifecycle.phases, start=1):
        phase.iteration = stage_number
    payload = {
        'approved_spec': approved,
        'input_refs': approved['source_refs'],
        'decomposition_stage': 'base',
        'sdlc_rules': planning_context(),
        'selected_sdlc_model': model,
    }

    validate_node_output(breakdown, payload)

    assert {phase.iteration for phase in breakdown.lifecycle.phases} == {0}
    validate_breakdown(
        breakdown,
        approved,
        require_implementation_plan=False,
        require_lifecycle=True,
    )
