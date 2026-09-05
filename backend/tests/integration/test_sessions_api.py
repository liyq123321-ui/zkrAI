"""HTTP contract coverage for project workflow sessions."""

import asyncio
import json
from collections import deque

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agents.codex import AgentExecutionError, AgentOutputError
from app.api.sessions import build_router as build_sessions_router
from app.database.models import (
    AgentCall,
    AuditEvent,
    ClarificationRequest,
    ClarificationResponse,
    Project,
    WorkItem,
)
from app.domain.types import ClarificationAnalysis, ReviewVerdict, SemanticReview
from app.identity import ActorResolver
from app.schemas.workflow import CommandResult, SessionState
from app.services.command_jobs import CommandJobCoordinator
from app.services.decomposition_service import BreakdownValidationError, DecompositionService
from main import create_app
from tests.helpers.factories import (
    make_complete_brief,
    make_passing_semantic_review,
    make_valid_breakdown,
    make_valid_spec,
)
from tests.helpers.fake_agent import ScriptedAgentGateway


def _blocking_analysis(question_id: str, question: str) -> ClarificationAnalysis:
    return ClarificationAnalysis(
        ready_for_spec=False,
        questions=[
            {
                "question_id": question_id,
                "question": question,
                "reason": "A human decision is required.",
                "affected_areas": ["users"],
                "blocking": True,
            }
        ],
        assumptions=[],
    )


def _approved_http_session(client: TestClient, request_id: str) -> dict[str, object]:
    created = client.post(
        "/sessions",
        json={
            "request_id": request_id,
            "actor_id": "approver-1",
            "brief": make_complete_brief().model_dump(mode="json"),
        },
    ).json()
    specified = client.post(
        f"/sessions/{created['session_id']}/commands",
        json={
            "command_id": f"{request_id}-spec",
            "action": "create_spec",
            "expected_state_version": created["state_version"],
            "actor_id": "approver-1",
            "payload": {},
        },
    ).json()["state"]
    return client.post(
        f"/sessions/{created['session_id']}/commands",
        json={
            "command_id": f"{request_id}-approve",
            "action": "approve",
            "expected_state_version": specified["state_version"],
            "actor_id": "approver-1",
            "payload": {},
        },
    ).json()["state"]


def _breakdown_with_input_refs(payload):
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


def test_command_job_openapi_contract_distinguishes_sync_and_async_responses(
    session_factory,
):
    schema = create_app(session_factory=session_factory).openapi()

    command_responses = schema["paths"]["/sessions/{session_id}/commands"]["post"]["responses"]
    assert command_responses["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/CommandResult"
    }
    assert command_responses["202"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/CommandJobAccepted"
    }

    status_response = schema["paths"][
        "/sessions/{session_id}/commands/{command_id}"
    ]["get"]["responses"]["200"]
    assert status_response["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/CommandJobRead"
    }

    events = schema["paths"][
        "/sessions/{session_id}/commands/{command_id}/events"
    ]["get"]
    assert events["responses"]["200"]["content"] == {
        "text/event-stream": {"schema": {"type": "string"}}
    }
    assert any(parameter["name"] == "Last-Event-ID" for parameter in events["parameters"])


def test_decomposition_returns_accepted_job_and_status_is_pollable(session_factory):
    agent = ScriptedAgentGateway(
        analyze_results=deque([ClarificationAnalysis(
            ready_for_spec=True, questions=[], assumptions=[]
        )]),
        generate_results=deque([make_valid_spec()]),
        review_results=deque([make_passing_semantic_review()]),
        decompose_results=deque([_breakdown_with_input_refs]),
    )
    with TestClient(create_app(agent_gateway=agent, session_factory=session_factory)) as client:
        approved = _approved_http_session(client, "async-decompose")
        response = client.post(
            f"/sessions/{approved['session_id']}/commands",
            json={
                "command_id": "decompose-accepted",
                "action": "convert_to_work_item",
                "expected_state_version": approved["state_version"],
                "actor_id": "approver-1",
                "payload": {},
            },
        )
        assert response.status_code == 202
        assert response.headers["content-type"].startswith("application/json")
        accepted = response.json()
        assert accepted == {
            "command_id": "decompose-accepted",
            "status": "pending",
            "status_url": f"/sessions/{approved['session_id']}/commands/decompose-accepted",
            "events_url": f"/sessions/{approved['session_id']}/commands/decompose-accepted/events",
        }
        status_response = client.get(accepted["status_url"])
        assert status_response.status_code == 200
        assert status_response.json()["status"] == "succeeded"
        assert status_response.json()["result"]["state"]["phase"] == "AGENT_SPECS_READY"


def test_non_decomposition_command_still_returns_completed_200(session_factory):
    agent = ScriptedAgentGateway(analyze_results=deque([
        _blocking_analysis("Q-SYNC", "Which deployment boundary applies?")
    ]))
    with TestClient(create_app(agent_gateway=agent, session_factory=session_factory)) as client:
        created = client.post("/sessions", json={
            "request_id": "sync-command",
            "actor_id": "approver-1",
            "brief": make_complete_brief().model_dump(mode="json"),
        }).json()
        response = client.post(f"/sessions/{created['session_id']}/commands", json={
            "command_id": "sync-skip",
            "action": "skip_clarification",
            "expected_state_version": created["state_version"],
            "actor_id": "approver-1",
            "payload": {},
        })
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")
        assert "state" in response.json()
        assert "status_url" not in response.json()


