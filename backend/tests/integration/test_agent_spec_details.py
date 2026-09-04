"""Exercise enrichment against real persistence and gateway boundaries, without CLI calls."""

import asyncio
from collections import deque
from copy import deepcopy
from types import SimpleNamespace

import pytest
import pytest_asyncio

from app.database.models import AgentCall, AgentSession, AgentSpec, AuditEvent, Project, SpecVersion, WorkItem, WorkItemDependency
from app.domain.implementation_plan import ImplementationPlan
from app.domain.types import SemanticReview
from app.services.command_service import ForbiddenActor
from app.services.decomposition_service import DecompositionService, _canonical_hash
from tests.helpers.implementation_plans import implementation_plan
from tests.helpers.fake_agent import ScriptedAgentGateway
from tests.helpers.scripted_codex import gateway_with_outputs
from tests.integration.test_decomposition_service import _approved_project


def service(factory, gateway):
    # Delayed import lets missing implementation fail the behavioral test after real setup.
    from app.services.agent_spec_details import AgentSpecDetailService
    return AgentSpecDetailService(factory, gateway)


def rows(db, model):
    return {row.id: deepcopy({column.name: getattr(row, column.name) for column in model.__table__.columns})
            for row in db.query(model).all()}


def state(factory):
    with factory() as db:
        return {model.__name__: rows(db, model) for model in (Project, SpecVersion, WorkItem, WorkItemDependency, AgentSpec)}


@pytest_asyncio.fixture
async def legacy_project(tmp_path, monkeypatch, session_factory, db_session, complete_brief,
                         valid_spec, valid_breakdown, passing_semantic_review):
    # Four tasks make an accidental unbounded gather observable.
    for suffix in ("extra-1", "extra-2"):
        task = valid_breakdown.tasks[0].model_copy(deep=True)
        task.local_key = suffix
        spec = valid_breakdown.agent_specs[0].model_copy(deep=True)
        spec.work_item_key = suffix
        valid_breakdown.tasks.append(task)
        valid_breakdown.agent_specs.append(spec)
    project = _approved_project(db_session, complete_brief, valid_spec)
    gateway = ScriptedAgentGateway(decompose_results=deque([valid_breakdown]))
    await DecompositionService(session_factory, gateway).convert(project.id)
    with session_factory() as db:
        for spec in db.query(AgentSpec).all():
            content = deepcopy(spec.content)
            content.pop("implementation_plan")
            content.pop("requirements", None)
            spec.content, spec.content_hash = content, _canonical_hash(content)
        db.commit()
    return project.id


def good_plan(payload):
    ids = sorted({rid for ac in payload["task_spec"]["acceptance_criteria"] for rid in ac["requirement_ids"]})
    return ImplementationPlan.model_validate(implementation_plan(ids))


async def pass_review(payload):
    return SemanticReview(verdict="PASS", findings=[])


def reject(path="agent_specs[t-api].implementation_plan", verdict="REJECT", code="VAGUE_METHOD"):
    return SemanticReview(verdict=verdict, findings=[dict(
        code=code, severity="BLOCKER", spec_path=path, message="Describe duplicate command handling",
        suggested_resolution="Specify receipt lookup and rollback semantics", blocks_progress=True,
    )])


