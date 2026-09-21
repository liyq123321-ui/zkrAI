"""Existing-PRD intake keeps the source as revision evidence."""

import base64
from collections import deque
from io import BytesIO

from fastapi.testclient import TestClient
from docx import Document

from app.domain.types import ClarificationAnalysis
from app.database.models import Artifact, Project
from main import create_app
from tests.helpers.factories import make_complete_brief
from tests.helpers.fake_agent import ScriptedAgentGateway


def _encoded(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def test_import_prd_extracts_markdown_for_project_creation(session_factory):
    agent = ScriptedAgentGateway()
    with TestClient(
        create_app(agent_gateway=agent, session_factory=session_factory)
    ) as client:
        response = client.post(
            "/sessions/import-prd",
            json={
                "filename": "existing-prd.md",
                "content_base64": _encoded("# Customer Portal\n\n## Current flow\nUsers sign in."),
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "filename": "existing-prd.md",
        "media_type": "text/markdown",
        "title": "Customer Portal",
        "content": "# Customer Portal\n\n## Current flow\nUsers sign in.",
    }


def test_import_prd_extracts_docx_headings_and_paragraphs(session_factory):
    document = Document()
    document.add_heading("Payments Refresh", level=1)
    document.add_paragraph("Keep the existing refund workflow.")
    buffer = BytesIO()
    document.save(buffer)

    with TestClient(
        create_app(agent_gateway=ScriptedAgentGateway(), session_factory=session_factory)
    ) as client:
        response = client.post(
            "/sessions/import-prd",
            json={
                "filename": "payments-prd.docx",
                "content_base64": base64.b64encode(buffer.getvalue()).decode("ascii"),
            },
        )

    assert response.status_code == 200
    assert response.json()["title"] == "Payments Refresh"
    assert response.json()["content"] == (
        "# Payments Refresh\n\nKeep the existing refund workflow."
    )


def test_imported_prd_is_persisted_and_sent_to_intake_agent(session_factory):
    agent = ScriptedAgentGateway(
        analyze_results=deque(
            [ClarificationAnalysis(ready_for_spec=True, questions=[], assumptions=[])]
        )
    )
    source = {
        "filename": "baseline.md",
        "media_type": "text/markdown",
        "title": "Baseline PRD",
        "content": "# Baseline PRD\n\nThe old rule needs correction.",
    }
    brief = make_complete_brief().model_dump(mode="json")
    brief["source_prd"] = source

    with TestClient(
        create_app(agent_gateway=agent, session_factory=session_factory)
    ) as client:
        response = client.post(
            "/sessions",
            json={"request_id": "imported-prd-intake", "actor_id": "owner-1", "brief": brief},
        )

    assert response.status_code == 201
    assert agent.calls[0][0] == "analyze_brief"
    assert agent.calls[0][1]["brief"]["source_prd"] == source
    with session_factory() as db:
        project = db.query(Project).filter_by(creation_request_id="imported-prd-intake").one()
        assert project.brief["source_prd"] == source
        original = db.query(Artifact).filter_by(project_id=project.id, kind="ORIGINAL_REQUIREMENT").one()
        assert original.content["source_prd"] == source


def test_import_prd_rejects_unsupported_files(session_factory):
    with TestClient(
        create_app(agent_gateway=ScriptedAgentGateway(), session_factory=session_factory)
    ) as client:
        response = client.post(
            "/sessions/import-prd",
            json={"filename": "prd.pages", "content_base64": _encoded("content")},
        )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "PRD_IMPORT_INVALID"
