"""Explicitly accepting a blocked PRD permits decomposition without rewriting it."""

from collections import deque

import pytest
from fastapi.testclient import TestClient

from app.database.models import (
    AgentCall, AgentSpec, AuditEvent, ClarificationRequest, ClarificationResponse,
    ProcessedCommand, Project, SpecReview, SpecVersion, WorkItem,
)
from app.domain.types import ProjectPhase, SpecStatus
from app.schemas.workflow import SessionCommandRequest
from app.services.command_service import CommandService
from main import create_app
from tests.helpers.fake_agent import ScriptedAgentGateway


@pytest.fixture
def blocked_prd(session_factory, complete_brief, valid_spec):
    with session_factory() as db:
        project = Project(
            id="skip-project", session_id="skip-session", creation_request_id="skip-create",
            brief=complete_brief.model_dump(mode="json"), final_approver="approver-1",
            project_manager_ids=["pm-1"], root_owner_ids=["owner-1"],
            phase=ProjectPhase.REVIEW.value, state_version=17,
            current_spec_version_id="skip-prd-v3",
        )
        db.add_all([
            project,
            SpecVersion(
                id="skip-prd-v3", project_id=project.id, revision=3,
                content=valid_spec.model_dump(mode="json"), markdown="# Existing PRD v3",
                generation_source="PM_AGENT", input_refs=["artifact:brief-1"],
                generator_agent_session_id="pm", generator_call_id="old-generation",
                change_summary="Existing PRD", content_hash="b" * 64,
                status=SpecStatus.NEED_CLARIFICATION.value,
            ),
            ClarificationRequest(
                id="skip-question", project_id=project.id, spec_version_id="skip-prd-v3",
                questions=[{
                    "question_id": "source-url", "question": "Provide the final source URLs.",
                    "reason": "Sources have not been frozen.",
                    "affected_areas": ["evidence"], "blocking": True,
                }], analysis_round=6,
            ),
            SpecReview(
                id="old-review", project_id=project.id, spec_version_id="skip-prd-v3",
                kind="AGENT", reviewer_id="reviewer", input_spec_hash="b" * 64,
                verdict="NEED_INFO", findings=[], comments="Source URLs are pending.",
            ),
        ])
        db.commit()
        return project


def _skip_request(**overrides):
    return {
        "command_id": "skip-and-approve", "action": "skip_clarification",
        "expected_state_version": 17, "actor_id": "approver-1",
        "payload": {"confirm_current_spec": True, "spec_version_id": "skip-prd-v3"},
        **overrides,
    }


def test_skip_review_confirms_same_prd_then_decomposes_once(
    session_factory, blocked_prd, valid_spec, valid_breakdown
):
    """The button's two commands must preserve the PRD and durable human consent."""
    agent = ScriptedAgentGateway(decompose_results=deque([valid_breakdown]))
    with TestClient(create_app(agent_gateway=agent, session_factory=session_factory)) as client:
        initial = client.get("/sessions/skip-session/state").json()
        assert "skip_clarification" in initial["legal_actions"]
        response = client.post("/sessions/skip-session/commands", json=_skip_request())
        assert response.status_code == 200, response.text
        approved = response.json()["state"]
        assert approved["current_spec_status"] == "APPROVED"
        assert approved["current_spec_version_id"] == "skip-prd-v3"
        assert approved["phase"] == "REVIEW"
        assert approved["state_version"] == 18
        assert approved["outstanding_questions"] == []
        assert "convert_to_work_item" in approved["legal_actions"]
        convert = {
            "command_id": "skip-decompose", "action": "convert_to_work_item",
            "expected_state_version": approved["state_version"], "actor_id": "approver-1",
        }
        decomposed = client.post("/sessions/skip-session/commands", json=convert)
        assert decomposed.status_code == 200, decomposed.text
        assert decomposed.json()["state"]["phase"] == "AGENT_SPECS_READY"
        assert client.post("/sessions/skip-session/commands", json=_skip_request()).json() == response.json()
        assert client.post("/sessions/skip-session/commands", json=convert).json() == decomposed.json()

    with session_factory() as db:
        version = db.get(SpecVersion, "skip-prd-v3")
        assert version.content == valid_spec.model_dump(mode="json")
        assert version.markdown == "# Existing PRD v3"
        assert version.content_hash == "b" * 64
        assert db.query(SpecVersion).count() == 1
        assert db.get(SpecReview, "old-review").verdict == "NEED_INFO"
        review = db.query(SpecReview).filter_by(kind="HUMAN").one()
        assert review.verdict == "PASS"
        assert review.reviewer_id == "approver-1"
        assert review.command_id == "skip-and-approve"
        assert review.input_spec_hash == "b" * 64
        answer = db.query(ClarificationResponse).one()
        assert answer.answers["decision"] == "SKIP_CLARIFICATION"
        assert answer.answers["approved_spec_version_id"] == "skip-prd-v3"
        assert db.query(AuditEvent).filter_by(event_type="SPEC_HUMAN_APPROVED").count() == 1
        assert db.query(AuditEvent).filter_by(event_type="CLARIFICATION_SKIPPED").count() == 1
        assert db.query(ProcessedCommand).count() == 2
        assert db.query(AgentSpec).count() == 2
        assert db.query(AgentCall).filter_by(operation="analyze_brief").count() == 0
        assert db.query(AgentCall).filter_by(operation="generate_spec").count() == 0
        assert db.query(AgentCall).filter_by(operation="decompose_spec").count() == 1