@pytest.mark.asyncio
async def test_decomposition_response_is_emitted_before_blocked_executor_finishes(
    session_factory, test_settings
):
    executor_started = asyncio.Event()
    release_executor = asyncio.Event()

    async def blocked_execute(session_id, command):
        executor_started.set()
        await release_executor.wait()
        return CommandResult(
            command_id=command.command_id,
            state=SessionState(
                session_id=session_id,
                project_id="blocked-project",
                phase="AGENT_SPECS_READY",
                state_version=5,
                current_spec_version_id="blocked-spec",
                current_spec_status="APPROVED",
                legal_actions=[],
                next_action="NONE",
                outstanding_questions=[],
                review_findings=[],
            ),
        )

    agent = ScriptedAgentGateway(
        analyze_results=deque([ClarificationAnalysis(
            ready_for_spec=True, questions=[], assumptions=[]
        )]),
        generate_results=deque([make_valid_spec()]),
        review_results=deque([make_passing_semantic_review()]),
    )
    command_jobs = CommandJobCoordinator(session_factory, blocked_execute)
    app = FastAPI()
    app.include_router(build_sessions_router(
        session_factory,
        agent,
        ActorResolver(test_settings),
        command_jobs=command_jobs,
    ))

    with TestClient(app) as client:
        approved = _approved_http_session(client, "blocked-acceptance")

    request_body = json.dumps({
        "command_id": "blocked-decompose",
        "action": "convert_to_work_item",
        "expected_state_version": approved["state_version"],
        "actor_id": "approver-1",
        "payload": {},
    }).encode()
    sent_messages = []
    response_body_emitted = asyncio.Event()
    request_consumed = False

    async def receive():
        nonlocal request_consumed
        if not request_consumed:
            request_consumed = True
            return {"type": "http.request", "body": request_body}
        await asyncio.Future()

    async def send(message):
        sent_messages.append(message)
        if message["type"] == "http.response.body" and not message.get(
            "more_body", False
        ):
            response_body_emitted.set()

    task = asyncio.create_task(app({
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": f"/sessions/{approved['session_id']}/commands",
        "raw_path": f"/sessions/{approved['session_id']}/commands".encode(),
        "query_string": b"",
        "headers": [(b"content-type", b"application/json")],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }, receive, send))
    try:
        await asyncio.wait_for(response_body_emitted.wait(), timeout=1)
        await asyncio.wait_for(executor_started.wait(), timeout=1)
        assert task.done() is False
        assert sent_messages[0]["type"] == "http.response.start"
        assert sent_messages[0]["status"] == 202
        assert json.loads(sent_messages[1]["body"]) == {
            "command_id": "blocked-decompose",
            "status": "pending",
            "status_url": f"/sessions/{approved['session_id']}/commands/blocked-decompose",
            "events_url": f"/sessions/{approved['session_id']}/commands/blocked-decompose/events",
        }
    finally:
        release_executor.set()
        await asyncio.wait_for(task, timeout=1)


@pytest.fixture
def async_job_client(session_factory):
    agent = ScriptedAgentGateway(
        analyze_results=deque([ClarificationAnalysis(
            ready_for_spec=True, questions=[], assumptions=[]
        )]),
        generate_results=deque([make_valid_spec()]),
        review_results=deque([make_passing_semantic_review()]),
        decompose_results=deque([_breakdown_with_input_refs]),
    )
    with TestClient(create_app(
        agent_gateway=agent, session_factory=session_factory
    )) as client:
        approved = _approved_http_session(client, "sse-command")
        command_id = "sse-decompose"
        response = client.post(
            f"/sessions/{approved['session_id']}/commands",
            json={
                "command_id": command_id,
                "action": "convert_to_work_item",
                "expected_state_version": approved["state_version"],
                "actor_id": "approver-1",
                "payload": {},
            },
        )
        assert response.status_code == 202
        yield client, approved["session_id"], command_id


def test_command_job_sse_emits_terminal_snapshot_with_version_id(async_job_client):
    client, session_id, command_id = async_job_client
    with client.stream(
        "GET", f"/sessions/{session_id}/commands/{command_id}/events"
    ) as response:
        body = "".join(response.iter_text())
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: command.status" in body
    assert "id: 3" in body
    assert '"status":"succeeded"' in body


def test_command_job_sse_honors_current_last_event_id(async_job_client):
    client, session_id, command_id = async_job_client
    response = client.get(
        f"/sessions/{session_id}/commands/{command_id}/events",
        headers={"Last-Event-ID": "3"},
    )
    assert response.status_code == 200
    assert "event: command.status" not in response.text


def test_command_job_reads_do_not_cross_session_boundary(async_job_client):
    client, _, command_id = async_job_client
    response = client.get(f"/sessions/another-session/commands/{command_id}")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "NOT_FOUND"


def test_session_creation_returns_state(session_factory):
    """Removing the Session router would make structured project intake unreachable."""
    payload = {
        "request_id": "http-create-1",
        "actor_id": "approver-1",
        "brief": make_complete_brief().model_dump(mode="json"),
    }

    agent = ScriptedAgentGateway(
        analyze_results=deque(
            [ClarificationAnalysis(ready_for_spec=True, questions=[], assumptions=[])]
        )
    )
    with TestClient(create_app(agent_gateway=agent, session_factory=session_factory)) as client:
        response = client.post("/sessions", json=payload)

    assert response.status_code == 201
    body = response.json()
    assert body["session_id"]
    assert body["phase"] in {"NEED_CLARIFICATION", "SPECIFICATION"}
    assert body["state_version"] == 1


def test_session_catalog_lists_database_roots_without_creating_agent_runs(session_factory):
    agent = ScriptedAgentGateway(analyze_results=deque([
        ClarificationAnalysis(ready_for_spec=True, questions=[], assumptions=[]),
        ClarificationAnalysis(ready_for_spec=True, questions=[], assumptions=[]),
    ]))
    with TestClient(create_app(agent_gateway=agent, session_factory=session_factory)) as client:
        empty = client.get("/sessions")
        assert empty.status_code == 200
        assert empty.json() == []
        for index in range(2):
            brief = make_complete_brief().model_copy(update={"final_objective": f"项目 {index}"})
            response = client.post("/sessions", json={
                "request_id": f"catalog-{index}", "actor_id": "approver-1",
                "brief": brief.model_dump(mode="json"),
            })
            assert response.status_code == 201
        with session_factory() as db:
            roots = db.query(WorkItem).filter_by(kind="ROOT").all()
            expected = {root.session_id: (root.project_id, root.id, root.title) for root in roots}
            versions_before = {item.id: item.state_version for item in db.query(Project).all()}
            event_count = db.query(AuditEvent).count()
        calls_before = len(agent.calls)
        response = client.get("/sessions")
        assert response.status_code == 200
        assert {item["session_id"]: (item["project_id"], item["root_work_item_id"], item["title"]) for item in response.json()} == expected
        assert len(response.json()) == 2
        assert client.get("/sessions").json() == response.json()
        assert len(agent.calls) == calls_before
        with session_factory() as db:
            assert {item.id: item.state_version for item in db.query(Project).all()} == versions_before
            assert db.query(AuditEvent).count() == event_count


