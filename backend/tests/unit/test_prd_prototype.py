from collections import deque

import pytest
from pydantic import ValidationError

from app.database.models import AgentCall, PrdPrototype, Project, SpecVersion
from app.domain.types import HtmlPrototypePayload, ProjectPhase, SpecStatus
from app.services.prd_prototype import PrdPrototypeService


VALID_HTML = """<!doctype html>
<html lang="zh-CN"><head><style>body{font-family:sans-serif}</style></head>
<body><button id="next">下一页</button><script>document.querySelector('#next').onclick=()=>{}</script></body></html>
"""


class PrototypeAgent:
    prototype_enabled = True

    def __init__(self, results):
        self.results = deque(results)
        self.calls = []

    async def generate_prd_prototype(self, payload):
        self.calls.append(payload)
        result = self.results.popleft()
        if isinstance(result, Exception):
            raise result
        return result


def _project_with_spec(db_session):
    project = Project(
        id="prototype-project",
        session_id="prototype-session",
        creation_request_id="prototype-request",
        brief={"title": "Prototype"},
        final_approver="owner-1",
        project_manager_ids=["owner-1"],
        root_owner_ids=["owner-1"],
        phase=ProjectPhase.REVIEW.value,
        state_version=2,
        current_spec_version_id="prototype-spec-v1",
    )
    spec = SpecVersion(
        id="prototype-spec-v1",
        project_id=project.id,
        revision=1,
        content={"background_and_goals": ["Build an interactive form"]},
        markdown="# Project Spec\n\nBuild an interactive form.\n",
        generation_source="PM_AGENT",
        input_refs=["artifact:brief-1"],
        generator_agent_session_id="pm-session",
        generator_call_id="pm-call",
        parent_version_id=None,
        change_summary="Initial specification",
        content_hash="a" * 64,
        status=SpecStatus.HUMAN_REVIEW.value,
    )
    db_session.add_all([project, spec])
    db_session.commit()
    return project, spec


def test_html_prototype_requires_a_self_contained_document():
    with pytest.raises(ValidationError):
        HtmlPrototypePayload(
            title="Unsafe",
            html='<html><body><script src="https://cdn.example/app.js"></script></body></html>',
            generation_summary="External dependency",
        )
    with pytest.raises(ValidationError):
        HtmlPrototypePayload(
            title="Unsafe",
            html="<html><body><img src=https://cdn.example/image.png></body></html>",
            generation_summary="External dependency",
        )


@pytest.mark.asyncio
async def test_prototype_service_generates_once_from_the_frozen_prd(
    session_factory, db_session
):
    project, spec = _project_with_spec(db_session)
    output = HtmlPrototypePayload(
        title="Interactive form prototype",
        html=VALID_HTML,
        generation_summary="Generated the primary form flow.",
    )
    agent = PrototypeAgent([output])
    service = PrdPrototypeService(session_factory, agent)

    first = await service.ensure_for_project(project.id)
    second = await service.ensure_for_project(project.id)

    assert first is not None and first.status == "ready"
    assert second is not None and second.id == first.id
    assert len(agent.calls) == 1
    assert agent.calls[0]["spec_version_id"] == spec.id
    assert agent.calls[0]["prd_markdown"] == spec.markdown
    with session_factory() as db:
        stored = db.query(PrdPrototype).filter_by(spec_version_id=spec.id).one()
        call = db.get(AgentCall, stored.generator_call_id)
        assert stored.html == VALID_HTML
        assert call.status == "SUCCEEDED"


@pytest.mark.asyncio
async def test_prototype_failure_is_durable_but_does_not_raise(
    session_factory, db_session
):
    project, spec = _project_with_spec(db_session)
    agent = PrototypeAgent([RuntimeError("secret upstream detail")])

    result = await PrdPrototypeService(session_factory, agent).ensure_for_project(project.id)

    assert result is not None and result.status == "failed"
    assert result.error_code == "PROTOTYPE_GENERATION_FAILED"
    assert result.error_message == "HTML 原型生成失败，可在生成下一版 PRD 后重试。"
    assert "secret" not in result.error_message
    with session_factory() as db:
        stored = db.query(PrdPrototype).filter_by(spec_version_id=spec.id).one()
        assert db.get(AgentCall, stored.generator_call_id).status == "FAILED"
