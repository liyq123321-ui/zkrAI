from datetime import UTC, datetime

import pytest

from app.config import Settings
from app.database.models import AgentSession, AuditEvent, Project
from app.services.agent_backends import AgentBackendService


def _settings(tmp_path, **updates):
    values = dict(
        database_url="sqlite://",
        codex_binary="codex",
        codex_home=tmp_path / "codex-home",
        codex_cwd=tmp_path,
    )
    values.update(updates)
    return Settings(**values)


def _seed(session_factory):
    now = datetime.now(UTC)
    with session_factory() as db:
        db.add(Project(
            id="project-1",
            session_id="session-1",
            creation_request_id="create-1",
            brief={},
            final_approver="owner-1",
            project_manager_ids=["owner-1"],
            root_owner_ids=["owner-1"],
            phase="INTAKE",
            state_version=0,
            created_at=now,
            updated_at=now,
        ))
        db.add(AgentSession(
            id="pm-1",
            project_id="project-1",
            role="PM",
            provider="codex",
            purpose="test",
            metadata_json={},
            created_at=now,
            updated_at=now,
        ))
        db.commit()


def test_catalogue_reports_configured_models_without_exposing_keys(
    session_factory, tmp_path
):
    service = AgentBackendService(
        session_factory,
        _settings(tmp_path, deepseek_api_key="server-secret"),
    )

    payload = [item.model_dump(mode="json") for item in service.catalogue()]

    deepseek = next(item for item in payload if item["provider"] == "deepseek")
    assert deepseek["available"] is True
    assert deepseek["model"] == "deepseek-v4-pro"
    assert "server-secret" not in str(payload)


def test_switch_updates_project_agents_and_writes_audit(session_factory, tmp_path):
    _seed(session_factory)
    service = AgentBackendService(
        session_factory,
        _settings(
            tmp_path,
            moonshot_api_key="server-secret",
            moonshot_model="kimi-k3",
        ),
    )

    selected = service.select("session-1", "kimi", "owner-1")

    assert selected.provider == "kimi"
    assert selected.model == "kimi-k3"
    with session_factory() as db:
        pm = db.get(AgentSession, "pm-1")
        assert (pm.provider, pm.model) == ("kimi", "kimi-k3")
        audit = db.query(AuditEvent).filter_by(
            event_type="AGENT_BACKEND_SELECTED"
        ).one()
        assert audit.payload["provider"] == "kimi"


def test_unconfigured_remote_backend_cannot_be_selected(session_factory, tmp_path):
    _seed(session_factory)
    service = AgentBackendService(session_factory, _settings(tmp_path))

    with pytest.raises(ValueError, match="MOONSHOT_API_KEY"):
        service.select("session-1", "kimi", "owner-1")
