"""Execution across the WorkItem dependency DAG, end to end.

These tests prove the graph actually drives behaviour: a task may only start
once every dependency is done, and finishing one unlocks its successors. That
was the missing half of the decomposition pipeline -- the DAG was validated and
persisted but nothing ever traversed it.
"""

from collections import deque

import pytest
from fastapi.testclient import TestClient

from app.database.models import WorkItemRun
from app.domain.types import ClarificationAnalysis
from main import create_app
from tests.helpers.factories import (
    make_complete_brief,
    make_passing_semantic_review,
    make_valid_breakdown,
    make_valid_spec,
)
from tests.helpers.fake_agent import ScriptedAgentGateway


def _breakdown_with_context_refs(payload):
    """Mirror the e2e fixture: AgentSpec context refs must name real inputs."""

    refs = list(payload["input_refs"])
    breakdown = make_valid_breakdown()
    return breakdown.model_copy(
        update={
            "agent_specs": [
                item.model_copy(update={"context_refs": refs})
                for item in breakdown.agent_specs
            ]
        }
    )


def _app(session_factory):
    agent = ScriptedAgentGateway(
        analyze_results=deque(
            [ClarificationAnalysis(ready_for_spec=True, questions=[], assumptions=[])]
        ),
        generate_results=deque([make_valid_spec()]),
        review_results=deque([make_passing_semantic_review()]),
        review_breakdown_results=deque([make_passing_semantic_review()]),
        decompose_results=deque([_breakdown_with_context_refs]),
    )
    return create_app(agent_gateway=agent, session_factory=session_factory)


def _post(client, session_id, command_id, action, state_version, payload):
    return client.post(
        f"/sessions/{session_id}/commands",
        json={
            "command_id": command_id,
            "action": action,
            "expected_state_version": state_version,
            "actor_id": "approver-1",
            "payload": payload,
        },
    )


def _run(client, state, command_id, action, payload=None):
    """Send a command that must succeed; return the next state."""

    response = _post(
        client, state["session_id"], command_id, action,
        state["state_version"], payload or {},
    )
    assert response.status_code == 200, response.text
    return response.json()["state"]


def _reject(client, state, command_id, action, payload=None):
    """Send a command that must be rejected; return the error code.

    The dependency graph rejecting a command is a client input problem, so the
    engine reports it as 422 VALIDATION_ERROR rather than a server fault.
    """

    response = _post(
        client, state["session_id"], command_id, action,
        state["state_version"], payload or {},
    )
    assert response.status_code == 422, response.text
    return response.json()["detail"]["code"]


def _reach_agent_specs_ready(client):
    state = client.post(
        "/sessions",
        json={
            "request_id": "exec-create",
            "actor_id": "approver-1",
            "brief": make_complete_brief().model_dump(mode="json"),
        },
    ).json()
    state = _run(client, state, "spec-1", "create_spec")
    state = _run(client, state, "approve-1", "approve")
    return _run(client, state, "convert-1", "convert_to_work_item")


def _items(client, session_id):
    return client.get(f"/sessions/{session_id}/work-items").json()


def _by_key(items, key):
    return next(item for item in items if item["local_key"] == key)


@pytest.fixture
def ready_state(session_factory):
    with TestClient(_app(session_factory)) as client:
        state = _reach_agent_specs_ready(client)
        yield client, state


def test_dependency_order_drives_what_can_start(ready_state):
    """t-api depends on t-domain, so only the leaf is startable at first."""

    client, state = ready_state
    items = _items(client, state["session_id"])

    assert _by_key(items, "t-domain")["available_actions"] == ["start_task"]
    assert _by_key(items, "t-api")["available_actions"] == []
    # Dependency edges are projected for the UI to render the real graph.
    assert _by_key(items, "t-api")["dependency_work_item_ids"] == [
        _by_key(items, "t-domain")["id"]
    ]
    # Depth comes from the graph, not from item kind.
    assert _by_key(items, "t-domain")["graph_depth"] == 0
    assert _by_key(items, "t-api")["graph_depth"] == 1


