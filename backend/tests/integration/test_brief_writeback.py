"""Clarification must update the Brief consumed by subsequent PRD work."""

from collections import deque
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.database.models import (
    AgentCall, Artifact, AuditEvent, ClarificationRequest, ClarificationResponse,
    Project, SpecVersion, clarification_boundary_key,
)
from app.domain.types import ClarificationAnalysis, CommandAction, ProjectPhase, SpecStatus
from app.schemas.workflow import SessionCommandRequest, SessionCreateRequest
from app.services.command_service import CommandService
from app.services.project_service import ProjectService
from main import create_app
from tests.helpers.fake_agent import ScriptedAgentGateway


def _analysis(*, ready=False, updates=None):
    result = {
        "ready_for_spec": ready,
        "questions": [] if ready else [{
            "question_id": "Q1",
            "question": "Who approves the prototype?",
            "reason": "Approval responsibility needs confirmation.",
            "affected_areas": ["responsibilities"],
            "blocking": True,
        }],
        "assumptions": [],
    }
    if updates is not None:
        result["brief_updates"] = updates
    return result


def test_brief_reconciliation_orders_answers_by_time_across_intake_and_review(
    session_factory, complete_brief
):
    """A review round restarting at 1 must not let an older intake decision win."""
    start = datetime(2026, 9, 1, tzinfo=UTC)
    with session_factory() as db:
        project = Project(
            id="history-project", session_id="history-session", creation_request_id="history-create",
            brief=complete_brief.model_dump(mode="json"), final_approver="approver-1",
        )
        db.add(project)
        for index, (round_number, spec_id, message) in enumerate([
            (1, None, "Use a database."),
            (2, None, "Use a vector database."),
            (1, "prd-v1", "Replace databases with web search only."),
        ]):
            request_id = f"history-question-{index}"
            db.add(ClarificationRequest(
                id=request_id, project_id=project.id, spec_version_id=spec_id,
                analysis_round=round_number, questions=_analysis()["questions"],
                created_at=start + timedelta(minutes=index),
            ))
            db.add(ClarificationResponse(
                id=f"history-answer-{index}", project_id=project.id,
                clarification_request_id=request_id, actor_id="approver-1",
                answers={"message": message}, created_at=start + timedelta(hours=index + 1),
            ))
        db.commit()
        payload = ProjectService(session_factory, ScriptedAgentGateway())._analysis_payload(db, project)

    history = payload["clarification_history"]
    assert [item["answers"]["message"] for item in history] == [
        "Use a database.", "Use a vector database.", "Replace databases with web search only."
    ]
    assert [item["response_id"] for item in history] == [
        "history-answer-0", "history-answer-1", "history-answer-2"
    ]
    assert all(item["answered_at"] for item in history)