def test_user_can_skip_intake_clarification_and_generate_spec_with_agent_assumptions(
    session_factory,
):
    """Skipping must close the question without another PM-analysis loop."""

    agent = ScriptedAgentGateway(
        analyze_results=deque([
            _blocking_analysis("Q-SKIP-1", "Which edge-case policy should apply?")
        ]),
        generate_results=deque([make_valid_spec()]),
        review_results=deque([make_passing_semantic_review()]),
    )
    with TestClient(
        create_app(agent_gateway=agent, session_factory=session_factory)
    ) as client:
        created = client.post(
            "/sessions",
            json={
                "request_id": "skip-clarification",
                "actor_id": "approver-1",
                "brief": make_complete_brief().model_dump(mode="json"),
            },
        ).json()

        assert created["phase"] == "NEED_CLARIFICATION"
        assert created["legal_actions"] == ["message", "skip_clarification"]

        skipped_response = client.post(
            f"/sessions/{created['session_id']}/commands",
            json={
                "command_id": "skip-clarification-command",
                "action": "skip_clarification",
                "expected_state_version": created["state_version"],
                "actor_id": "approver-1",
                "payload": {},
            },
        )
        skipped = skipped_response.json()["state"]

        assert skipped_response.status_code == 200
        assert skipped["phase"] == "SPECIFICATION"
        assert skipped["outstanding_questions"] == []
        assert skipped["legal_actions"] == ["create_spec"]

        generated_response = client.post(
            f"/sessions/{created['session_id']}/commands",
            json={
                "command_id": "generate-after-skip",
                "action": "create_spec",
                "expected_state_version": skipped["state_version"],
                "actor_id": "approver-1",
                "payload": {},
            },
        )

    assert generated_response.status_code == 200
    assert generated_response.json()["state"]["phase"] == "REVIEW"
    assert [operation for operation, _ in agent.calls] == [
        "analyze_brief",
        "generate_spec",
        "review_spec",
    ]
    generation_payload = next(
        payload for operation, payload in agent.calls if operation == "generate_spec"
    )
    skip_response = generation_payload["clarification_history"][0]["responses"][0]
    assert skip_response["answers"]["decision"] == "SKIP_CLARIFICATION"
    assert skip_response["answers"]["assumption_policy"] == "AGENT_DISCRETION"

    with session_factory() as db:
        response = db.query(ClarificationResponse).one()
        event = db.query(AuditEvent).filter_by(
            event_type="CLARIFICATION_SKIPPED"
        ).one()
        assert response.actor_id == "approver-1"
        assert event.actor_id == "approver-1"


def test_public_events_include_safe_agent_trace_without_raw_inputs_or_outputs(session_factory):
    secret = "private-project-secret"
    agent = ScriptedAgentGateway(
        analyze_results=deque([
            ClarificationAnalysis(ready_for_spec=True, questions=[], assumptions=[secret])
        ])
    )
    with TestClient(
        create_app(agent_gateway=agent, session_factory=session_factory)
    ) as client:
        created = client.post(
            "/sessions",
            json={
                "request_id": "safe-trace",
                "actor_id": "approver-1",
                "brief": make_complete_brief().model_dump(mode="json"),
            },
        ).json()
        events = client.get(f"/sessions/{created['session_id']}/events")

    assert events.status_code == 200
    trace = next(item for item in events.json() if item["event_type"] == "AGENT_TRACE")
    assert set(trace["payload"]) == {
        "trace_id",
        "agent_call_id",
        "sequence",
        "phase",
        "summary",
        "input_hash",
        "output_hash",
        "status",
        "started_at",
        "completed_at",
        "safe_error_code",
    }
    assert trace["payload"]["phase"] == "analyze_brief"
    assert trace["payload"]["status"] == "done"
    assert secret not in events.text


def test_restore_historical_spec_creates_a_new_reviewed_revision(session_factory):
    """Restoring history must create n+1 without changing either historical revision."""
    first_spec = make_valid_spec()
    second_spec = first_spec.model_copy(
        update={"main_flows": [*first_spec.main_flows, "A newer flow that will be superseded."]}
    )
    agent = ScriptedAgentGateway(
        analyze_results=deque([
            ClarificationAnalysis(ready_for_spec=True, questions=[], assumptions=[])
        ]),
        generate_results=deque([first_spec, second_spec]),
        review_results=deque([
            make_passing_semantic_review(),
            make_passing_semantic_review(),
            make_passing_semantic_review(),
        ]),
    )
    with TestClient(create_app(agent_gateway=agent, session_factory=session_factory)) as client:
        created = client.post(
            "/sessions",
            json={
                "request_id": "restore-project",
                "actor_id": "approver-1",
                "brief": make_complete_brief().model_dump(mode="json"),
            },
        ).json()
        v1_state = client.post(
            f"/sessions/{created['session_id']}/commands",
            json={
                "command_id": "restore-v1",
                "action": "create_spec",
                "expected_state_version": created["state_version"],
                "actor_id": "approver-1",
                "payload": {},
            },
        ).json()["state"]
        rework_state = client.post(
            f"/sessions/{created['session_id']}/commands",
            json={
                "command_id": "restore-rework",
                "action": "rework",
                "expected_state_version": v1_state["state_version"],
                "actor_id": "approver-1",
                "message": "Create a second version.",
                "payload": {},
            },
        ).json()["state"]
        v2_state = client.post(
            f"/sessions/{created['session_id']}/commands",
            json={
                "command_id": "restore-v2",
                "action": "revise",
                "expected_state_version": rework_state["state_version"],
                "actor_id": "approver-1",
                "message": "Create a second version.",
                "payload": {},
            },
        ).json()["state"]

        response = client.post(
            f"/sessions/{created['session_id']}/commands",
            json={
                "command_id": "restore-v3",
                "action": "restore_spec_version",
                "expected_state_version": v2_state["state_version"],
                "actor_id": "approver-1",
                "message": "The first version is the approved business baseline.",
                "payload": {"source_revision": 1},
            },
        )
        versions = client.get(f"/sessions/{created['session_id']}/specs").json()
        events = client.get(f"/sessions/{created['session_id']}/events").json()

    assert response.status_code == 200
    assert [item["revision"] for item in versions] == [1, 2, 3]
    assert versions[2]["content"] == versions[0]["content"]
    assert versions[2]["content"] != versions[1]["content"]
    assert versions[2]["parent_version_id"] == versions[1]["id"]
    assert versions[2]["generation_source"] == "HISTORICAL_RESTORE"
    applied = next(
        item for item in events
        if item["event_type"] == "COMMAND_APPLIED"
        and item["payload"].get("command_id") == "restore-v3"
    )
    assert applied["payload"]["source_revision"] == 1
    assert applied["payload"]["source_spec_version_id"] == versions[0]["id"]


