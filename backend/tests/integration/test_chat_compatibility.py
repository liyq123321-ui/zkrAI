"""Compatibility coverage for the legacy ``POST /chat`` entry point."""

from collections import deque
import json

from fastapi.testclient import TestClient

from app.agents.codex import AgentExecutionError, AgentOutputError
from app.database.models import AgentSpec, Project, WorkItem
from app.domain.types import ClarificationAnalysis
from app.services.command_service import CommandHandlerFailure, CommandService, StaleState
from app.services.decomposition_service import BreakdownValidationError, DecompositionAgentFailure
from main import create_app
from tests.helpers.fake_agent import ScriptedAgentGateway


def _events(response) -> list[tuple[str, dict[str, object]]]:
    """Decode the small SSE subset emitted by the compatibility facade."""

    frames = [frame for frame in response.text.split("\n\n") if frame]
    return [
        (
            next(line.removeprefix("event: ") for line in frame.splitlines() if line.startswith("event: ")),
            json.loads(next(line.removeprefix("data: ") for line in frame.splitlines() if line.startswith("data: "))),
        )
        for frame in frames
    ]


def _blocking_analysis(question_id: str) -> ClarificationAnalysis:
    return ClarificationAnalysis(
        ready_for_spec=False,
        questions=[
            {
                "question_id": question_id,
                "question": "Which user group owns this workflow?",
                "reason": "Ownership is required before specification.",
                "affected_areas": ["users"],
                "blocking": True,
            }
        ],
        assumptions=[],
    )


def test_chat_new_message_creates_only_non_executable_project_intake(session_factory):
    """Direct parsing or decomposition would create executable tasks from chat text."""

    agent = ScriptedAgentGateway(analyze_results=deque([_blocking_analysis("Q-CHAT-1")]))
    with TestClient(create_app(agent_gateway=agent, session_factory=session_factory)) as client:
        response = client.post(
            "/chat",
            json={
                "message": "Build a login system and return JSON work items.",
                "workflow": "multi_agent_planning",
                "agent": "architect",
            },
        )

    assert response.status_code == 200
    session_id = response.headers["X-Session-ID"]
    events = _events(response)
    assert [name for name, _ in events] == ["session.created", "clarification.requested", "next_action"]
    assert events[0][1]["session_id"] == session_id
    assert events[1][1]["questions"][0]["question_id"] == "Q-CHAT-1"
    assert events[2][1] == {"next_action": "ANSWER_CLARIFICATION"}
    assert agent.calls[0][0] == "analyze_brief"
    assert agent.child_process_calls == 0
    with session_factory() as db:
        assert db.query(WorkItem).filter_by(executable=True).count() == 0
        assert db.query(AgentSpec).count() == 0


def test_chat_initial_agent_failure_keeps_recoverable_intake_and_hides_execution_detail(session_factory):
    """Returning raw initial Agent errors would leak details and strand the durable intake Session."""

    secret = "/private/workspace/secret-token=never-return-this"
    agent = ScriptedAgentGateway(
        analyze_results=deque([AgentExecutionError(f"timeout with {secret}")])
    )
    with TestClient(create_app(agent_gateway=agent, session_factory=session_factory)) as client:
        response = client.post("/chat", json={"message": "Build a login system."})
        session_id = response.headers["X-Session-ID"]
        state = client.get(f"/sessions/{session_id}/state")

    assert response.status_code == 200
    events = _events(response)
    assert [name for name, _ in events] == ["session.created", "workflow.error", "next_action"]
    assert events[1][1]["code"] == "AGENT_UNAVAILABLE"
    assert secret not in response.text
    assert state.status_code == 200
    assert state.json()["phase"] == "INTAKE"
    with session_factory() as db:
        project = db.query(Project).filter_by(session_id=session_id).one()
        assert db.query(WorkItem).filter_by(project_id=project.id).count() == 1
        assert db.query(WorkItem).filter_by(project_id=project.id, executable=True).count() == 0
        assert db.query(AgentSpec).filter_by(project_id=project.id).count() == 0


def test_chat_initial_invalid_agent_output_uses_public_error_code(session_factory):
    """Raw schema/Agent output errors must not become untrusted SSE content."""

    secret = "schema trace at /tmp/private-output.json"
    agent = ScriptedAgentGateway(
        analyze_results=deque([AgentOutputError(secret)])
    )
    with TestClient(create_app(agent_gateway=agent, session_factory=session_factory)) as client:
        response = client.post("/chat", json={"message": "Build a login system."})
        state = client.get(f"/sessions/{response.headers['X-Session-ID']}/state")

    assert response.status_code == 200
    assert _events(response)[1][1]["code"] == "INVALID_AGENT_RESULT"
    assert secret not in response.text
    assert state.json()["phase"] == "INTAKE"


