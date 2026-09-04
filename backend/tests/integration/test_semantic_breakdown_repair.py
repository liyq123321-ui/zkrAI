"""Semantic feedback must revise the breakdown under the same approved PRD."""

import asyncio
from collections import deque

import pytest

from app.database.models import AgentCall, AgentSpec, AuditEvent, Project, SpecVersion, WorkItem
from app.domain.types import AgentSpecProposal, CommandAction, ReviewFinding, ReviewVerdict, SemanticReview
from app.schemas.workflow import SessionCommandRequest
from app.services.command_service import CommandHandlerRejected, CommandService
from app.services.decomposition_service import DecompositionService, DecompositionNotAllowed, SemanticReviewRejected
from tests.helpers.fake_agent import ScriptedAgentGateway
from tests.helpers.implementation_plans import implementation_plan
from tests.helpers.scripted_codex import gateway_with_outputs
from tests.integration.test_decomposition_service import _approved_project, _plan_for


def source_review():
    return SemanticReview(verdict=ReviewVerdict.REJECT, findings=[ReviewFinding(
        code="SOURCE_EXTENSION", severity="BLOCKER", spec_path="agent_specs[t-api].extension_points[0]",
        message="Source extension is not restricted to approved sources.",
        suggested_resolution="Limit extension to sources approved by the owner.", blocks_progress=True,
    )])


def command_service(session_factory, agent):
    return CommandService(session_factory, handlers={
        CommandAction.CONVERT_TO_WORK_ITEM: DecompositionService(session_factory, agent).as_command_handler(),
    })


@pytest.mark.asyncio
async def test_staged_review_repairs_only_the_implicated_task_plan(
    session_factory, db_session, complete_brief, valid_spec, valid_breakdown,
    passing_semantic_review,
):
    """A plan finding must not regenerate the base tree or unaffected plans."""

    project = _approved_project(db_session, complete_brief, valid_spec)
    base = valid_breakdown.model_copy(deep=True)
    for task in base.agent_specs:
        task.implementation_plan = None
    rejected = SemanticReview(verdict=ReviewVerdict.REJECT, findings=[
        ReviewFinding(
            code="UNASSIGNED_IMPLEMENTATION_REQUIREMENT",
            severity="BLOCKER",
            spec_path="agent_specs[t-api].implementation_plan.steps[0]",
            message="The step references requirements outside this task.",
            suggested_resolution="Regenerate only the t-api implementation plan.",
            blocks_progress=True,
        )
    ])
    agent = ScriptedAgentGateway(
        decompose_results=deque([base]),
        plan_results=deque([
            _plan_for(base.agent_specs[0]),
            _plan_for(base.agent_specs[1]),
            _plan_for(base.agent_specs[1]),
        ]),
        review_breakdown_results=deque([rejected, passing_semantic_review]),
    )

    specs = await DecompositionService(session_factory, agent).convert(project.id)

    assert len(specs) == 2
    assert [operation for operation, _ in agent.calls] == [
        "decompose_spec",
        "plan_task",
        "plan_task",
        "review_breakdown",
        "plan_task",
        "review_breakdown",
    ]
    repaired = agent.calls[4][1]
    assert repaired["task_spec"]["work_item_key"] == "t-api"
    assert repaired["previous_plan"] is not None
    assert repaired["review_feedback"] == rejected.model_dump(mode="json")
    assert repaired["previous_review_call_id"]


@pytest.mark.asyncio
async def test_staged_review_keeps_plan_evidence_order_when_findings_are_reversed(
    session_factory, db_session, complete_brief, valid_spec, valid_breakdown,
    passing_semantic_review,
):
    project = _approved_project(db_session, complete_brief, valid_spec)
    base = valid_breakdown.model_copy(deep=True)
    for task in base.agent_specs:
        task.implementation_plan = None
    rejected = SemanticReview(verdict=ReviewVerdict.REJECT, findings=[
        ReviewFinding(
            code="API_PLAN", severity="BLOCKER",
            spec_path="agent_specs[t-api].implementation_plan.steps[0]",
            message="Repair API plan", suggested_resolution="Repair it",
            blocks_progress=True,
        ),
        ReviewFinding(
            code="DOMAIN_PLAN", severity="BLOCKER",
            spec_path="agent_specs[t-domain].implementation_plan.steps[0]",
            message="Repair domain plan", suggested_resolution="Repair it",
            blocks_progress=True,
        ),
    ])
    agent = ScriptedAgentGateway(
        decompose_results=deque([base]),
        plan_results=deque([
            _plan_for(base.agent_specs[0]), _plan_for(base.agent_specs[1]),
            _plan_for(base.agent_specs[0]), _plan_for(base.agent_specs[1]),
        ]),
        review_breakdown_results=deque([rejected, passing_semantic_review]),
    )

    await DecompositionService(session_factory, agent).convert(project.id)

    with session_factory() as db:
        final_review = db.query(AgentCall).filter_by(
            project_id=project.id, operation="review_breakdown",
            status="SUCCEEDED",
        ).one()
        planned_keys = [
            db.get(AgentCall, call_id).request["task_spec"]["work_item_key"]
            for call_id in final_review.request["plan_agent_call_ids"]
        ]
    assert planned_keys == ["t-domain", "t-api"]


