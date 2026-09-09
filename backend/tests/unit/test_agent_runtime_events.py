import asyncio

from app.agents.codex import CodexAgentGateway
from app.database.models import AgentCall, AgentRuntimeEvent, AgentSession, Project
from app.domain.types import ClarificationAnalysis
from app.services.agent_runtime_events import (
    AgentRuntimeEventStore,
    current_agent_runtime_context,
)
from app.services.query_service import QueryService


def _seed_call(session_factory):
    with session_factory() as db:
        db.add_all(
            [
                Project(
                    id="runtime-project",
                    session_id="runtime-session",
                    creation_request_id="runtime-request",
                    brief={"final_objective": "Record Codex runtime"},
                    final_approver="owner-1",
                    project_manager_ids=["owner-1"],
                    root_owner_ids=["owner-1"],
                ),
                AgentSession(
                    id="runtime-agent",
                    project_id="runtime-project",
                    role="PM",
                ),
                AgentCall(
                    id="runtime-call",
                    project_id="runtime-project",
                    agent_session_id="runtime-agent",
                    operation="analyze_brief",
                    request={"input": "brief"},
                    status="PENDING",
                ),
            ]
        )
        db.commit()


def test_runtime_event_store_resolves_call_and_persists_sanitized_event(session_factory):
    _seed_call(session_factory)
    store = AgentRuntimeEventStore(session_factory)

    context = store.resolve("analyze_brief", {"input": "brief"})
    assert context is not None
    assert context.agent_call_id == "runtime-call"

    store.append(
        context,
        {
            "type": "item.completed",
            "authorization": "Bearer top-secret-token",
            "item": {
                "type": "reasoning",
                "text": "Check API_KEY=top-secret and summarize the requirement.",
            },
        },
    )

    with session_factory() as db:
        persisted = db.query(AgentRuntimeEvent).one()
        assert persisted.title == "推理摘要"
        assert "top-secret" not in persisted.detail
        assert persisted.payload["authorization"] == "[REDACTED]"

    timeline = QueryService(session_factory).agent_runtime_events(
        "runtime-session", "runtime-agent"
    )
    assert len(timeline) == 1
    assert timeline[0].agent_call_id == "runtime-call"
    assert timeline[0].operation == "analyze_brief"
    assert timeline[0].item_type == "reasoning"
    assert "[REDACTED]" in (timeline[0].detail or "")


def test_runtime_event_store_does_not_guess_when_request_does_not_match(session_factory):
    _seed_call(session_factory)

    context = AgentRuntimeEventStore(session_factory).resolve(
        "analyze_brief", {"input": "different"}
    )

    assert context is None


def test_gateway_binds_the_matching_agent_call_while_runner_is_active(
    session_factory, test_settings
):
    _seed_call(session_factory)

    class ObservingRunner:
        context = None

        async def run(self, _prompt, output_type, _cwd, **_kwargs):
            self.context = current_agent_runtime_context()
            return output_type(ready_for_spec=True, questions=[], assumptions=[])

    runner = ObservingRunner()
    gateway = CodexAgentGateway(
        runner=runner,
        settings=test_settings,
        session_factory=session_factory,
    )

    result = asyncio.run(gateway.analyze_brief({"input": "brief"}))

    assert result == ClarificationAnalysis(
        ready_for_spec=True, questions=[], assumptions=[]
    )
    assert runner.context is not None
    assert runner.context.agent_call_id == "runtime-call"
    assert runner.context.agent_session_id == "runtime-agent"