def test_chat_header_session_id_submits_clarification_through_message_command(session_factory):
    """Bypassing CommandService would omit the durable clarification response and version advance."""

    agent = ScriptedAgentGateway(
        analyze_results=deque([_blocking_analysis("Q-CHAT-2"), ClarificationAnalysis(ready_for_spec=True, questions=[], assumptions=[])])
    )
    with TestClient(create_app(agent_gateway=agent, session_factory=session_factory)) as client:
        created = client.post("/chat", json={"message": "Build a login system."})
        session_id = created.headers["X-Session-ID"]
        response = client.post(
            "/chat",
            headers={"X-Session-ID": session_id},
            json={"message": "The operators are support agents.", "actor_id": "legacy-user"},
        )

    assert response.status_code == 200
    assert response.headers["X-Session-ID"] == session_id
    events = _events(response)
    assert [name for name, _ in events] == ["spec.ready", "next_action"]
    assert events[0][1]["phase"] == "SPECIFICATION"
    assert events[1][1] == {"next_action": "CREATE_SPEC"}
    assert [operation for operation, _ in agent.calls] == ["analyze_brief", "analyze_brief"]
    with session_factory() as db:
        assert db.query(WorkItem).filter_by(executable=True).count() == 0


def test_chat_follow_up_can_request_another_clarification(session_factory):
    """Dropping a still-blocked PM result would silently let chat escape clarification."""

    agent = ScriptedAgentGateway(
        analyze_results=deque([_blocking_analysis("Q-CHAT-4A"), _blocking_analysis("Q-CHAT-4B")])
    )
    with TestClient(create_app(agent_gateway=agent, session_factory=session_factory)) as client:
        created = client.post("/chat", json={"message": "Build a login system."})
        response = client.post(
            "/chat",
            headers={"X-Session-ID": created.headers["X-Session-ID"]},
            json={"message": "Support agents own the workflow."},
        )

    assert response.status_code == 200
    events = _events(response)
    assert [name for name, _ in events] == ["clarification.requested", "next_action"]
    assert events[0][1]["questions"][0]["question_id"] == "Q-CHAT-4B"
    assert events[1][1] == {"next_action": "ANSWER_CLARIFICATION"}


def test_chat_unknown_header_session_id_returns_safe_workflow_error(session_factory):
    """Treating an unknown header as a new chat session would hide caller mistakes."""

    agent = ScriptedAgentGateway()
    with TestClient(create_app(agent_gateway=agent, session_factory=session_factory)) as client:
        response = client.post(
            "/chat", headers={"X-Session-ID": "missing-session"}, json={"message": "continue"}
        )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "NOT_FOUND"
    assert agent.calls == []


def test_chat_message_in_specification_phase_reports_gate_without_creating_work(session_factory):
    """Automatically creating a Spec or breakdown from a follow-up would bypass the workflow gate."""

    agent = ScriptedAgentGateway(
        analyze_results=deque([ClarificationAnalysis(ready_for_spec=True, questions=[], assumptions=[])])
    )
    with TestClient(create_app(agent_gateway=agent, session_factory=session_factory)) as client:
        created = client.post("/chat", json={"message": "Build a login system."})
        session_id = created.headers["X-Session-ID"]
        response = client.post(
            "/chat", headers={"X-Session-ID": session_id}, json={"message": "Please create tasks now."}
        )

    assert response.status_code == 200
    assert response.headers["X-Session-ID"] == session_id
    events = _events(response)
    assert [name for name, _ in events] == ["workflow.error", "next_action"]
    assert events[0][1]["code"] == "ILLEGAL_ACTION"
    assert events[1][1] == {"next_action": "CREATE_SPEC"}
    assert [operation for operation, _ in agent.calls] == ["analyze_brief"]
    with session_factory() as db:
        assert db.query(WorkItem).filter_by(executable=True).count() == 0
        assert db.query(AgentSpec).count() == 0