@pytest.mark.asyncio
async def test_enrichment_preserves_scope_ids_prd_and_copies_requirements_atomically(
    legacy_project, session_factory, tmp_path, monkeypatch, passing_semantic_review
):
    before = state(session_factory)
    tasks = sorted(before["WorkItem"].values(), key=lambda item: item["local_key"])
    specs = {row["work_item_id"]: row for row in before["AgentSpec"].values()}
    ordered_tasks = sorted(
        (task for task in tasks if task["kind"] == "TASK"),
        key=lambda task: (task["local_key"] == "t-api", task["local_key"]),
    )
    outputs = [good_plan({"task_spec": specs[task["id"]]["content"]}) for task in ordered_tasks]
    gateway, prompts = gateway_with_outputs(tmp_path, monkeypatch, [*outputs, passing_semantic_review])

    result = await service(session_factory, gateway).enrich(legacy_project, "approver-1")

    after = state(session_factory)
    assert {s.id for s in result} == set(before["AgentSpec"])
    for model in ("SpecVersion", "WorkItem", "WorkItemDependency"):
        assert after[model] == before[model]
    assert after["Project"][legacy_project]["state_version"] == before["Project"][legacy_project]["state_version"] + 1
    for spec_id, old in before["AgentSpec"].items():
        new = after["AgentSpec"][spec_id]
        original_fields = {k: v for k, v in new["content"].items() if k not in {"implementation_plan", "requirements"}}
        assert original_fields == old["content"]
        assert {k: v for k, v in new.items() if k not in {"content", "content_hash"}} == {k: v for k, v in old.items() if k not in {"content", "content_hash"}}
        approved = next(iter(before["SpecVersion"].values()))["content"]
        ids = {rid for ac in old["content"]["acceptance_criteria"] for rid in ac["requirement_ids"]}
        assert new["content"]["requirements"] == [r for section in ("functional_requirements", "non_functional_requirements") for r in approved[section] if r["requirement_id"] in ids]
        assert new["content"]["implementation_plan"]["interfaces"][0]["definition"] == "POST /sessions"
        assert new["content_hash"] == _canonical_hash(new["content"]) != old["content_hash"]
    assert len(prompts) == 5
    assert "canonical_breakdown" in prompts[-1] and "implementation_plan" in prompts[-1]
    with session_factory() as db:
        calls = db.query(AgentCall).filter(AgentCall.operation == "plan_task").all()
        event = db.query(AuditEvent).filter_by(event_type="AGENT_SPEC_DETAILS_ENRICHED").one()
        assert len(calls) == 4 and all(c.status == "SUCCEEDED" for c in calls)
        assert event.actor_id == "approver-1"
        assert {c.id for c in calls} <= set(event.payload["agent_call_ids"])
        assert event.payload["old_hashes"] == {sid: s["content_hash"] for sid, s in before["AgentSpec"].items()}
        assert event.payload["new_hashes"] == {sid: s["content_hash"] for sid, s in after["AgentSpec"].items()}
        for call in calls:
            assert call.request["approved_spec"] == approved
            assert call.request["parent_work_item_id"] in before["WorkItem"]
            assert call.request["source_spec_version_id"] == next(iter(before["SpecVersion"]))
            assert db.get(AgentSession, call.agent_session_id).role == "PM"
        review = db.query(AgentCall).filter_by(id=event.payload["agent_call_ids"][-1]).one()
        assert review.operation == "review_breakdown" and review.status == "SUCCEEDED"
        assert db.get(AgentSession, review.agent_session_id).role == "REVIEWER"
        assert len(review.request["canonical_breakdown"]["agent_specs"]) == 4


@pytest.mark.asyncio
async def test_calls_are_durable_before_external_work_and_concurrency_is_bounded(legacy_project, session_factory):
    active = peak = 0
    async def plan(payload):
        nonlocal active, peak
        with session_factory() as db:
            call = db.query(AgentCall).filter_by(operation="plan_task", status="PENDING").filter(
                AgentCall.request["work_item_id"].as_string() == payload["work_item_id"]).one()
            assert call.request["task_spec"] == payload["task_spec"]
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return good_plan(payload)
    async def review(payload):
        with session_factory() as db:
            assert db.query(AgentCall).filter_by(operation="plan_task", status="RESULT_READY").count() == 4
            assert db.query(AgentCall).filter_by(operation="review_breakdown", status="PENDING").count() == 1
            assert all(not s.content.get("implementation_plan") for s in db.query(AgentSpec).all())
        return await pass_review(payload)

    await service(session_factory, SimpleNamespace(plan_task=plan, review_breakdown=review)).enrich(legacy_project, "pm-1")
    assert peak == 2 and active == 0


@pytest.mark.asyncio
async def test_enrichment_plans_dependency_order_in_waves_with_durable_contracts(legacy_project, session_factory):
    active = peak = 0
    completed = set()

    async def plan(payload):
        nonlocal active, peak
        key = payload["task_spec"]["work_item_key"]
        if key == "t-api":
            assert "t-domain" in completed
            assert [contract["work_item_key"] for contract in payload["dependency_contracts"]] == ["t-domain"]
            assert payload["dependency_contracts"][0]["implementation_plan"]
            assert payload["dependency_contract_hashes"] == {
                "t-domain": payload["dependency_contracts"][0]["contract_hash"],
            }
            with session_factory() as db:
                call = next(
                    call for call in db.query(AgentCall).filter_by(operation="plan_task", status="PENDING")
                    if call.request["task_spec"]["work_item_key"] == key
                )
                assert call.request["dependency_contracts"] == payload["dependency_contracts"]
                assert call.request["dependency_contract_hashes"] == payload["dependency_contract_hashes"]
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        completed.add(key)
        active -= 1
        return good_plan(payload)

    await service(session_factory, SimpleNamespace(plan_task=plan, review_breakdown=pass_review)).enrich(
        legacy_project, "approver-1")

    assert peak == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["plan", "invalid_plan", "review", "human", "need_info", "blocking_pass"])
