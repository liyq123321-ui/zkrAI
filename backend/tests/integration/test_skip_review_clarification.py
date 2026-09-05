"""A blocked PRD must return to review; clarification cannot approve or decompose it."""

import pytest
from fastapi.testclient import TestClient

from app.database.models import (
    AgentCall,
    ClarificationRequest,
    ClarificationResponse,
    ProcessedCommand,
    Project,
    SpecReview,
    SpecVersion,
)
from app.domain.types import ProjectPhase, SpecStatus
from main import create_app
from tests.helpers.fake_agent import ScriptedAgentGateway


@pytest.fixture
def blocked_prd(session_factory, complete_brief, valid_spec):
    with session_factory() as db:
        project = Project(
            id="skip-project",
            session_id="skip-session",
            creation_request_id="skip-create",
            brief=complete_brief.model_dump(mode="json"),
            final_approver="approver-1",
            project_manager_ids=["pm-1"],
            root_owner_ids=["owner-1"],
            phase=ProjectPhase.REVIEW.value,
            state_version=17,
            current_spec_version_id="skip-prd-v3",
        )
        db.add_all(
            [
                project,
                SpecVersion(
                    id="skip-prd-v3",
                    project_id=project.id,
                    revision=3,
                    content=valid_spec.model_dump(mode="json"),
                    markdown="# Existing PRD v3",
                    generation_source="PM_AGENT",
                    input_refs=["artifact:brief-1"],
                    generator_agent_session_id="pm",
                    generator_call_id="old-generation",
                    change_summary="Existing PRD",
                    content_hash="b" * 64,
                    status=SpecStatus.NEED_CLARIFICATION.value,
                ),
                ClarificationRequest(
                    id="skip-question",
                    project_id=project.id,
                    spec_version_id="skip-prd-v3",
                    questions=[
                        {
                            "question_id": "source-url",
                            "question": "Provide the final source URLs.",
                            "reason": "Sources have not been frozen.",
                            "affected_areas": ["evidence"],
                            "blocking": True,
                        }
                    ],
                    analysis_round=6,
                ),
                SpecReview(
                    id="old-review",
                    project_id=project.id,
                    spec_version_id="skip-prd-v3",
                    kind="AGENT",
                    reviewer_id="reviewer",
                    input_spec_hash="b" * 64,
                    verdict="NEED_INFO",
                    findings=[],
                    comments="Source URLs are pending.",
                ),
            ]
        )
        db.commit()
        return project


def test_review_clarification_cannot_be_skipped_into_approval(
    session_factory, blocked_prd
):
    request = {
        "command_id": "skip-and-approve",
        "action": "skip_clarification",
        "expected_state_version": 17,
        "actor_id": "approver-1",
        "payload": {
            "confirm_current_spec": True,
            "spec_version_id": "skip-prd-v3",
        },
    }
    with TestClient(
        create_app(
            agent_gateway=ScriptedAgentGateway(),
            session_factory=session_factory,
        )
    ) as client:
        initial = client.get("/sessions/skip-session/state").json()
        assert initial["legal_actions"] == ["message", "restore_spec_version"]
        response = client.post("/sessions/skip-session/commands", json=request)
        assert response.status_code == 400, response.text
        assert response.json()["detail"]["code"] == "ILLEGAL_ACTION"

    with session_factory() as db:
        assert db.get(Project, "skip-project").state_version == 17
        assert db.get(SpecVersion, "skip-prd-v3").status == "NEED_CLARIFICATION"
        assert db.query(ClarificationResponse).count() == 0
        assert db.query(SpecReview).filter_by(kind="HUMAN").count() == 0
        assert db.query(ProcessedCommand).count() == 0
        assert db.query(AgentCall).count() == 0