def test_convert_before_approval_returns_illegal_action(session_factory):
    """Skipping the command policy would allow executable task creation before approval."""
    agent = ScriptedAgentGateway(
        analyze_results=deque(
            [
                ClarificationAnalysis(
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
            ]
        )
    )
    app = create_app(agent_gateway=agent, session_factory=session_factory)
    payload = {
        "request_id": "http-gate-1",
        "actor_id": "approver-1",
        "brief": make_complete_brief().model_dump(mode="json"),
    }
    with TestClient(app) as client:
        created = client.post("/sessions", json=payload).json()
        session_id = created["session_id"]
        command_body = {
            "command_id": "convert-early",
            "action": "convert_to_work_item",
            "expected_state_version": created["state_version"],
            "actor_id": "approver-1",
            "payload": {},
        }
        expected_error_code = "ILLEGAL_ACTION"
        submission = client.post(f"/sessions/{session_id}/commands", json=command_body)
        snapshot = client.get(submission.json()["status_url"])

    assert submission.status_code == 202
    assert snapshot.status_code == 200
    assert snapshot.json()["status"] == "failed"
    assert snapshot.json()["error"]["code"] == expected_error_code


def test_state_query_and_session_not_found_are_stable(session_factory):
    """Leaking ORM errors would make missing and existing Session reads unstable for clients."""
    agent = ScriptedAgentGateway(
        analyze_results=deque(
            [ClarificationAnalysis(ready_for_spec=True, questions=[], assumptions=[])]
        )
    )
    app = create_app(agent_gateway=agent, session_factory=session_factory)
    payload = {
        "request_id": "http-state-1",
        "actor_id": "approver-1",
        "brief": make_complete_brief().model_dump(mode="json"),
    }
    with TestClient(app) as client:
        created = client.post("/sessions", json=payload).json()
        state = client.get(f"/sessions/{created['session_id']}/state")
        missing = client.get("/sessions/missing/state")

    assert state.status_code == 200
    assert state.json() == created
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "NOT_FOUND"


def test_create_replay_is_idempotent_and_changed_body_conflicts(session_factory):
    """Reusing a request ID with changed intent must not silently return the first project."""
    agent = ScriptedAgentGateway(
        analyze_results=deque(
            [ClarificationAnalysis(ready_for_spec=True, questions=[], assumptions=[])]
        )
    )
    app = create_app(agent_gateway=agent, session_factory=session_factory)
    payload = {
        "request_id": "http-replay-1",
        "actor_id": "approver-1",
        "brief": make_complete_brief().model_dump(mode="json"),
    }
    changed = {
        **payload,
        "brief": {**payload["brief"], "final_objective": "A changed objective"},
    }
    with TestClient(app) as client:
        first = client.post("/sessions", json=payload)
        replay = client.post("/sessions", json=payload)
        conflict = client.post("/sessions", json=changed)

    assert replay.status_code == 201
    assert replay.json() == first.json()
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "REQUEST_CONFLICT"


def test_command_replay_conflict_stale_version_and_authority_are_stable(session_factory):
    """HTTP callers must not bypass idempotency, optimistic locking, or stored responsibility."""
    agent = ScriptedAgentGateway(
        analyze_results=deque(
            [ClarificationAnalysis(ready_for_spec=True, questions=[], assumptions=[])]
        ),
        generate_results=deque([make_valid_spec()]),
        review_results=deque([make_passing_semantic_review()]),
    )
    app = create_app(agent_gateway=agent, session_factory=session_factory)
    payload = {
        "request_id": "http-command-guards",
        "actor_id": "approver-1",
        "brief": make_complete_brief().model_dump(mode="json"),
    }
    with TestClient(app) as client:
        created = client.post("/sessions", json=payload).json()
        create_command = {
            "command_id": "create-spec-once",
            "action": "create_spec",
            "expected_state_version": created["state_version"],
            "actor_id": "approver-1",
            "payload": {},
        }
        first = client.post(
            f"/sessions/{created['session_id']}/commands", json=create_command
        )
        replay = client.post(
            f"/sessions/{created['session_id']}/commands", json=create_command
        )
        changed = client.post(
            f"/sessions/{created['session_id']}/commands",
            json={**create_command, "message": "different input"},
        )
        stale = client.post(
            f"/sessions/{created['session_id']}/commands",
            json={
                "command_id": "approve-stale",
                "action": "approve",
                "expected_state_version": created["state_version"],
                "actor_id": "approver-1",
                "payload": {},
            },
        )
        forbidden = client.post(
            f"/sessions/{created['session_id']}/commands",
            json={
                "command_id": "approve-outsider",
                "action": "approve",
                "expected_state_version": first.json()["state"]["state_version"],
                "actor_id": "outsider",
                "payload": {},
            },
        )

    assert first.status_code == 200
    assert replay.json() == first.json()
    assert changed.status_code == 409
    assert changed.json()["detail"]["code"] == "COMMAND_CONFLICT"
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "STALE_STATE"
    assert forbidden.status_code == 403
    assert forbidden.json()["detail"]["code"] == "FORBIDDEN_ACTOR"


def test_agent_failure_returns_503_without_advancing_state(session_factory):
    """An unavailable PM Agent must leave the last durable state retryable."""
    agent = ScriptedAgentGateway(
        analyze_results=deque(
            [ClarificationAnalysis(ready_for_spec=True, questions=[], assumptions=[])]
        ),
        generate_results=deque([RuntimeError("PM unavailable")]),
    )
    app = create_app(agent_gateway=agent, session_factory=session_factory)
    payload = {
        "request_id": "http-agent-failure",
        "actor_id": "approver-1",
        "brief": make_complete_brief().model_dump(mode="json"),
    }
    with TestClient(app) as client:
        created = client.post("/sessions", json=payload).json()
        failed = client.post(
            f"/sessions/{created['session_id']}/commands",
            json={
                "command_id": "spec-agent-fails",
                "action": "create_spec",
                "expected_state_version": created["state_version"],
                "actor_id": "approver-1",
                "payload": {},
            },
        )
        state = client.get(f"/sessions/{created['session_id']}/state").json()

    assert failed.status_code == 503
    assert state["phase"] == "SPECIFICATION"
    assert state["state_version"] == created["state_version"]
    assert state["current_spec_version_id"] is None


def test_agent_failure_events_never_disclose_raw_stderr_secret_or_path(session_factory):
    """Public history must remain safe even though restricted AgentCall diagnostics are retained."""
    sentinel = "SECRET_TOKEN stderr at /private/tmp/agent-secret.log"
    agent = ScriptedAgentGateway(
        analyze_results=deque(
            [ClarificationAnalysis(ready_for_spec=True, questions=[], assumptions=[])]
        ),
        generate_results=deque([AgentExecutionError(sentinel)]),
    )
    app = create_app(agent_gateway=agent, session_factory=session_factory)
    payload = {
        "request_id": "http-public-event-redaction",
        "actor_id": "approver-1",
        "brief": make_complete_brief().model_dump(mode="json"),
    }
    with TestClient(app) as client:
        created = client.post("/sessions", json=payload).json()
        failed = client.post(
            f"/sessions/{created['session_id']}/commands",
            json={
                "command_id": "spec-secret-failure",
                "action": "create_spec",
                "expected_state_version": created["state_version"],
                "actor_id": "approver-1",
                "payload": {},
            },
        )
        events = client.get(f"/sessions/{created['session_id']}/events")

    assert failed.status_code == 503
    assert sentinel not in failed.text
    assert events.status_code == 200
    assert sentinel not in events.text
    assert "/private/tmp/agent-secret.log" not in events.text


def test_intake_failure_history_never_discloses_invalid_agent_output(session_factory):
    sentinel = "BAD_AGENT_JSON secret at /Users/private/source.json"
    agent = ScriptedAgentGateway(
        analyze_results=deque([AgentOutputError(sentinel)])
    )
    app = create_app(agent_gateway=agent, session_factory=session_factory)
    with TestClient(app) as client:
        failed = client.post(
            "/sessions",
            json={
                "request_id": "intake-public-event-redaction",
                "actor_id": "approver-1",
                "brief": make_complete_brief().model_dump(mode="json"),
            },
        )
        with session_factory() as db:
            project = db.query(Project).filter_by(
                creation_request_id="intake-public-event-redaction"
            ).one()
            session_id = project.session_id
        events = client.get(f"/sessions/{session_id}/events")

    assert failed.status_code == 422
    assert sentinel not in failed.text
    assert sentinel not in events.text
    assert "/Users/private/source.json" not in events.text


def test_invalid_brief_uses_stable_validation_error_body(session_factory):
    """Framework-shaped validation arrays would force clients to special-case one error source."""
    app = create_app(
        agent_gateway=ScriptedAgentGateway(), session_factory=session_factory
    )
    with TestClient(app) as client:
        response = client.post(
            "/sessions",
            json={
                "request_id": "invalid-brief",
                "actor_id": "approver-1",
                "brief": {"motivation": "only one field"},
            },
        )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "VALIDATION_ERROR"


def test_intake_agent_unavailability_is_503_and_same_request_can_retry(session_factory):
    """A transient intake failure must be visible while retaining one retryable Project."""
    agent = ScriptedAgentGateway(
        analyze_results=deque(
            [
                RuntimeError("PM unavailable"),
                ClarificationAnalysis(ready_for_spec=True, questions=[], assumptions=[]),
            ]
        )
    )
    app = create_app(agent_gateway=agent, session_factory=session_factory)
    payload = {
        "request_id": "http-intake-retry",
        "actor_id": "approver-1",
        "brief": make_complete_brief().model_dump(mode="json"),
    }
    with TestClient(app) as client:
        failed = client.post("/sessions", json=payload)
        recovered = client.post("/sessions", json=payload)

    assert failed.status_code == 503
    assert failed.json()["detail"]["code"] == "AGENT_UNAVAILABLE"
    assert recovered.status_code == 201
    assert recovered.json()["phase"] == "SPECIFICATION"
    assert recovered.json()["state_version"] == 1


def test_revise_command_preserves_no_change_as_one_immutable_version(session_factory):
    """A no-op revision must not manufacture a new immutable Spec version."""
    original = make_valid_spec()
    agent = ScriptedAgentGateway(
        analyze_results=deque(
            [ClarificationAnalysis(ready_for_spec=True, questions=[], assumptions=[])]
        ),
        generate_results=deque([original, original]),
        review_results=deque([make_passing_semantic_review()]),
    )
    app = create_app(agent_gateway=agent, session_factory=session_factory)
    payload = {
        "request_id": "http-revise-no-change",
        "actor_id": "approver-1",
        "brief": make_complete_brief().model_dump(mode="json"),
    }
    with TestClient(app) as client:
        state = client.post("/sessions", json=payload).json()
        created = client.post(
            f"/sessions/{state['session_id']}/commands",
            json={
                "command_id": "spec-original",
                "action": "create_spec",
                "expected_state_version": state["state_version"],
                "actor_id": "approver-1",
                "payload": {},
            },
        ).json()["state"]
        reworked = client.post(
            f"/sessions/{state['session_id']}/commands",
            json={
                "command_id": "human-rework",
                "action": "rework",
                "expected_state_version": created["state_version"],
                "actor_id": "approver-1",
                "message": "Clarify ownership.",
                "payload": {},
            },
        ).json()["state"]
        revised = client.post(
            f"/sessions/{state['session_id']}/commands",
            json={
                "command_id": "spec-no-change",
                "action": "revise",
                "expected_state_version": reworked["state_version"],
                "actor_id": "approver-1",
                "message": "Apply the ownership clarification.",
                "payload": {},
            },
        )
        versions = client.get(f"/sessions/{state['session_id']}/specs").json()

    assert revised.status_code == 200
    assert revised.json()["created_resource_ids"] == []
    assert revised.json()["state"]["current_spec_status"] == "REWORK"
    assert len(versions) == 1


def test_schema_invalid_agent_result_maps_to_422_not_unavailability(session_factory):
    """A contract-invalid Agent answer is actionable validation, not an outage."""
    agent = ScriptedAgentGateway(
        analyze_results=deque(
            [ClarificationAnalysis(ready_for_spec=True, questions=[], assumptions=[])]
        ),
        generate_results=deque([AgentOutputError("invalid structured output")]),
    )
    app = create_app(agent_gateway=agent, session_factory=session_factory)
    payload = {
        "request_id": "http-invalid-agent-output",
        "actor_id": "approver-1",
        "brief": make_complete_brief().model_dump(mode="json"),
    }
    with TestClient(app) as client:
        state = client.post("/sessions", json=payload).json()
        response = client.post(
            f"/sessions/{state['session_id']}/commands",
            json={
                "command_id": "invalid-spec-output",
                "action": "create_spec",
                "expected_state_version": state["state_version"],
                "actor_id": "approver-1",
                "payload": {},
            },
        )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_AGENT_RESULT"


def test_spec_clarification_projection_ignores_stale_intake_rounds_and_matches_commands(
    session_factory,
):
    """A high-numbered intake request must never hide the active current-Spec decision."""
    semantic_question = SemanticReview(
        verdict=ReviewVerdict.NEED_INFO,
        findings=[
            {
                "code": "NEEDS_HUMAN_DECISION",
                "severity": "BLOCKER",
                "spec_path": "/permissions_and_responsibilities",
                "message": "Who owns the final operational handoff?",
                "suggested_resolution": "Name the accountable owner.",
                "blocks_progress": True,
            }
        ],
    )
    agent = ScriptedAgentGateway(
        analyze_results=deque(
            [
                _blocking_analysis("INTAKE-Q1", "Which team uses it?"),
                _blocking_analysis("INTAKE-Q2", "Which region launches first?"),
                ClarificationAnalysis(ready_for_spec=True, questions=[], assumptions=[]),
                ClarificationAnalysis(ready_for_spec=True, questions=[], assumptions=[]),
            ]
        ),
        generate_results=deque([make_valid_spec()]),
        review_results=deque([semantic_question]),
    )
    app = create_app(agent_gateway=agent, session_factory=session_factory)
    payload = {
        "request_id": "http-spec-clarification",
        "actor_id": "approver-1",
        "brief": make_complete_brief().model_dump(mode="json"),
    }

    def command(client, state, command_id, action, message=None):
        body = {
            "command_id": command_id,
            "action": action,
            "expected_state_version": state["state_version"],
            "actor_id": "approver-1",
            "payload": {},
        }
        if message is not None:
            body["message"] = message
        response = client.post(f"/sessions/{state['session_id']}/commands", json=body)
        assert response.status_code == 200, response.text
        return response

    with TestClient(app) as client:
        state = client.post("/sessions", json=payload).json()
        first_answer = command(client, state, "answer-intake-1", "message", "Backend operators.")
        assert first_answer.json()["state"] == client.get(
            f"/sessions/{state['session_id']}/state"
        ).json()
        assert first_answer.json()["state"]["outstanding_questions"][0]["question_id"] == "INTAKE-Q2"
        assert command(client, state, "answer-intake-1", "message", "Backend operators.").json() == first_answer.json()

        state = first_answer.json()["state"]
        state = command(client, state, "answer-intake-2", "message", "Shanghai.").json()["state"]
        spec_response = command(client, state, "create-human-decision", "create_spec")
        spec_state = spec_response.json()["state"]
        assert spec_state == client.get(f"/sessions/{state['session_id']}/state").json()
        assert spec_state["outstanding_questions"][0]["question"] == "Who owns the final operational handoff?"
        assert spec_state["review_findings"] == semantic_question.model_dump(mode="json")["findings"]
        assert command(client, state, "create-human-decision", "create_spec").json() == spec_response.json()

        with session_factory() as db:
            project_id = spec_state["project_id"]
            stale = ClarificationRequest(
                id="stale-intake-round-999",
                project_id=project_id,
                spec_version_id=None,
                questions=[
                    _blocking_analysis("STALE", "This stale intake question must stay hidden.")
                    .questions[0]
                    .model_dump(mode="json")
                ],
                analysis_round=999,
                blocking=True,
            )
            db.add(stale)
            db.commit()

        projected = client.get(f"/sessions/{state['session_id']}/state").json()
        assert projected["outstanding_questions"][0]["question"] == "Who owns the final operational handoff?"
        answered = command(client, projected, "answer-spec-question", "message", "Operations Director.")
        continued = answered.json()["state"]
        assert continued["current_spec_status"] == "REWORK"
        assert continued == client.get(f"/sessions/{state['session_id']}/state").json()
        assert command(
            client, projected, "answer-spec-question", "message", "Operations Director."
        ).json() == answered.json()

    with session_factory() as db:
        spec_request = (
            db.query(ClarificationRequest)
            .filter_by(project_id=continued["project_id"], spec_version_id=continued["current_spec_version_id"])
            .one()
        )
        response = db.query(ClarificationResponse).filter_by(
            clarification_request_id=spec_request.id
        ).one()
        assert response.answers == {"message": "Operations Director."}
    last_analysis = [payload for operation, payload in agent.calls if operation == "analyze_brief"][-1]
    assert any(
        item["request_id"] == spec_request.id
        and item["answers"] == {"message": "Operations Director."}
        for item in last_analysis["clarification_history"]
    )


def test_retry_after_reviewer_transport_failure_reuses_command_bound_generation(
    session_factory,
):
    """Retrying one command must not regenerate a Spec after its PM result is durable."""
    agent = ScriptedAgentGateway(
        analyze_results=deque(
            [ClarificationAnalysis(ready_for_spec=True, questions=[], assumptions=[])]
        ),
        generate_results=deque([make_valid_spec()]),
        review_results=deque(
            [AgentExecutionError("reviewer unavailable"), make_passing_semantic_review()]
        ),
    )
    app = create_app(agent_gateway=agent, session_factory=session_factory)
    payload = {
        "request_id": "http-reviewer-retry",
        "actor_id": "approver-1",
        "brief": make_complete_brief().model_dump(mode="json"),
    }
    with TestClient(app) as client:
        state = client.post("/sessions", json=payload).json()
        command = {
            "command_id": "create-spec-review-retry",
            "action": "create_spec",
            "expected_state_version": state["state_version"],
            "actor_id": "approver-1",
            "payload": {},
        }
        failed = client.post(f"/sessions/{state['session_id']}/commands", json=command)
        recovered = client.post(f"/sessions/{state['session_id']}/commands", json=command)
        versions = client.get(f"/sessions/{state['session_id']}/specs").json()

    assert failed.status_code == 503
    assert recovered.status_code == 200
    assert recovered.json()["state"]["current_spec_status"] == "HUMAN_REVIEW"
    assert len(versions) == 1
    assert [operation for operation, _ in agent.calls] == [
        "analyze_brief",
        "generate_spec",
        "review_spec",
        "review_spec",
    ]


def test_intake_schema_invalid_output_is_422_after_durable_failure(session_factory):
    """Intake must not relabel schema-invalid Agent output as transport unavailability."""
    agent = ScriptedAgentGateway(
        analyze_results=deque([AgentOutputError("invalid clarification JSON")])
    )
    app = create_app(agent_gateway=agent, session_factory=session_factory)
    with TestClient(app) as client:
        response = client.post(
            "/sessions",
            json={
                "request_id": "intake-invalid-output",
                "actor_id": "approver-1",
                "brief": make_complete_brief().model_dump(mode="json"),
            },
        )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_AGENT_RESULT"
    with session_factory() as db:
        call = db.query(AgentCall).filter_by(operation="analyze_brief").one()
        assert call.status == "FAILED"
        assert call.response == {"error_code": "INVALID_AGENT_RESULT"}


def test_breakdown_reviewer_rejection_exposes_domain_code(session_factory):
    """A semantic rejection must identify the blocking decomposition rule to API callers."""
    rejection = SemanticReview(
        verdict=ReviewVerdict.REJECT,
        findings=[
            {
                "code": "SCOPE_CONTRADICTION",
                "severity": "BLOCKER",
                "spec_path": "/agent_specs/0/exclusions",
                "message": "The task contradicts the approved boundary.",
                "suggested_resolution": "Align the task with the approved exclusions.",
                "blocks_progress": True,
            }
        ],
    )

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
        analyze_results=deque(
            [ClarificationAnalysis(ready_for_spec=True, questions=[], assumptions=[])]
        ),
        generate_results=deque([make_valid_spec()]),
        review_results=deque([make_passing_semantic_review()]),
        decompose_results=deque([breakdown_for] * 3),
        review_breakdown_results=deque([rejection] * 3),
    )
    app = create_app(agent_gateway=agent, session_factory=session_factory)
    with TestClient(app) as client:
        approved = _approved_http_session(client, "breakdown-semantic-reject")
        session_id = approved["session_id"]
        command_body = {
            "command_id": "convert-semantic-reject",
            "action": "convert_to_work_item",
            "expected_state_version": approved["state_version"],
            "actor_id": "approver-1",
            "payload": {},
        }
        expected_error_code = "SEMANTIC_REVIEW_BLOCKED"
        submission = client.post(f"/sessions/{session_id}/commands", json=command_body)
        snapshot = client.get(submission.json()["status_url"])

    assert submission.status_code == 202
    assert snapshot.status_code == 200
    assert snapshot.json()["status"] == "failed"
    assert snapshot.json()["error"]["code"] == expected_error_code