async def test_failure_never_partially_saves_and_keeps_call_evidence(legacy_project, session_factory, failure):
    before = state(session_factory)
    async def plan(payload):
        if payload["task_spec"]["work_item_key"] == "t-api":
            if failure == "plan":
                raise RuntimeError("planner unavailable")
            if failure == "invalid_plan":
                return ImplementationPlan.model_validate(implementation_plan(["FR-999"]))
        return good_plan(payload)
    async def review(payload):
        if failure == "review":
            raise RuntimeError("reviewer unavailable")
        return reject(verdict="NEED_INFO" if failure == "need_info" else "PASS" if failure == "blocking_pass" else "REJECT",
                      code="NEEDS_HUMAN_DECISION" if failure == "human" else "VAGUE_METHOD")

    with pytest.raises(RuntimeError) as error:
        await service(session_factory, SimpleNamespace(plan_task=plan, review_breakdown=review)).enrich(legacy_project, "owner-1")
    assert error.value.agent_call_ids
    assert state(session_factory) == before
    with session_factory() as db:
        calls = db.query(AgentCall).filter(AgentCall.id.in_(error.value.agent_call_ids)).all()
        assert len(calls) == len(error.value.agent_call_ids)
        assert all(c.status in {"FAILED", "RESULT_READY"} for c in calls)
        if failure in {"plan", "invalid_plan", "review"}:
            assert any(c.status == "FAILED" and c.error for c in calls)
        if failure in {"human", "need_info"}:
            assert len(calls) == 5
        assert db.query(AuditEvent).filter_by(event_type="AGENT_SPEC_DETAILS_ENRICHED").count() == 0


@pytest.mark.asyncio
async def test_unauthorized_actor_is_rejected_before_any_calls(legacy_project, session_factory):
    before = state(session_factory)
    with pytest.raises(ForbiddenActor):
        await service(session_factory, SimpleNamespace()).enrich(legacy_project, "outsider")
    assert state(session_factory) == before
    with session_factory() as db:
        assert db.query(AgentCall).count() == 2  # Initial decomposition and its review only.


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["version", "auth", "prd", "spec", "hash", "task", "dependency", "phase"])
async def test_snapshot_changes_during_review_block_adoption(legacy_project, session_factory, change):
    changed = None
    async def plan(payload):
        return good_plan(payload)
    async def review(payload):
        nonlocal changed
        with session_factory() as db:
            project = db.get(Project, legacy_project)
            if change == "version":
                project.state_version += 1
            elif change == "auth":
                project.final_approver = "new-owner"
            elif change == "phase":
                project.phase = "REVIEW"
            elif change == "prd":
                spec = db.get(SpecVersion, project.current_spec_version_id)
                spec.content = {**spec.content, "risks": ["changed while reviewing"]}
            elif change in {"spec", "hash"}:
                spec = db.query(AgentSpec).first()
                if change == "hash":
                    spec.content_hash = "e" * 64
                else:
                    spec.content = {**spec.content, "scope": ["concurrent scope"]}
            elif change == "task":
                db.query(WorkItem).filter_by(kind="TASK").first().title = "Concurrent title"
            else:
                db.delete(db.query(WorkItemDependency).one())
            db.commit()
        changed = state(session_factory)
        return await pass_review(payload)
    with pytest.raises((RuntimeError, ForbiddenActor)):
        await service(session_factory, SimpleNamespace(plan_task=plan, review_breakdown=review)).enrich(legacy_project, "approver-1")
    assert state(session_factory) == changed


