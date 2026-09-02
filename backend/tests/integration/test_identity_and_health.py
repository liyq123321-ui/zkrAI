"""HTTP identity injection, health, and browser transport contracts."""

from collections import deque
from dataclasses import replace

from fastapi.testclient import TestClient

from app.database.models import WorkItem
from app.domain.types import ClarificationAnalysis, WorkItemKind
from main import create_app
from tests.helpers.factories import make_complete_brief
from tests.helpers.fake_agent import ScriptedAgentGateway


def _agent() -> ScriptedAgentGateway:
    return ScriptedAgentGateway(
        analyze_results=deque(
            [ClarificationAnalysis(ready_for_spec=True, questions=[], assumptions=[])]
        )
    )


def _payload(request_id: str, actor_id: str | None = None) -> dict[str, object]:
    value: dict[str, object] = {
        "request_id": request_id,
        "brief": make_complete_brief().model_dump(mode="json"),
    }
    if actor_id is not None:
        value["actor_id"] = actor_id
    return value


def test_local_identity_is_injected_and_overrides_browser_actor(
    session_factory, test_settings
):
    settings = replace(
        test_settings,
        identity_mode="local",
        identity_actor_id="owner-1",
    )
    app = create_app(settings, _agent(), session_factory)

    with TestClient(app) as client:
        response = client.post(
            "/sessions",
            json=_payload("local-identity-1", actor_id="attacker"),
        )

    assert response.status_code == 201
    with session_factory() as db:
        root = db.query(WorkItem).filter_by(kind=WorkItemKind.ROOT.value).one()
        assert root.suggested_assignee == "owner-1"


def test_proxy_identity_requires_and_uses_trusted_header(session_factory, test_settings):
    settings = replace(test_settings, identity_mode="proxy")
    app = create_app(settings, _agent(), session_factory)

    with TestClient(app) as client:
        missing = client.post("/sessions", json=_payload("proxy-identity-missing"))
        accepted = client.post(
            "/sessions",
            headers={"X-FirstFlight-Actor": "pm-1"},
            json=_payload("proxy-identity-accepted", actor_id="attacker"),
        )

    assert missing.status_code == 401
    assert missing.json()["detail"]["code"] == "IDENTITY_REQUIRED"
    assert accepted.status_code == 201
    with session_factory() as db:
        root = db.query(WorkItem).filter_by(kind=WorkItemKind.ROOT.value).one()
        assert root.suggested_assignee == "pm-1"


def test_healthz_checks_application_and_database(session_factory, test_settings):
    app = create_app(test_settings, _agent(), session_factory)

    with TestClient(app) as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_configured_cors_origin_is_allowed(session_factory, test_settings):
    settings = replace(
        test_settings,
        cors_allowed_origins=("http://127.0.0.1:3000",),
    )
    app = create_app(settings, _agent(), session_factory)

    with TestClient(app) as client:
        response = client.options(
            "/healthz",
            headers={
                "Origin": "http://127.0.0.1:3000",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:3000"