def test_nested_decomposition_agent_output_error_is_durable(session_factory):
    """Nested decomposition wrappers must preserve schema-invalid output classification."""
    agent = ScriptedAgentGateway(
        analyze_results=deque(
            [ClarificationAnalysis(ready_for_spec=True, questions=[], assumptions=[])]
        ),
        generate_results=deque([make_valid_spec()]),
        review_results=deque([make_passing_semantic_review()]),
        decompose_results=deque([AgentOutputError("invalid breakdown JSON")]),
    )
    app = create_app(agent_gateway=agent, session_factory=session_factory)
    with TestClient(app) as client:
        approved = _approved_http_session(client, "breakdown-invalid-output")
        session_id = approved["session_id"]
        command_body = {
            "command_id": "convert-invalid-output",
            "action": "convert_to_work_item",
            "expected_state_version": approved["state_version"],
            "actor_id": "approver-1",
            "payload": {},
        }
        expected_error_code = "INVALID_AGENT_RESULT"
        submission = client.post(f"/sessions/{session_id}/commands", json=command_body)
        snapshot = client.get(submission.json()["status_url"])

    assert submission.status_code == 202
    assert snapshot.status_code == 200
    assert snapshot.json()["status"] == "failed"
    assert snapshot.json()["error"]["code"] == expected_error_code