@pytest.mark.parametrize("overrides,status,code", [
    ({"actor_id": "outsider"}, 403, "FORBIDDEN_ACTOR"),
    ({"expected_state_version": 16}, 409, "STALE_STATE"),
    ({"payload": {}}, 422, "VALIDATION_ERROR"),
    ({"payload": {"confirm_current_spec": False, "spec_version_id": "skip-prd-v3"}}, 422, "VALIDATION_ERROR"),
    ({"payload": {"confirm_current_spec": True, "spec_version_id": "older-prd"}}, 409, "STALE_STATE"),
])
def test_skip_review_rejects_missing_or_stale_consent(
    session_factory, blocked_prd, overrides, status, code
):
    with TestClient(create_app(agent_gateway=ScriptedAgentGateway(), session_factory=session_factory)) as client:
        result = client.post("/sessions/skip-session/commands", json=_skip_request(**overrides))
        assert result.status_code == status, result.text
        assert result.json()["detail"]["code"] == code
    with session_factory() as db:
        assert db.get(Project, "skip-project").state_version == 17
        assert db.get(SpecVersion, "skip-prd-v3").status == "NEED_CLARIFICATION"
        assert db.query(ClarificationResponse).count() == 0
        assert db.query(SpecReview).filter_by(kind="HUMAN").count() == 0
        assert db.query(ProcessedCommand).count() == 0


@pytest.mark.asyncio
async def test_skip_review_approval_and_answer_roll_back_together(
    session_factory, blocked_prd, monkeypatch
):
    service = CommandService(session_factory)

    def fail_projection(*args):
        raise RuntimeError("simulated persistence failure")

    monkeypatch.setattr(service, "_state", fail_projection)
    with pytest.raises(RuntimeError, match="simulated persistence failure"):
        await service.execute("skip-session", SessionCommandRequest.model_validate(_skip_request()))
    with session_factory() as db:
        assert db.get(Project, "skip-project").state_version == 17
        assert db.get(SpecVersion, "skip-prd-v3").status == "NEED_CLARIFICATION"
        assert db.query(ClarificationResponse).count() == 0
        assert db.query(SpecReview).filter_by(kind="HUMAN").count() == 0
        assert db.query(AuditEvent).count() == 0


def test_decomposition_failure_preserves_approval_for_retry(
    session_factory, blocked_prd, valid_breakdown
):
    agent = ScriptedAgentGateway(decompose_results=deque([
        RuntimeError("Agent unavailable"), valid_breakdown,
    ]))
    with TestClient(create_app(agent_gateway=agent, session_factory=session_factory)) as client:
        skipped = client.post("/sessions/skip-session/commands", json=_skip_request())
        assert skipped.status_code == 200, skipped.text
        request = {
            "command_id": "retry-decompose", "action": "convert_to_work_item",
            "expected_state_version": 18, "actor_id": "approver-1",
        }
        assert client.post("/sessions/skip-session/commands", json=request).status_code == 503
        state = client.get("/sessions/skip-session/state").json()
        assert state["current_spec_status"] == "APPROVED"
        assert state["state_version"] == 18
        assert "convert_to_work_item" in state["legal_actions"]
        result = client.post("/sessions/skip-session/commands", json=request)
        assert result.status_code == 200, result.text
        assert result.json()["state"]["phase"] == "AGENT_SPECS_READY"
    with session_factory() as db:
        assert db.query(SpecReview).filter_by(kind="HUMAN").count() == 1
        assert db.query(ClarificationResponse).count() == 1
        assert db.query(WorkItem).filter_by(kind="TASK").count() == 2