@pytest.mark.asyncio
async def test_cancelled_task_planner_settles_every_started_call(
    session_factory, db_session, complete_brief, valid_spec, valid_breakdown,
):
    project = _approved_project(db_session, complete_brief, valid_spec)
    base = valid_breakdown.model_copy(deep=True)
    for task in base.agent_specs:
        task.implementation_plan = None

    class CancelledPlanningGateway(ScriptedAgentGateway):
        async def plan_task(self, payload):
            self.calls.append(("plan_task", payload))
            if payload["task_spec"]["work_item_key"] == "t-domain":
                raise asyncio.CancelledError()
            return _plan_for(AgentSpecProposal.model_validate(payload["task_spec"]))

    agent = CancelledPlanningGateway(decompose_results=deque([base]))

    with pytest.raises(asyncio.CancelledError):
        await DecompositionService(session_factory, agent).convert(project.id)

    with session_factory() as db:
        plan_calls = db.query(AgentCall).filter_by(
            project_id=project.id, operation="plan_task"
        ).all()
        assert len(plan_calls) == 2
        assert all(call.status != "PENDING" for call in plan_calls)


@pytest.mark.asyncio
async def test_semantic_revision_runs_through_real_gateway_validation(
    tmp_path, monkeypatch, session_factory, db_session, complete_brief, valid_spec, valid_breakdown, passing_semantic_review
):
    project = _approved_project(db_session, complete_brief, valid_spec)
    revised = valid_breakdown.model_copy(deep=True)
    revised.agent_specs[1].extension_points = ["Extend only within approved boundaries"]
    import json

    replacements = json.dumps({
        "milestones": [], "tasks": [],
        "agent_specs": [revised.agent_specs[1].model_dump(mode="json")],
    })
    plans = [
        implementation_plan(sorted({
            requirement_id
            for criterion in task.acceptance_criteria
            for requirement_id in criterion.requirement_ids
        }))
        for task in valid_breakdown.agent_specs
    ]
    gateway, prompts = gateway_with_outputs(tmp_path, monkeypatch, [
        valid_breakdown,
        *map(json.dumps, plans),
        source_review(),
        replacements,
        json.dumps(plans[0]),
        json.dumps(plans[1]),
        passing_semantic_review,
    ])

    specs = await DecompositionService(session_factory, gateway).convert(project.id)

    assert len(specs) == 2
    assert len(prompts) == 8
    assert '"previous_breakdown"' in prompts[4]
    assert '"SOURCE_EXTENSION"' in prompts[4]
    assert any(s.content["extension_points"] == ["Extend only within approved boundaries"] for s in specs)
    assert any(s.content["extension_points"] == valid_breakdown.agent_specs[0].extension_points for s in specs)


@pytest.mark.asyncio
async def test_semantic_feedback_repairs_then_rechecks_before_atomic_commit(
    session_factory, db_session, complete_brief, valid_spec, valid_breakdown, passing_semantic_review
):
    project = _approved_project(db_session, complete_brief, valid_spec)
    fixed = valid_breakdown.model_copy(deep=True)
    fixed.agent_specs[1].extension_points = ["Only owner-approved sources may be added"]
    agent = ScriptedAgentGateway(decompose_results=deque([valid_breakdown, fixed]),
                                 review_breakdown_results=deque([source_review(), passing_semantic_review]))
    commands = command_service(session_factory, agent)
    request = SessionCommandRequest(command_id="semantic-repair", action=CommandAction.CONVERT_TO_WORK_ITEM,
                                    expected_state_version=7, actor_id="owner")

    first = await commands.execute(project.session_id, request)
    replay = await commands.execute(project.session_id, request)

    assert first.state.phase == "AGENT_SPECS_READY"
    assert first.created_resource_ids == replay.created_resource_ids
    assert len(agent.calls) == 4
    repair = agent.calls[2][1]
    assert repair["approved_spec"] == valid_spec.model_dump(mode="json")
    assert repair["previous_breakdown"] == valid_breakdown.model_dump(mode="json")
    assert repair["review_feedback"]["findings"][0]["code"] == "SOURCE_EXTENSION"
    with session_factory() as db:
        assert db.get(SpecVersion, project.current_spec_version_id).content == valid_spec.model_dump(mode="json")
        assert db.get(SpecVersion, project.current_spec_version_id).content_hash == "d" * 64
        assert db.get(Project, project.id).state_version == 8
        specs = db.query(AgentSpec).filter_by(project_id=project.id).all()
        assert len(specs) == 2
        assert any(s.content["extension_points"] == ["Only owner-approved sources may be added"] for s in specs)
        assert db.query(AgentCall).filter_by(status="SUCCEEDED").count() == 2
        assert db.query(AgentCall).filter_by(status="RESULT_READY").count() == 2
        assert db.query(AuditEvent).filter_by(event_type="DECOMPOSITION_REVIEW_REJECTED").count() == 1