def test_completing_a_dependency_unlocks_its_successor(ready_state):
    """The whole point of the DAG: finishing work makes the next work legal."""

    client, state = ready_state
    domain = _by_key(_items(client, state["session_id"]), "t-domain")

    state = _run(client, state, "start-domain", "start_task",
                 {"work_item_id": domain["id"]})
    assert _by_key(_items(client, state["session_id"]), "t-domain")["status"] == (
        "in_progress"
    )

    # Finishing the dependency is what unlocks the successor.
    state = _run(client, state, "done-domain", "complete_task",
                 {"work_item_id": domain["id"]})
    items = _items(client, state["session_id"])
    assert _by_key(items, "t-domain")["status"] == "done"
    assert _by_key(items, "t-api")["available_actions"] == ["start_task"]

    api = _by_key(items, "t-api")
    state = _run(client, state, "start-api", "start_task",
                 {"work_item_id": api["id"]})
    state = _run(client, state, "done-api", "complete_task",
                 {"work_item_id": api["id"]})
    items = _items(client, state["session_id"])
    assert _by_key(items, "t-api")["status"] == "done"
    assert all(item["available_actions"] == [] for item in items)


def test_starting_a_blocked_task_is_rejected(ready_state):
    """The engine must refuse even if a client forges the command."""

    client, state = ready_state
    api = _by_key(_items(client, state["session_id"]), "t-api")

    code = _reject(client, state, "start-blocked", "start_task",
                   {"work_item_id": api["id"]})
    assert code == "VALIDATION_ERROR"


def test_start_task_twice_is_rejected(ready_state):
    client, state = ready_state
    domain = _by_key(_items(client, state["session_id"]), "t-domain")

    state = _run(client, state, "start-once", "start_task",
                 {"work_item_id": domain["id"]})
    code = _reject(client, state, "start-twice", "start_task",
                   {"work_item_id": domain["id"]})
    assert code == "VALIDATION_ERROR"
    assert _by_key(_items(client, state["session_id"]), "t-domain")["status"] == (
        "in_progress"
    )


def test_completing_a_task_that_never_started_is_rejected(ready_state):
    client, state = ready_state
    domain = _by_key(_items(client, state["session_id"]), "t-domain")

    code = _reject(client, state, "complete-cold", "complete_task",
                   {"work_item_id": domain["id"]})
    assert code == "VALIDATION_ERROR"


def test_root_and_milestone_cannot_be_executed(ready_state):
    """Only TASK items are executable; the graph roots are planning nodes."""

    client, state = ready_state
    items = _items(client, state["session_id"])
    root = next(item for item in items if item["kind"] == "ROOT")
    milestone = next(item for item in items if item["kind"] == "MILESTONE")

    assert root["available_actions"] == []
    assert milestone["available_actions"] == []
    assert _reject(client, state, "start-root", "start_task",
                   {"work_item_id": root["id"]}) == "VALIDATION_ERROR"
    assert _reject(client, state, "start-ms", "start_task",
                   {"work_item_id": milestone["id"]}) == "VALIDATION_ERROR"


def test_missing_work_item_id_is_rejected(ready_state):
    client, state = ready_state

    assert _reject(client, state, "start-empty", "start_task",
                   {}) == "VALIDATION_ERROR"


def test_replaying_a_command_records_exactly_one_run(ready_state, session_factory):
    """Idempotency is enforced twice: by the receipt and by the DB constraint."""

    client, state = ready_state
    domain = _by_key(_items(client, state["session_id"]), "t-domain")

    first = _post(client, state["session_id"], "start-replay", "start_task",
                  state["state_version"], {"work_item_id": domain["id"]})
    assert first.status_code == 200, first.text
    replay = _post(client, state["session_id"], "start-replay", "start_task",
                   state["state_version"], {"work_item_id": domain["id"]})
    assert replay.status_code == 200, replay.text
    assert replay.json() == first.json()

    with session_factory() as db:
        runs = db.query(WorkItemRun).filter_by(command_id="start-replay").all()
    assert len(runs) == 1


def test_failed_task_can_be_restarted(ready_state):
    client, state = ready_state
    domain = _by_key(_items(client, state["session_id"]), "t-domain")

    state = _run(client, state, "start-f", "start_task",
                 {"work_item_id": domain["id"]})
    state = _run(client, state, "fail-f", "fail_task",
                 {"work_item_id": domain["id"]})
    items = _items(client, state["session_id"])
    assert _by_key(items, "t-domain")["status"] == "failed"
    assert _by_key(items, "t-domain")["available_actions"] == ["start_task"]
    # A failed task must not unlock its successor.
    assert _by_key(items, "t-api")["available_actions"] == []


def test_stale_state_version_is_rejected(ready_state):
    """The existing optimistic lock keeps concurrent starts honest."""

    client, state = ready_state
    domain = _by_key(_items(client, state["session_id"]), "t-domain")

    response = _post(client, state["session_id"], "start-stale", "start_task",
                     state["state_version"] + 7, {"work_item_id": domain["id"]})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "STALE_STATE"
