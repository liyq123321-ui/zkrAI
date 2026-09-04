"""Operator corrections must pass a new review bound to the unchanged full snapshot."""

import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import event

from app.database.models import AgentCall, AgentSpec, AuditEvent, Project, SpecVersion, WorkItem
from app.services.agent_spec_details import (
    AgentSpecDetailsError, AgentSpecDetailsReviewRejected, AgentSpecDetailsSnapshotChanged,
)
from app.services.command_service import ForbiddenActor
from app.services.decomposition_service import _canonical_hash
from tests.integration.test_agent_spec_details import (
    good_plan, legacy_project, pass_review, reject, rows, service, state,
)


@pytest_asyncio.fixture
async def rejected_source(legacy_project, session_factory):
    async def plan(payload):
        return good_plan(payload)

    async def review(payload):
        return reject()

    with pytest.raises(AgentSpecDetailsReviewRejected) as error:
        await service(session_factory, SimpleNamespace(plan_task=plan, review_breakdown=review)).enrich(
            legacy_project, "approver-1")
    with session_factory() as db:
        source = db.get(AgentCall, error.value.agent_call_id)
        assert source.request["repair_round"] == 2
        plans = {task["work_item_key"]: deepcopy(task["implementation_plan"])
                 for task in source.request["canonical_breakdown"]["agent_specs"]}
        for plan in plans.values():
            plan["interfaces"][0]["name"] = "CreateSession"
        return source.id, plans


def evidence(factory):
    with factory() as db:
        return rows(db, AgentCall)


@pytest.mark.asyncio
async def test_queued_planner_never_sees_timing_dependent_peer_plans(
    legacy_project, session_factory,
):
    before = state(session_factory)
    first_two_started, third_started = asyncio.Event(), asyncio.Event()
    release_first, release_rest = asyncio.Event(), asyncio.Event()
    started = []
    active = peak = 0

    async def plan(payload):
        nonlocal active, peak
        index = len(started)
        started.append(deepcopy(payload))
        active += 1
        peak = max(peak, active)
        try:
            if index == 0:
                await release_first.wait()
            else:
                if index == 1:
                    first_two_started.set()
                elif index == 2:
                    third_started.set()
                await release_rest.wait()
            result = good_plan(payload)
            result.interfaces[0].name = "CreateSession"
            return result
        finally:
            active -= 1

    async def review(payload):
        assert state(session_factory) == before
        return await pass_review(payload)

    run = asyncio.create_task(service(session_factory, SimpleNamespace(
        plan_task=plan, review_breakdown=review)).enrich(legacy_project, "approver-1"))
    try:
        await asyncio.wait_for(first_two_started.wait(), timeout=5)
        assert len(started) == 2 and active == 2
        release_first.set()
        await asyncio.wait_for(third_started.wait(), timeout=5)
        assert len(started) == 3 and active == 2
        assert all(
            "implementation_plan" not in peer
            for payload in started
            for peer in payload["related_tasks"]
        )
        assert "dependency_contracts" in started[2]["planning_guidance"]
        assert "only authoritative" in started[2]["planning_guidance"]
        assert "explicit adapters" in started[2]["planning_guidance"]
        assert state(session_factory) == before
    finally:
        release_first.set()
        release_rest.set()
        result = await asyncio.wait_for(run, timeout=5)
    assert peak == 2 and active == 0
    assert len(started) == len(result) == 4


def assert_failure(factory, old_calls, *, new_calls=0):
    calls = evidence(factory)
    assert {key: calls[key] for key in old_calls} == old_calls
    assert len(calls) == len(old_calls) + new_calls
    with factory() as db:
        assert db.query(AuditEvent).filter_by(event_type="AGENT_SPEC_DETAILS_ENRICHED").count() == 0
        # Includes the original exhausted enrichment and this failed correction.
        assert db.query(AuditEvent).filter_by(event_type="AGENT_SPEC_DETAILS_FAILED").count() == 2