@pytest.mark.asyncio
async def test_repair_exhaustion_preserves_all_reviews_without_partial_work(
    session_factory, db_session, complete_brief, valid_spec, valid_breakdown
):
    project = _approved_project(db_session, complete_brief, valid_spec)
    agent = ScriptedAgentGateway(decompose_results=deque([valid_breakdown] * 3),
                                 review_breakdown_results=deque([source_review()] * 3))

    with pytest.raises(SemanticReviewRejected):
        await DecompositionService(session_factory, agent).convert(project.id)

    assert len(agent.calls) == 6
    with session_factory() as db:
        assert db.get(Project, project.id).state_version == 7
        assert db.query(AgentSpec).count() == 0
        assert db.query(WorkItem).count() == 0
        assert db.query(AgentCall).count() == 6
        assert db.query(AuditEvent).filter_by(event_type="DECOMPOSITION_REVIEW_REJECTED").count() == 3


@pytest.mark.asyncio
async def test_existing_rejection_is_revised_without_generating_a_new_breakdown(
    session_factory, db_session, complete_brief, valid_spec, valid_breakdown, passing_semantic_review
):
    project = _approved_project(db_session, complete_brief, valid_spec)
    # A durable pre-upgrade rejection: no repair_round or parent-call metadata.
    old_request = {
        "project_id": project.id, "source_spec_version_id": project.current_spec_version_id,
        "source_spec_content_hash": "d" * 64, "approved_spec": valid_spec.model_dump(mode="json"),
        "input_refs": ["artifact:brief-1"],
        "canonical_breakdown": valid_breakdown.model_dump(mode="json"),
    }
    db_session.add(AgentCall(id="old-review", project_id=project.id, agent_session_id="reviewer",
                             operation="review_breakdown", status="RESULT_READY", request=old_request,
                             response=source_review().model_dump(mode="json")))
    db_session.commit()
    agent = ScriptedAgentGateway(decompose_results=deque([valid_breakdown]),
                                 review_breakdown_results=deque([passing_semantic_review]))

    await DecompositionService(session_factory, agent).convert(project.id)

    assert len(agent.calls) == 2
    assert agent.calls[0][1]["previous_breakdown"] == valid_breakdown.model_dump(mode="json")
    assert agent.calls[0][1]["previous_review_call_id"] == "old-review"


@pytest.mark.asyncio
async def test_changed_prd_cannot_reuse_stale_semantic_feedback(
    session_factory, db_session, complete_brief, valid_spec, valid_breakdown, passing_semantic_review
):
    project = _approved_project(db_session, complete_brief, valid_spec)
    db_session.add(AgentCall(id="stale-review", project_id=project.id, agent_session_id="reviewer",
        operation="review_breakdown", status="RESULT_READY", request={
            "source_spec_version_id": "old-spec", "source_spec_content_hash": "old-hash",
            "approved_spec": valid_spec.model_dump(mode="json"),
            "canonical_breakdown": valid_breakdown.model_dump(mode="json"),
        }, response=source_review().model_dump(mode="json")))
    db_session.commit()
    agent = ScriptedAgentGateway(decompose_results=deque([valid_breakdown]),
                                 review_breakdown_results=deque([passing_semantic_review]))

    await DecompositionService(session_factory, agent).convert(project.id)

    assert "previous_breakdown" not in agent.calls[0][1]


@pytest.mark.asyncio
async def test_changed_input_refs_cannot_reuse_semantic_feedback(
    session_factory, db_session, complete_brief, valid_spec, valid_breakdown,
    passing_semantic_review,
):
    project = _approved_project(db_session, complete_brief, valid_spec)
    db_session.add(AgentCall(
        id="stale-input-review", project_id=project.id,
        agent_session_id="reviewer", operation="review_breakdown",
        status="RESULT_READY", request={
            "project_id": project.id,
            "source_spec_version_id": project.current_spec_version_id,
            "source_spec_content_hash": "d" * 64,
            "approved_spec": valid_spec.model_dump(mode="json"),
            "input_refs": ["artifact:different-source"],
            "canonical_breakdown": valid_breakdown.model_dump(mode="json"),
        }, response=source_review().model_dump(mode="json"),
    ))
    db_session.commit()
    agent = ScriptedAgentGateway(
        decompose_results=deque([valid_breakdown]),
        review_breakdown_results=deque([passing_semantic_review]),
    )

    await DecompositionService(session_factory, agent).convert(project.id)

    assert "previous_breakdown" not in agent.calls[0][1]


