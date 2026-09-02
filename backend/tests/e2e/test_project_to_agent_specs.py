"""End-to-end HTTP proof of the Project-to-child-Agent-Spec stop boundary."""

from collections import deque

from fastapi.testclient import TestClient

from app.domain.types import ClarificationAnalysis
from main import create_app
from tests.helpers.factories import (
    make_complete_brief,
    make_passing_semantic_review,
    make_valid_breakdown,
    make_valid_spec,
)
from tests.helpers.fake_agent import ScriptedAgentGateway


def test_approved_spec_becomes_queryable_agent_specs_without_child_execution(session_factory):
    """A broken orchestration boundary could persist bad dependencies or execute child work."""
    blocking = ClarificationAnalysis(
        ready_for_spec=False,
        questions=[
            {
                "question_id": "Q1",
                "question": "Who operates the workflow?",
                "reason": "The user is not explicit.",
                "affected_areas": ["users"],
                "blocking": True,
            }
        ],
        assumptions=[],
    )
    ready = ClarificationAnalysis(ready_for_spec=True, questions=[], assumptions=[])
    def breakdown_for(payload):
        breakdown = make_valid_breakdown()
        refs = list(payload["input_refs"])
        return breakdown.model_copy(
            update={
                "agent_specs": [
                    item.model_copy(update={"context_refs": refs})
                    for item in breakdown.agent_specs
                ]
            }
        )

    agent = ScriptedAgentGateway(
        analyze_results=deque([blocking, ready]),
        generate_results=deque([make_valid_spec()]),
        review_results=deque([make_passing_semantic_review()]),
        review_breakdown_results=deque([make_passing_semantic_review()]),
        decompose_results=deque([breakdown_for]),
    )
    app = create_app(agent_gateway=agent, session_factory=session_factory)
    create_payload = {
        "request_id": "e2e-create-1",
        "actor_id": "approver-1",
        "brief": make_complete_brief().model_dump(mode="json"),
    }

    def command(client, state, command_id, action, *, actor_id="approver-1", message=None):
        body = {
            "command_id": command_id,
            "action": action,
            "expected_state_version": state["state_version"],
            "actor_id": actor_id,
            "payload": {},
        }
        if message is not None:
            body["message"] = message
        response = client.post(f"/sessions/{state['session_id']}/commands", json=body)
        assert response.status_code == 200, response.text
        return response.json()["state"], response.json()

    with TestClient(app) as client:
        state = client.post("/sessions", json=create_payload).json()
        state, _ = command(client, state, "answer-1", "message", message="Operations managers use it.")
        state, create_result = command(client, state, "spec-1", "create_spec")
        approved_version_id = state["current_spec_version_id"]
        state, _ = command(client, state, "approve-1", "approve")
        state, convert_result = command(client, state, "convert-1", "convert_to_work_item")
        specs = client.get(f"/sessions/{state['session_id']}/specs").json()
        work_items = client.get(f"/sessions/{state['session_id']}/work-items").json()
        agent_specs = client.get(f"/sessions/{state['session_id']}/agent-specs").json()
        events = client.get(f"/sessions/{state['session_id']}/events").json()
        spec_by_revision = client.get(f"/sessions/{state['session_id']}/specs/1")

    assert create_result["created_resource_ids"] == [approved_version_id]
    assert state["phase"] == "AGENT_SPECS_READY"
    assert state["state_version"] == 5
    assert len(specs) == 1
    assert len(agent_specs) == 2
    assert specs[0]["project_id"] == state["project_id"]
    assert all(
        review["input_spec_hash"] == specs[0]["content_hash"]
        and review["project_id"] == state["project_id"]
        and review["spec_version_id"] == specs[0]["id"]
        for review in specs[0]["reviews"]
    )
    assert [item["kind"] for item in work_items] == [
        "ROOT",
        "MILESTONE",
        "TASK",
        "TASK",
    ]
    assert all(item["source_spec_version_id"] == approved_version_id for item in agent_specs)
    tasks = {item["local_key"]: item for item in work_items if item["kind"] == "TASK"}
    root = next(item for item in work_items if item["kind"] == "ROOT")
    assert root["project_id"] == state["project_id"]
    assert root["session_id"] == state["session_id"]
    assert root["responsible_role"] == "Project Owner"
    assert root["suggested_assignee"] == "approver-1"
    assert root["scope"] == make_complete_brief().known_scope
    assert root["exclusions"] == make_complete_brief().exclusions
    assert {
        "inputs",
        "outputs",
        "acceptance_criteria",
        "required_skills",
        "responsible_role",
        "suggested_assignee",
        "description",
        "spec",
        "department",
        "type",
    } <= tasks["t-api"].keys()
    api_proposal = make_valid_breakdown().agent_specs[1]
    assert tasks["t-api"]["scope"] == api_proposal.scope
    assert tasks["t-api"]["exclusions"] == api_proposal.exclusions
    assert tasks["t-api"]["inputs"] == api_proposal.inputs
    assert tasks["t-api"]["outputs"] == [item.model_dump(mode="json") for item in api_proposal.outputs]
    assert tasks["t-api"]["acceptance_criteria"] == [
        item.model_dump(mode="json") for item in api_proposal.acceptance_criteria
    ]
    assert tasks["t-api"]["required_skills"] == api_proposal.required_skills
    assert tasks["t-api"]["responsible_role"] == api_proposal.responsible_role
    assert tasks["t-api"]["suggested_assignee"] == api_proposal.suggested_assignee
    by_work_item = {item["work_item_id"]: item for item in agent_specs}
    assert by_work_item[tasks["t-api"]["id"]]["dependency_work_item_ids"] == [tasks["t-domain"]["id"]]
    assert set(convert_result["created_resource_ids"]) == {item["id"] for item in agent_specs}
    assert any(item["event_type"] == "COMMAND_APPLIED" for item in events)
    assert all(
        item["project_id"] == state["project_id"]
        and item["session_id"] == state["session_id"]
        for item in events
    )
    assert spec_by_revision.status_code == 200
    assert spec_by_revision.json()["id"] == approved_version_id
    with TestClient(app) as client:
        assert client.get(
            f"/sessions/{state['session_id']}/work-items/{tasks['t-api']['id']}"
        ).json()["dependency_work_item_ids"] == [tasks["t-domain"]["id"]]
        assert client.get(
            f"/sessions/{state['session_id']}/agent-specs/{by_work_item[tasks['t-api']['id']]['id']}"
        ).json()["work_item_id"] == tasks["t-api"]["id"]
    assert agent.child_process_calls == 0