def test_decomposition_local_key_error_preserves_stable_public_code(session_factory, monkeypatch):
    """Collapsing a direct decomposition validation error prevents HTTP callers from handling it."""

    secret = "secret detail at /private/decomposition-output.json"
    def breakdown_for(payload):
        breakdown = make_valid_breakdown()
        return breakdown.model_copy(
            update={
                "agent_specs": [
                    item.model_copy(update={"context_refs": list(payload["input_refs"])})
                    for item in breakdown.agent_specs
                ]
            }
        )

    agent = ScriptedAgentGateway(
        analyze_results=deque([ClarificationAnalysis(ready_for_spec=True, questions=[], assumptions=[])]),
        generate_results=deque([make_valid_spec()]),
        review_results=deque([make_passing_semantic_review()]),
        decompose_results=deque([breakdown_for]),
    )
    app = create_app(agent_gateway=agent, session_factory=session_factory)

    def local_key_failure(*_):
        raise BreakdownValidationError("LOCAL_KEY_EXISTS", "root", secret)

    monkeypatch.setattr(DecompositionService, "_materialize_command", local_key_failure)
    with TestClient(app) as client:
        approved = _approved_http_session(client, "breakdown-local-key-code")
        session_id = approved["session_id"]
        command_body = {
            "command_id": "convert-local-key-code",
            "action": "convert_to_work_item",
            "expected_state_version": approved["state_version"],
            "actor_id": "approver-1",
            "payload": {},
        }
        expected_error_code = "LOCAL_KEY_EXISTS"
        submission = client.post(f"/sessions/{session_id}/commands", json=command_body)
        snapshot = client.get(submission.json()["status_url"])

    assert submission.status_code == 202
    assert snapshot.status_code == 200
    assert snapshot.json()["status"] == "failed"
    assert snapshot.json()["error"]["code"] == expected_error_code
    assert secret not in submission.text
    assert secret not in snapshot.text