@pytest.mark.asyncio
async def test_prd_change_during_review_stops_automatic_repair(
    session_factory, db_session, complete_brief, valid_spec, valid_breakdown
):
    project = _approved_project(db_session, complete_brief, valid_spec)

    def review_then_change(payload):
        with session_factory() as db:
            version = db.get(SpecVersion, project.current_spec_version_id)
            version.status = "REWORK"
            db.commit()
        return source_review()

    agent = ScriptedAgentGateway(decompose_results=deque([valid_breakdown]),
                                 review_breakdown_results=deque([review_then_change]))

    with pytest.raises(DecompositionNotAllowed):
        await DecompositionService(session_factory, agent).convert(project.id)

    assert len(agent.calls) == 2


@pytest.mark.asyncio
async def test_resuming_same_command_retains_exhausted_semantic_budget(
    session_factory, db_session, complete_brief, valid_spec, valid_breakdown, passing_semantic_review
):
    project = _approved_project(db_session, complete_brief, valid_spec)
    db_session.add(AgentCall(id="exhausted-review", project_id=project.id, agent_session_id="reviewer",
        operation="review_breakdown", status="RESULT_READY", request={
            "project_id": project.id, "source_spec_version_id": project.current_spec_version_id,
            "source_spec_content_hash": "d" * 64, "approved_spec": valid_spec.model_dump(mode="json"),
            "input_refs": ["artifact:brief-1"],
            "canonical_breakdown": valid_breakdown.model_dump(mode="json"),
            "command_id": "same-command", "input_hash": "same-input", "repair_round": 2,
        }, response=source_review().model_dump(mode="json")))
    db_session.commit()
    agent = ScriptedAgentGateway(decompose_results=deque([valid_breakdown]),
                                 review_breakdown_results=deque([passing_semantic_review]))

    with pytest.raises(SemanticReviewRejected):
        await DecompositionService(session_factory, agent).prepare(
            project.id, command_id="same-command", input_hash="same-input",
        )

    assert agent.calls == []


@pytest.mark.asyncio
async def test_command_commit_rechecks_full_prd_snapshot_after_final_review(
    session_factory, db_session, complete_brief, valid_spec, valid_breakdown, passing_semantic_review
):
    project = _approved_project(db_session, complete_brief, valid_spec)

    def change_same_version(payload):
        with session_factory() as db:
            version = db.get(SpecVersion, project.current_spec_version_id)
            version.content = {**version.content, "exclusions": ["Changed approved scope"]}
            version.content_hash = "e" * 64
            db.commit()
        return passing_semantic_review

    agent = ScriptedAgentGateway(decompose_results=deque([valid_breakdown]),
                                 review_breakdown_results=deque([change_same_version]))

    with pytest.raises(ValueError, match="approved Spec changed"):
        await command_service(session_factory, agent).execute(project.session_id, SessionCommandRequest(
            command_id="stale-snapshot", action=CommandAction.CONVERT_TO_WORK_ITEM,
            expected_state_version=7, actor_id="owner",
        ))

    with session_factory() as db:
        assert db.get(Project, project.id).state_version == 7
        assert db.query(AgentSpec).count() == 0
        assert db.query(WorkItem).count() == 0


@pytest.mark.asyncio
async def test_other_command_cannot_hide_exhausted_repair_budget(
    session_factory, db_session, complete_brief, valid_spec, valid_breakdown
):
    project = _approved_project(db_session, complete_brief, valid_spec)
    agent = ScriptedAgentGateway(decompose_results=deque([valid_breakdown] * 7),
                                 review_breakdown_results=deque([source_review()] * 7))
    commands = command_service(session_factory, agent)
    def request(key):
        return SessionCommandRequest(command_id=key, action=CommandAction.CONVERT_TO_WORK_ITEM,
                                     expected_state_version=7, actor_id="owner")

    with pytest.raises(CommandHandlerRejected):
        await commands.execute(project.session_id, request("A"))
    with pytest.raises(CommandHandlerRejected):
        await commands.execute(project.session_id, request("B"))
    calls_before_retry = len(agent.calls)
    with pytest.raises(CommandHandlerRejected):
        await commands.execute(project.session_id, request("A"))

    assert calls_before_retry == 10
    assert len(agent.calls) == calls_before_retry