@pytest.mark.asyncio
@pytest.mark.parametrize("passes", [True, False])
async def test_review_repairs_only_targeted_plans_at_most_twice(legacy_project, session_factory, passes):
    planned, reviews = [], []
    async def plan(payload):
        planned.append(deepcopy(payload))
        return good_plan(payload)
    async def review(payload):
        reviews.append(deepcopy(payload))
        return await pass_review(payload) if passes and len(reviews) == 3 else reject()
    operation = service(session_factory, SimpleNamespace(plan_task=plan, review_breakdown=review))
    before = state(session_factory)
    if passes:
        await operation.enrich(legacy_project, "approver-1")
    else:
        with pytest.raises(RuntimeError, match="(?i)review|revision"):
            await operation.enrich(legacy_project, "approver-1")
        assert state(session_factory) == before
    assert len(reviews) == 3 and len(planned) == 6
    assert [p["task_spec"]["work_item_key"] for p in planned[4:]] == ["t-api", "t-api"]
    for payload in planned[4:]:
        assert payload["previous_plan"] and payload["review_feedback"] == reject().model_dump(mode="json")
        assert payload["previous_review_call_id"]
    for review_payload in reviews[1:]:
        assert review_payload["canonical_breakdown"] == reviews[0]["canonical_breakdown"]
    with session_factory() as db:
        calls = db.query(AgentCall).filter_by(operation="plan_task").all()
        assert len(calls) == 6
        assert sum(c.status == "SUCCEEDED" for c in calls) == (4 if passes else 0)


@pytest.mark.asyncio
async def test_rejecting_producer_repairs_transitive_consumers_in_dependency_order(legacy_project, session_factory):
    planned = []
    rounds = 0

    async def plan(payload):
        key = payload["task_spec"]["work_item_key"]
        planned.append(key)
        result = good_plan(payload)
        result.overview = f"{key} plan {planned.count(key)}"
        return result

    async def review(payload):
        nonlocal rounds
        rounds += 1
        return reject(path="agent_specs[t-domain].implementation_plan") if rounds == 1 else await pass_review(payload)

    result = await service(session_factory, SimpleNamespace(plan_task=plan, review_breakdown=review)).enrich(
        legacy_project, "approver-1")

    assert planned[4:] == ["t-domain", "t-api"]
    with session_factory() as db:
        keys_by_id = {item.id: item.local_key for item in db.query(WorkItem).all()}
    plans = {
        keys_by_id[spec.work_item_id]: spec.content["implementation_plan"]["overview"]
        for spec in result
    }
    assert plans == {
        "extra-1": "extra-1 plan 1",
        "extra-2": "extra-2 plan 1",
        "t-domain": "t-domain plan 2",
        "t-api": "t-api plan 2",
    }


@pytest.mark.asyncio
async def test_valid_existing_plans_are_idempotent_but_corruption_is_not_a_noop(legacy_project, session_factory):
    async def plan(payload):
        return good_plan(payload)
    await service(session_factory, SimpleNamespace(plan_task=plan, review_breakdown=pass_review)).enrich(legacy_project, "approver-1")
    before = state(session_factory)
    with session_factory() as db:
        call_count = db.query(AgentCall).count()
    again = await service(session_factory, SimpleNamespace()).enrich(legacy_project, "approver-1")
    assert {s.id for s in again} == set(before["AgentSpec"])
    assert state(session_factory) == before
    with session_factory() as db:
        assert db.query(AgentCall).count() == call_count
        spec = db.query(AgentSpec).first()
        content = deepcopy(spec.content)
        content["implementation_plan"]["steps"][0]["requirement_ids"] = ["FR-999"]
        spec.content, spec.content_hash = content, _canonical_hash(content)
        db.commit()
    with pytest.raises(RuntimeError, match="UNKNOWN_REQUIREMENT_ID"):
        await service(session_factory, SimpleNamespace()).enrich(legacy_project, "approver-1")


@pytest.mark.asyncio
async def test_only_missing_plans_are_generated_and_existing_plan_is_retained(legacy_project, session_factory):
    with session_factory() as db:
        spec = db.query(AgentSpec).first()
        content = deepcopy(spec.content)
        content["implementation_plan"] = good_plan({"task_spec": content}).model_dump(mode="json")
        content["implementation_plan"]["overview"] = "Previously approved concrete implementation approach"
        spec.content, spec.content_hash = content, _canonical_hash(content)
        existing_id, original_plan = spec.id, deepcopy(content["implementation_plan"])
        db.commit()
    async def plan(payload):
        assert payload["agent_spec_id"] != existing_id
        return good_plan(payload)
    result = await service(session_factory, SimpleNamespace(plan_task=plan, review_breakdown=pass_review)).enrich(legacy_project, "approver-1")
    assert next(s for s in result if s.id == existing_id).content["implementation_plan"] == original_plan
    with session_factory() as db:
        assert db.query(AgentCall).filter_by(operation="plan_task").count() == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ["hash", "source", "requirements", "dependencies", "missing_spec", "phase", "unapproved"])