@pytest.mark.asyncio
async def test_corrected_full_candidate_is_durable_and_only_new_review_is_adopted(
    legacy_project, rejected_source, session_factory,
):
    source_id, plans = rejected_source
    before, old_calls = state(session_factory), evidence(session_factory)
    original_plans = deepcopy(plans)
    received = []

    async def review(payload):
        received.append(deepcopy(payload))
        with session_factory() as db:
            pending = db.query(AgentCall).filter_by(status="PENDING").one()
            assert pending.operation == "review_breakdown"
            assert pending.request == payload
        assert state(session_factory) == before
        # Mutating caller input while awaiting review must not alter the adopted candidate.
        plans[next(iter(plans))]["overview"] = "Changed after review started"
        return await pass_review(payload)

    result = await service(session_factory, SimpleNamespace(review_breakdown=review)).review_revised_plans(
        legacy_project, "owner-1", source_id, plans)

    after = state(session_factory)
    assert {spec.id for spec in result} == set(before["AgentSpec"])
    for model in ("WorkItem", "WorkItemDependency", "SpecVersion"):
        assert after[model] == before[model]
    old_project, new_project = before["Project"][legacy_project], after["Project"][legacy_project]
    assert new_project["state_version"] == old_project["state_version"] + 1
    assert {k: v for k, v in new_project.items() if k not in {"state_version", "updated_at"}} == {
        k: v for k, v in old_project.items() if k not in {"state_version", "updated_at"}}
    approved = next(iter(before["SpecVersion"].values()))["content"]
    for spec_id, old in before["AgentSpec"].items():
        new = after["AgentSpec"][spec_id]
        key = before["WorkItem"][old["work_item_id"]]["local_key"]
        assert new["content"]["implementation_plan"] == original_plans[key]
        assert {k: v for k, v in new["content"].items() if k not in {"implementation_plan", "requirements"}} == old["content"]
        assert {k: v for k, v in new.items() if k not in {"content", "content_hash"}} == {
            k: v for k, v in old.items() if k not in {"content", "content_hash"}}
        ids = {rid for ac in old["content"]["acceptance_criteria"] for rid in ac["requirement_ids"]}
        assert new["content"]["requirements"] == [r for section in ("functional_requirements", "non_functional_requirements")
                                                  for r in approved[section] if r["requirement_id"] in ids]
        assert new["content_hash"] == _canonical_hash(new["content"]) != old["content_hash"]
    calls = evidence(session_factory)
    assert {key: calls[key] for key in old_calls} == old_calls
    new_id, = calls.keys() - old_calls.keys()
    assert calls[new_id]["status"] == "SUCCEEDED"
    payload, = received
    assert payload["revision_origin"] == "operator_supplied_plan_corrections"
    assert payload["source_review_call_id"] == source_id
    assert payload["candidate_plan_hash"] == _canonical_hash(original_plans)
    assert payload["repair_round"] == 2
    assert payload["review_feedback"] == old_calls[source_id]["response"]
    for field in ("project_id", "source_spec_version_id", "source_spec_content_hash", "source_state_version", "source_snapshot_hash"):
        assert payload[field] == old_calls[source_id]["request"][field]
    assert payload["source_binding"]["actor_id"] == "approver-1"
    assert payload["actor_id"] == "owner-1"
    expected = deepcopy(old_calls[source_id]["request"]["canonical_breakdown"])
    for task in expected["agent_specs"]:
        task["implementation_plan"] = original_plans[task["work_item_key"]]
    assert payload["canonical_breakdown"] == expected
    with session_factory() as db:
        audit = db.query(AuditEvent).filter_by(event_type="AGENT_SPEC_DETAILS_ENRICHED").one()
        assert audit.payload["adopted_agent_call_ids"] == [new_id]
        assert audit.payload["agent_call_ids"] == [new_id]