def test_spec_clarification_followups_stay_bound_until_ready(session_factory):
    """A still-blocked Spec answer must expose a new question on the same Spec, never intake."""
    first_question = SemanticReview(
        verdict=ReviewVerdict.NEED_INFO,
        findings=[
            {
                "code": "NEEDS_HUMAN_DECISION",
                "severity": "BLOCKER",
                "spec_path": "/permissions_and_responsibilities",
                "message": "Who owns the operational handoff?",
                "suggested_resolution": "Name the accountable owner.",
                "blocks_progress": True,
            }
        ],
    )
    agent = ScriptedAgentGateway(
        analyze_results=deque(
            [
                ClarificationAnalysis(ready_for_spec=True, questions=[], assumptions=[]),
                _blocking_analysis("SPEC-Q2", "Who is the backup owner?"),
                ClarificationAnalysis(ready_for_spec=True, questions=[], assumptions=[]),
            ]
        ),
        generate_results=deque([make_valid_spec()]),
        review_results=deque([first_question]),
    )
    app = create_app(agent_gateway=agent, session_factory=session_factory)
    with TestClient(app) as client:
        state = client.post(
            "/sessions",
            json={
                "request_id": "spec-followup-chain",
                "actor_id": "approver-1",
                "brief": make_complete_brief().model_dump(mode="json"),
            },
        ).json()
        first = client.post(
            f"/sessions/{state['session_id']}/commands",
            json={
                "command_id": "spec-followup-create",
                "action": "create_spec",
                "expected_state_version": state["state_version"],
                "actor_id": "approver-1",
                "payload": {},
            },
        ).json()["state"]
        spec_version_id = first["current_spec_version_id"]
        with session_factory() as db:
            db.add(
                ClarificationRequest(
                    id="stale-intake-followup",
                    project_id=first["project_id"],
                    spec_version_id=None,
                    questions=[
                        _blocking_analysis("STALE", "Stale intake question.")
                        .questions[0]
                        .model_dump(mode="json")
                    ],
                    analysis_round=999,
                    blocking=True,
                )
            )
            db.commit()

        assert client.get(f"/sessions/{state['session_id']}/state").json()[
            "outstanding_questions"
        ][0]["question"] == "Who owns the operational handoff?"
        second_response = client.post(
            f"/sessions/{state['session_id']}/commands",
            json={
                "command_id": "answer-spec-q1",
                "action": "message",
                "expected_state_version": first["state_version"],
                "actor_id": "approver-1",
                "message": "Operations Director.",
                "payload": {},
            },
        )
        assert second_response.status_code == 200, second_response.text
        second = second_response.json()["state"]
        assert second["outstanding_questions"][0]["question_id"] == "SPEC-Q2"
        assert second == client.get(f"/sessions/{state['session_id']}/state").json()
        final_response = client.post(
            f"/sessions/{state['session_id']}/commands",
            json={
                "command_id": "answer-spec-q2",
                "action": "message",
                "expected_state_version": second["state_version"],
                "actor_id": "approver-1",
                "message": "Platform Director.",
                "payload": {},
            },
        )
        assert final_response.status_code == 200, final_response.text
        final = final_response.json()["state"]

    assert final["phase"] == "REVIEW"
    assert final["current_spec_status"] == "REWORK"
    with session_factory() as db:
        spec_requests = (
            db.query(ClarificationRequest)
            .filter_by(project_id=final["project_id"], spec_version_id=spec_version_id)
            .order_by(ClarificationRequest.analysis_round)
            .all()
        )
        assert len(spec_requests) == 2
        assert spec_requests[0].analysis_round < spec_requests[1].analysis_round
        responses = {
            item.clarification_request_id: item.answers
            for item in db.query(ClarificationResponse)
            .filter_by(project_id=final["project_id"])
            .all()
        }
        assert responses == {
            spec_requests[0].id: {"message": "Operations Director."},
            spec_requests[1].id: {"message": "Platform Director."},
        }