async def test_inconsistent_existing_data_is_rejected_without_model_calls(legacy_project, session_factory, defect):
    with session_factory() as db:
        spec = db.query(AgentSpec).first()
        content = deepcopy(spec.content)
        if defect == "hash":
            spec.content_hash = "bad"
        elif defect == "source":
            spec.source_spec_version_id = "another-approved-version"
        elif defect == "requirements":
            content["requirements"] = [{"requirement_id": "FR-001", "statement": "Invented", "priority": "MUST"}]
        elif defect == "dependencies":
            spec.dependency_work_item_ids = ["outside-project"]
        elif defect == "missing_spec":
            db.delete(spec)
        elif defect == "phase":
            db.get(Project, legacy_project).phase = "REVIEW"
        else:
            db.get(SpecVersion, spec.source_spec_version_id).status = "DRAFT"
        if defect == "requirements":
            spec.content, spec.content_hash = content, _canonical_hash(content)
        db.commit()
    before = state(session_factory)
    with pytest.raises(RuntimeError):
        await service(session_factory, SimpleNamespace()).enrich(legacy_project, "approver-1")
    assert state(session_factory) == before
    with session_factory() as db:
        assert db.query(AgentCall).count() == 2


@pytest.mark.asyncio
async def test_global_review_findings_replan_all_tasks(legacy_project, session_factory):
    rounds = 0
    async def plan(payload):
        return good_plan(payload)
    async def review(payload):
        nonlocal rounds
        rounds += 1
        return reject(path="canonical_breakdown.interfaces") if rounds == 1 else await pass_review(payload)
    await service(session_factory, SimpleNamespace(plan_task=plan, review_breakdown=review)).enrich(legacy_project, "approver-1")
    with session_factory() as db:
        assert db.query(AgentCall).filter_by(operation="plan_task").count() == 8
        assert db.query(AgentCall).filter_by(operation="plan_task", status="SUCCEEDED").count() == 4


@pytest.mark.asyncio
async def test_final_transaction_failure_rolls_back_plans_audit_and_call_adoption(legacy_project, session_factory, engine):
    from sqlalchemy import event
    before = state(session_factory)
    async def plan(payload):
        return good_plan(payload)
    def fail_after_spec_updates(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO audit_events") and "AGENT_SPEC_DETAILS_ENRICHED" in str(parameters):
            raise RuntimeError("forced adoption transaction failure")
    event.listen(engine, "before_cursor_execute", fail_after_spec_updates)
    try:
        with pytest.raises(RuntimeError, match="forced adoption transaction failure"):
            await service(session_factory, SimpleNamespace(plan_task=plan, review_breakdown=pass_review)).enrich(legacy_project, "approver-1")
    finally:
        event.remove(engine, "before_cursor_execute", fail_after_spec_updates)
    assert state(session_factory) == before
    with session_factory() as db:
        assert db.query(AgentCall).filter_by(status="RESULT_READY").count() == 5
        assert db.query(AuditEvent).filter_by(event_type="AGENT_SPEC_DETAILS_ENRICHED").count() == 0


@pytest.mark.asyncio
async def test_vague_legacy_outputs_request_proposed_concrete_definitions_without_rewriting_scope(legacy_project, session_factory):
    with session_factory() as db:
        spec = db.query(AgentSpec).first()
        content = deepcopy(spec.content)
        content["outputs"][0].update(name="legacy artifact??", format="maybe?")
        spec.content, spec.content_hash = content, _canonical_hash(content)
        spec_id, original_outputs = spec.id, deepcopy(content["outputs"])
        db.commit()
    async def plan(payload):
        assert "PROPOSED" in payload["planning_guidance"]
        assert "expected_output" in payload["planning_guidance"]
        assert "placeholder" in payload["planning_guidance"]
        result = good_plan(payload)
        result.steps[0].expected_output = "Proposed: Python module defining SessionCommand with request_id: string and brief: object"
        return result
    result = await service(session_factory, SimpleNamespace(plan_task=plan, review_breakdown=pass_review)).enrich(legacy_project, "approver-1")
    enriched = next(spec for spec in result if spec.id == spec_id)
    assert enriched.content["outputs"] == original_outputs
    assert "Proposed: Python module" in enriched.content["implementation_plan"]["steps"][0]["expected_output"]