@pytest.mark.asyncio
@pytest.mark.parametrize("verdict", ["REJECT", "NEED_INFO", "PASS", "exception"])
async def test_blocked_correction_never_replans_or_writes_specs(
    legacy_project, rejected_source, session_factory, verdict,
):
    source_id, plans = rejected_source
    before, old_calls = state(session_factory), evidence(session_factory)

    async def review(payload):
        if verdict == "exception":
            raise RuntimeError("review unavailable")
        return reject(verdict=verdict)

    with pytest.raises(AgentSpecDetailsError) as error:
        await service(session_factory, SimpleNamespace(review_breakdown=review)).review_revised_plans(
            legacy_project, "approver-1", source_id, plans)
    assert len(error.value.agent_call_ids) == 1
    assert state(session_factory) == before
    assert_failure(session_factory, old_calls, new_calls=1)
    with session_factory() as db:
        call = db.get(AgentCall, error.value.agent_call_id)
        assert call.status == ("FAILED" if verdict == "exception" else "RESULT_READY")
        if verdict != "exception":
            assert call.response["verdict"] == verdict


@pytest.mark.asyncio
async def test_unauthorized_corrections_do_not_create_calls(legacy_project, rejected_source, session_factory):
    source_id, plans = rejected_source
    before, old_calls = state(session_factory), evidence(session_factory)
    with pytest.raises(ForbiddenActor):
        await service(session_factory, SimpleNamespace()).review_revised_plans(
            legacy_project, "outsider", source_id, plans)
    assert state(session_factory) == before
    assert evidence(session_factory) == old_calls


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", [
    "missing_source", "project", "operation", "status", "snapshot", "state_version", "spec_version", "spec_hash",
    "missing_binding", "scope", "outputs", "ownership", "task", "extra_content", "approved_content",
    "invalid_source_plan", "passing", "need_info", "human", "decision", "risk", "empty_feedback",
])
async def test_unbound_or_tampered_source_is_rejected_before_review(
    legacy_project, rejected_source, session_factory, defect,
):
    source_id, plans = rejected_source
    with session_factory() as db:
        call = db.get(AgentCall, source_id)
        request, response = deepcopy(call.request), deepcopy(call.response)
        if defect == "missing_source":
            source_id = "missing"
        elif defect == "project":
            call.project_id = "another-project"
        elif defect == "operation":
            call.operation = "plan_task"
        elif defect == "status":
            call.status = "SUCCEEDED"
        elif defect in {"snapshot", "state_version", "spec_version", "spec_hash"}:
            field = {"snapshot": "source_snapshot_hash", "state_version": "source_state_version",
                     "spec_version": "source_spec_version_id", "spec_hash": "source_spec_content_hash"}[defect]
            request[field] = "stale"
        elif defect == "missing_binding":
            request.pop("source_snapshot_hash")
        elif defect in {"scope", "outputs", "ownership", "extra_content"}:
            task = request["canonical_breakdown"]["agent_specs"][0]
            field, value = {"scope": ("scope", ["Unapproved scope"]), "outputs": ("outputs", [dict(name="Other", format="JSON", required=True)]),
                            "ownership": ("suggested_assignee", "another-owner"), "extra_content": ("injected", "unsafe")}[defect]
            task[field] = value
        elif defect == "task":
            request["canonical_breakdown"]["tasks"][0]["title"] = "Changed task"
        elif defect == "approved_content":
            request["approved_spec"]["risks"] = ["Fabricated PRD"]
        elif defect == "invalid_source_plan":
            request["canonical_breakdown"]["agent_specs"][0]["implementation_plan"]["steps"] = []
        elif defect in {"passing", "need_info"}:
            response["verdict"] = "PASS" if defect == "passing" else "NEED_INFO"
        elif defect == "empty_feedback":
            response["findings"] = []
        else:
            response["findings"][0]["code"] = {"human": "NEEDS_HUMAN_DECISION", "decision": "NEEDS_DECISION", "risk": "UNACCEPTED_RISK"}[defect]
        call.request, call.response = request, response
        db.commit()
    before, old_calls = state(session_factory), evidence(session_factory)
    with pytest.raises(AgentSpecDetailsError):
        await service(session_factory, SimpleNamespace()).review_revised_plans(
            legacy_project, "approver-1", source_id, plans)
    assert state(session_factory) == before
    assert_failure(session_factory, old_calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ["missing", "unknown", "not_map", "scope", "blank", "coverage", "unknown_requirement"])