@pytest.mark.asyncio
@pytest.mark.parametrize("via_command", [False, True], ids=["direct", "command"])
async def test_multiple_clarification_rounds_merge_into_durable_brief(
    session_factory, complete_brief, via_command
):
    """Losing an earlier patch or ignoring a blocked round leaves contradictory scope."""
    agent = ScriptedAgentGateway(analyze_results=deque([
        ClarificationAnalysis.model_validate(_analysis()),
        _analysis(updates={
            "final_objective": "Deliver a runnable web-search Q&A prototype",
            "known_scope": ["Web search", "Multi-turn Q&A", "Citations"],
        }),
        _analysis(ready=True, updates={
            "final_objective": None,
            "exclusions": [],
            "reference_materials": ["Public CSDN and GitHub content from the last 30 days"],
            "expected_deliverables": ["Prototype", "API documentation", "Test report"],
            "time_constraints": "7 days after baseline confirmation",
        }),
    ]))
    service = ProjectService(session_factory, agent)
    state = await service.create_session(SessionCreateRequest(
        request_id="brief-rounds", actor_id="approver-1", brief=complete_brief
    ))
    commands = CommandService(session_factory, handlers={
        CommandAction.MESSAGE: service.as_command_handler(),
    })
    for index, message in enumerate([
        "Build a web-search prototype with multi-turn Q&A and citations.",
        "Use public CSDN and GitHub content from the last 30 days. Deliver in 7 days.",
    ]):
        if via_command:
            request = SessionCommandRequest(
                command_id=f"brief-round-{index}", action=CommandAction.MESSAGE,
                expected_state_version=state.state_version,
                actor_id="approver-1", message=message,
            )
            result = await commands.execute(state.session_id, request)
            assert await commands.execute(state.session_id, request) == result
            state = result.state
        else:
            state = await service.answer_clarification(state.project_id, "approver-1", message)
        with session_factory() as db:
            brief = db.get(Project, state.project_id).brief
            assert brief["final_objective"] == "Deliver a runnable web-search Q&A prototype"
            assert brief["known_scope"] == ["Web search", "Multi-turn Q&A", "Citations"]

    assert state.phase is ProjectPhase.SPECIFICATION
    with session_factory() as db:
        project = db.get(Project, state.project_id)
        assert project.brief["reference_materials"] == [
            "Public CSDN and GitHub content from the last 30 days"
        ]
        assert project.brief["exclusions"] == []
        assert project.brief["motivation"] == complete_brief.motivation
        assert project.brief["final_approver"] == project.final_approver == "approver-1"
        assert project.brief["project_manager_ids"] == ["pm-1"]
        assert project.brief["root_owner_ids"] == ["owner-1"]
        assert db.query(ClarificationResponse).count() == 2
        assert db.query(AgentCall).filter_by(operation="analyze_brief").count() == 3
        assert db.query(Artifact).one().content == complete_brief.model_dump(mode="json")
        calls = db.query(AgentCall).filter_by(operation="analyze_brief").all()
        second_round = next(call for call in calls if len(call.request["clarification_history"]) == 2)
        assert second_round.request["brief"]["known_scope"] == ["Web search", "Multi-turn Q&A", "Citations"]


@pytest.mark.parametrize("during_review", [False, True], ids=["initial-prd", "revised-prd"])
def test_http_answer_updates_brief_used_for_prd_generation_and_review(
    session_factory, complete_brief, valid_spec, passing_semantic_review, during_review
):
    """Persisted generation and review snapshots must both carry the clarified Brief."""
    agent = ScriptedAgentGateway(
        analyze_results=deque([
            ClarificationAnalysis.model_validate(_analysis()),
            _analysis(ready=True, updates={
                "known_scope": ["Web search Q&A, without a persistent database"],
                "reference_materials": ["Acceptance baseline: 23/25 normal, 5/5 edge cases, 95/100 within 60s"],
            }),
        ]),
        generate_results=deque([valid_spec]),
        review_results=deque([passing_semantic_review]),
    )
    with TestClient(create_app(agent_gateway=agent, session_factory=session_factory)) as client:
        created = client.post("/sessions", json={
            "request_id": "brief-http", "actor_id": "approver-1",
            "brief": complete_brief.model_dump(mode="json"),
        }).json()
        if during_review:
            with session_factory() as db:
                project = db.get(Project, created["project_id"])
                version = SpecVersion(
                    id="blocked-prd", project_id=project.id, revision=3,
                    content=valid_spec.model_dump(mode="json"), markdown="# PRD v3",
                    generation_source="PM_AGENT", input_refs=[],
                    generator_agent_session_id="pm", generator_call_id="previous-call",
                    change_summary="Previous version", content_hash="a" * 64,
                    status=SpecStatus.NEED_CLARIFICATION.value,
                )
                db.add(version)
                project.phase = ProjectPhase.REVIEW.value
                project.current_spec_version_id = version.id
                clarification = db.query(ClarificationRequest).one()
                clarification.spec_version_id = version.id
                clarification.boundary_key = clarification_boundary_key(version.id)
                db.commit()
        answered = client.post(f"/sessions/{created['session_id']}/commands", json={
            "command_id": "brief-answer", "action": "message",
            "expected_state_version": created["state_version"], "actor_id": "approver-1",
            "message": "Use web search, not a database; confirm the supplied acceptance baseline.",
        })
        assert answered.status_code == 200, answered.text
        state = answered.json()["state"]
        if during_review:
            assert state["phase"] == "REVIEW"
            assert state["current_spec_status"] == "REWORK"
        generated = client.post(f"/sessions/{created['session_id']}/commands", json={
            "command_id": "brief-generate", "action": "revise" if during_review else "create_spec",
            "expected_state_version": state["state_version"], "actor_id": "approver-1",
            "message": "Apply the confirmed Brief.",
        })
        assert generated.status_code == 200, generated.text
    with session_factory() as db:
        generation = db.query(AgentCall).filter_by(operation="generate_spec").one()
        review = db.query(AgentCall).filter_by(operation="review_spec").one()
        for brief in [generation.request["brief"], review.request["generation_source_snapshot"]["brief"]]:
            assert brief["known_scope"] == ["Web search Q&A, without a persistent database"]
            assert brief["reference_materials"] == [
                "Acceptance baseline: 23/25 normal, 5/5 edge cases, 95/100 within 60s"
            ]
        event = next(
            event for event in db.query(AuditEvent).filter_by(event_type="COMMAND_APPLIED")
            if event.payload["command_id"] == "brief-answer"
        )
        assert event.payload["brief_updated_fields"] == ["known_scope", "reference_materials"]
        if during_review:
            assert db.get(SpecVersion, "blocked-prd").content == valid_spec.model_dump(mode="json")


