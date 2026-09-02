import sys
from collections import deque
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database.database import Base, create_engine_for_url, make_session_factory
from app.config import Settings
from app.domain.types import ClarificationAnalysis
from main import create_app
from tests.helpers.factories import (
    make_complete_brief,
    make_passing_semantic_review,
    make_valid_breakdown,
    make_valid_spec,
)
from tests.helpers.fake_agent import ScriptedAgentGateway


@pytest.fixture
def engine():
    value = create_engine_for_url("sqlite://")
    Base.metadata.create_all(value)
    yield value
    Base.metadata.drop_all(value)


@pytest.fixture
def session_factory(engine):
    return make_session_factory(engine)


@pytest.fixture
def db_session(session_factory):
    with session_factory() as session:
        yield session


@pytest.fixture
def complete_brief():
    return make_complete_brief()


@pytest.fixture
def valid_spec():
    return make_valid_spec()


@pytest.fixture
def passing_semantic_review():
    return make_passing_semantic_review()


@pytest.fixture
def valid_breakdown():
    return make_valid_breakdown()


@pytest.fixture
def test_settings(tmp_path):
    return Settings(
        database_url="sqlite://",
        codex_binary="codex",
        codex_home=tmp_path / "codex-home",
        codex_cwd=tmp_path,
        codex_timeout_seconds=1,
    )


@pytest.fixture
def session_create_payload():
    return {
        "request_id": "fixture-session-create",
        "actor_id": "approver-1",
        "brief": make_complete_brief().model_dump(mode="json"),
    }


@pytest.fixture
def client(session_factory, test_settings):
    agent = ScriptedAgentGateway(
        analyze_results=deque(
            [ClarificationAnalysis(ready_for_spec=True, questions=[], assumptions=[])]
        )
    )
    app = create_app(test_settings, agent, session_factory)
    with TestClient(app) as value:
        yield value


@pytest.fixture
def clarification_session(session_factory, test_settings):
    agent = ScriptedAgentGateway(
        analyze_results=deque(
            [
                ClarificationAnalysis(
                    ready_for_spec=False,
                    questions=[
                        {
                            "question_id": "Q-FIXTURE",
                            "question": "Who operates the workflow?",
                            "reason": "The user is required.",
                            "affected_areas": ["users"],
                            "blocking": True,
                        }
                    ],
                    assumptions=[],
                )
            ]
        )
    )
    app = create_app(test_settings, agent, session_factory)
    with TestClient(app) as value:
        response = value.post(
            "/sessions",
            json={
                "request_id": "fixture-clarification-session",
                "actor_id": "approver-1",
                "brief": make_complete_brief().model_dump(mode="json"),
            },
        )
    assert response.status_code == 201
    return response.json()