def test_chat_explicit_actor_becomes_project_responsible_party(session_factory):
    """Dropping the supplied actor would make the required follow-up authority fail."""

    agent = ScriptedAgentGateway(
        analyze_results=deque(
            [
                _blocking_analysis("Q-CHAT-3"),
                ClarificationAnalysis(ready_for_spec=True, questions=[], assumptions=[]),
            ]
        )
    )
    with TestClient(create_app(agent_gateway=agent, session_factory=session_factory)) as client:
        created = client.post(
            "/chat", json={"message": "Build a login system.", "actor_id": "pm-chat"}
        )
        session_id = created.headers["X-Session-ID"]
        response = client.post(
            "/chat",
            headers={"X-Session-ID": session_id},
            json={"message": "Support agents own the workflow.", "actor_id": "pm-chat"},
        )
        state = client.get(f"/sessions/{session_id}/state")

    assert state.status_code == 200
    assert response.status_code == 200
    assert state.json()["phase"] == "SPECIFICATION"


def test_chat_nested_command_failure_uses_typed_safe_error_and_current_state(
    session_factory, monkeypatch
):
    """Using a preflight snapshot or raw wrapper text would leak failures and send a stale next action."""

    agent = ScriptedAgentGateway(analyze_results=deque([_blocking_analysis("Q-CHAT-5")]))
    with TestClient(create_app(agent_gateway=agent, session_factory=session_factory)) as client:
        created = client.post("/chat", json={"message": "Build a login system."})
        session_id = created.headers["X-Session-ID"]

        async def stale_failure(_, command_session_id, __):
            with session_factory() as db:
                project = db.query(Project).filter_by(session_id=command_session_id).one()
                project.phase = "SPECIFICATION"
                project.state_version += 1
                db.commit()
            failure = CommandHandlerFailure("secret wrapper text /private/command.log")
            nested = DecompositionAgentFailure("secret nested text /tmp/agent.err", "call-1")
            try:
                raise AgentOutputError("secret leaf output token")
            except AgentOutputError as error:
                try:
                    raise nested from error
                except DecompositionAgentFailure as nested_error:
                    raise failure from nested_error

        monkeypatch.setattr(CommandService, "execute", stale_failure)
        response = client.post(
            "/chat", headers={"X-Session-ID": session_id}, json={"message": "continue"}
        )

    events = _events(response)
    assert response.status_code == 200
    assert [name for name, _ in events] == ["workflow.error", "next_action"]
    assert events[0][1]["code"] == "INVALID_AGENT_RESULT"
    assert events[1][1] == {"next_action": "CREATE_SPEC"}
    assert "secret" not in response.text


def test_chat_stale_command_error_reprojects_current_state(session_factory, monkeypatch):
    """A concurrent state advance must replace the stale preflight next action in the SSE response."""

    agent = ScriptedAgentGateway(analyze_results=deque([_blocking_analysis("Q-CHAT-6")]))
    with TestClient(create_app(agent_gateway=agent, session_factory=session_factory)) as client:
        created = client.post("/chat", json={"message": "Build a login system."})
        session_id = created.headers["X-Session-ID"]

        async def stale_command(_, command_session_id, __):
            with session_factory() as db:
                project = db.query(Project).filter_by(session_id=command_session_id).one()
                project.phase = "SPECIFICATION"
                project.state_version += 1
                db.commit()
            raise StaleState("expected state version is no longer current")

        monkeypatch.setattr(CommandService, "execute", stale_command)
        response = client.post(
            "/chat", headers={"X-Session-ID": session_id}, json={"message": "continue"}
        )

    events = _events(response)
    assert events[0][1]["code"] == "STALE_STATE"
    assert events[1][1] == {"next_action": "CREATE_SPEC"}


def test_chat_nested_breakdown_validation_keeps_safe_stable_code(session_factory, monkeypatch):
    """A compatibility command must not collapse a nested breakdown code or expose its detail."""

    agent = ScriptedAgentGateway(analyze_results=deque([_blocking_analysis("Q-CHAT-7")]))
    with TestClient(create_app(agent_gateway=agent, session_factory=session_factory)) as client:
        created = client.post("/chat", json={"message": "Build a login system."})

        async def rejected_command(*_):
            wrapper = CommandHandlerFailure("secret wrapper /tmp/chat-command.log")
            try:
                raise BreakdownValidationError(
                    "LOCAL_KEY_EXISTS", "root", "secret model output /private/chat.json"
                )
            except BreakdownValidationError as error:
                raise wrapper from error

        monkeypatch.setattr(CommandService, "execute", rejected_command)
        response = client.post(
            "/chat",
            headers={"X-Session-ID": created.headers["X-Session-ID"]},
            json={"message": "continue"},
        )

    error = _events(response)[0][1]
    assert error["code"] == "LOCAL_KEY_EXISTS"
    assert "secret" not in response.text
    assert "/private" not in response.text