@pytest.mark.parametrize("updates", [
    {"final_approver": "intruder"},
    {"known_scope": "not a list"},
    {"final_objective": "   "},
])
def test_invalid_brief_updates_fail_without_saving_an_answer(
    session_factory, complete_brief, updates
):
    """Malformed Agent output cannot corrupt a Brief or grant approval authority."""
    agent = ScriptedAgentGateway(analyze_results=deque([
        ClarificationAnalysis.model_validate(_analysis()),
        _analysis(ready=True, updates=updates),
    ]))
    with TestClient(create_app(agent_gateway=agent, session_factory=session_factory)) as client:
        created = client.post("/sessions", json={
            "request_id": "brief-invalid", "actor_id": "approver-1",
            "brief": complete_brief.model_dump(mode="json"),
        }).json()
        answered = client.post(f"/sessions/{created['session_id']}/commands", json={
            "command_id": "invalid-answer", "action": "message",
            "expected_state_version": created["state_version"], "actor_id": "approver-1",
            "message": "Confirm the requirements.",
        })
        assert answered.status_code == 422
        assert answered.json()["detail"]["code"] == "INVALID_AGENT_RESULT"
    with session_factory() as db:
        project = db.get(Project, created["project_id"])
        assert project.brief == complete_brief.model_dump(mode="json")
        assert project.final_approver == "approver-1"
        assert project.state_version == created["state_version"]
        assert db.query(ClarificationResponse).count() == 0


@pytest.mark.asyncio
async def test_brief_writeback_rolls_back_with_failed_command_and_retries_once(
    session_factory, complete_brief, monkeypatch
):
    """A failed final transaction must not leak a Brief change or consume the answer."""
    agent = ScriptedAgentGateway(analyze_results=deque([
        ClarificationAnalysis.model_validate(_analysis()),
        _analysis(ready=True, updates={"known_scope": ["Web search Q&A"]}),
    ]))
    service = ProjectService(session_factory, agent)
    state = await service.create_session(SessionCreateRequest(
        request_id="brief-rollback", actor_id="approver-1", brief=complete_brief
    ))
    handler = service.as_command_handler()
    materialize = handler.materialize

    def fail_after_writeback(uow, context, prepared):
        materialize(uow, context, prepared)
        raise RuntimeError("simulated transaction failure")

    monkeypatch.setattr(handler, "materialize", fail_after_writeback)
    commands = CommandService(session_factory, handlers={CommandAction.MESSAGE: handler})
    request = SessionCommandRequest(
        command_id="brief-atomic", action=CommandAction.MESSAGE,
        expected_state_version=state.state_version, actor_id="approver-1",
        message="Confirm web search Q&A.",
    )
    with pytest.raises(RuntimeError, match="simulated transaction failure"):
        await commands.execute(state.session_id, request)
    with session_factory() as db:
        project = db.get(Project, state.project_id)
        assert project.brief == complete_brief.model_dump(mode="json")
        assert project.state_version == state.state_version
        assert db.query(ClarificationResponse).count() == 0

    monkeypatch.setattr(handler, "materialize", materialize)
    result = await commands.execute(state.session_id, request)
    assert result.state.state_version == state.state_version + 1
    with session_factory() as db:
        assert db.get(Project, state.project_id).brief["known_scope"] == ["Web search Q&A"]
        assert db.query(ClarificationResponse).count() == 1
        assert db.query(AgentCall).filter_by(operation="analyze_brief").count() == 2