async def test_corrections_require_exact_task_set_and_valid_plan_only_content(
    legacy_project, rejected_source, session_factory, defect,
):
    source_id, plans = rejected_source
    key = "t-api"
    if defect == "missing":
        plans.pop(key)
    elif defect == "unknown":
        plans["not-a-task"] = plans.pop(key)
    elif defect == "not_map":
        plans = list(plans.values())
    elif defect == "scope":
        plans[key]["scope"] = ["Unapproved scope"]
    elif defect == "blank":
        plans[key]["overview"] = " "
    else:
        plans[key]["steps"][0]["requirement_ids"] = ["FR-999"] if defect == "unknown_requirement" else ["FR-001"]
    before, old_calls = state(session_factory), evidence(session_factory)
    with pytest.raises(AgentSpecDetailsError):
        await service(session_factory, SimpleNamespace()).review_revised_plans(
            legacy_project, "approver-1", source_id, plans)
    assert state(session_factory) == before
    assert_failure(session_factory, old_calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("when", ["before", "during"])
@pytest.mark.parametrize("change", ["version", "prd", "spec", "task", "permissions"])
async def test_stale_or_concurrently_changed_snapshot_cannot_be_adopted(
    legacy_project, rejected_source, session_factory, when, change,
):
    source_id, plans = rejected_source
    old_calls = evidence(session_factory)

    def mutate():
        with session_factory() as db:
            project = db.get(Project, legacy_project)
            if change == "version":
                project.state_version += 1
            elif change == "permissions":
                project.project_manager_ids = ["new-manager"]
            elif change == "prd":
                version = db.get(SpecVersion, project.current_spec_version_id)
                version.content = {**version.content, "risks": ["Concurrent PRD edit"]}
            elif change == "spec":
                spec = db.query(AgentSpec).first()
                spec.content = {**spec.content, "scope": ["Concurrent scope"]}
                spec.content_hash = _canonical_hash(spec.content)
            else:
                db.query(WorkItem).filter_by(kind="TASK").first().title = "Concurrent title"
            db.commit()
        return state(session_factory)

    changed = mutate() if when == "before" else None

    async def review(payload):
        nonlocal changed
        assert when == "during", "Stale source must fail before another review"
        changed = mutate()
        return await pass_review(payload)

    with pytest.raises(AgentSpecDetailsSnapshotChanged):
        await service(session_factory, SimpleNamespace(review_breakdown=review)).review_revised_plans(
            legacy_project, "approver-1", source_id, plans)
    assert state(session_factory) == changed
    assert_failure(session_factory, old_calls, new_calls=int(when == "during"))


@pytest.mark.asyncio
async def test_adoption_transaction_failure_rolls_back_all_corrections(
    legacy_project, rejected_source, session_factory, engine,
):
    source_id, plans = rejected_source
    before, old_calls = state(session_factory), evidence(session_factory)

    def fail_audit(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO audit_events") and "AGENT_SPEC_DETAILS_ENRICHED" in str(parameters):
            raise RuntimeError("forced atomic adoption failure")

    event.listen(engine, "before_cursor_execute", fail_audit)
    try:
        with pytest.raises(RuntimeError, match="forced atomic adoption failure"):
            await service(session_factory, SimpleNamespace(review_breakdown=pass_review)).review_revised_plans(
                legacy_project, "approver-1", source_id, plans)
    finally:
        event.remove(engine, "before_cursor_execute", fail_audit)
    assert state(session_factory) == before
    assert_failure(session_factory, old_calls, new_calls=1)
    calls = evidence(session_factory)
    new_id, = calls.keys() - old_calls.keys()
    assert calls[new_id]["status"] == "RESULT_READY"


@pytest.mark.parametrize("extra", [["--revised-plans", "plans.json"], ["--source-review-call-id", "review-1"]])
def test_cli_correction_options_must_be_paired(monkeypatch, capsys, extra):
    from scripts.enrich_agent_specs import main

    monkeypatch.setattr("sys.argv", ["enrich_agent_specs", "--session-id", "session", "--actor-id", "owner", *extra])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    assert "must be supplied together" in capsys.readouterr().err


@pytest.mark.parametrize("corrected", [True, False])
def test_cli_runs_reviewed_corrections_or_original_enrichment(
    legacy_project, rejected_source, session_factory, engine, monkeypatch, tmp_path, test_settings, capsys, corrected,
):
    from app.config import Settings
    from scripts.enrich_agent_specs import main

    source_id, plans = rejected_source
    plans["t-api"]["overview"] = "明确共享接口名称和调用顺序。"
    path = tmp_path / "revised-plans.json"
    path.write_text(json.dumps(plans, ensure_ascii=False), encoding="utf-8")
    with session_factory() as db:
        session_id = db.get(Project, legacy_project).session_id
    old_calls = evidence(session_factory)

    async def plan(payload):
        assert not corrected, "Explicit corrections must never request PM plans"
        return good_plan(payload)

    async def review(payload):
        if corrected:
            assert payload["source_review_call_id"] == source_id
            by_key = {task["work_item_key"]: task["implementation_plan"]
                      for task in payload["canonical_breakdown"]["agent_specs"]}
            assert by_key == plans
        return await pass_review(payload)

    argv = ["enrich_agent_specs", "--session-id", session_id, "--actor-id", "approver-1"]
    if corrected:
        argv += ["--revised-plans", str(path), "--source-review-call-id", source_id]
    monkeypatch.setattr("sys.argv", argv)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args: None)
    monkeypatch.setattr(Settings, "from_env", classmethod(lambda cls: test_settings))
    monkeypatch.setattr("app.database.database.create_engine_for_url", lambda url: engine)
    monkeypatch.setattr("app.agents.codex.CodexAgentGateway", lambda **kwargs: SimpleNamespace(plan_task=plan, review_breakdown=review))
    # The fixture owns this in-memory engine and tears it down after assertions.
    monkeypatch.setattr(engine, "dispose", lambda: None)
    main()
    output = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert output["session_id"] == session_id
    assert output["agent_specs"] == 4
    calls = evidence(session_factory)
    new_calls = [call for key, call in calls.items() if key not in old_calls]
    assert len(new_calls) == (1 if corrected else 5)
    assert all(call["status"] == "SUCCEEDED" for call in new_calls)
    assert {key: calls[key] for key in old_calls} == old_calls
    with session_factory() as db:
        assert all(spec.content.get("implementation_plan") for spec in db.query(AgentSpec).all())


@pytest.mark.parametrize("content", [b"{", b"[]", b"\xff", None])
def test_cli_invalid_plan_files_fail_before_opening_database(monkeypatch, tmp_path, capsys, content):
    from scripts.enrich_agent_specs import main

    path = tmp_path / "invalid-plans.json"
    if content is not None:
        path.write_bytes(content)
    monkeypatch.setattr("sys.argv", ["enrich_agent_specs", "--session-id", "session", "--actor-id", "owner",
                                     "--revised-plans", str(path), "--source-review-call-id", "review"])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    assert "Cannot read revised plans" in capsys.readouterr().err if content != b"[]" else True
