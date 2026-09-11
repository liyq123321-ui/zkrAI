from dataclasses import replace
from uuid import uuid4

import httpx
import pytest

from app.database.models import PrdPrototype, Project, SpecVersion, WorkItem
from app.services.weknora import WeKnoraClient, WeKnoraSession
from app.services.workspace_portal import WorkspacePortalService


class FakeWeKnora:
    base_url = "http://weknora.test/api/v1"
    web_url = "http://weknora.test"

    def __init__(self):
        self.tenants = []
        self.knowledge_bases = {}
        self.documents = {}
        self.uploads = []

    async def personal_session(self):
        return WeKnoraSession(
            token="personal-token",
            refresh_token="refresh-token",
            active_tenant={"id": 10000, "storage_used": 8, "storage_quota": 100},
            memberships=[],
        )

    async def switch_tenant(self, session, tenant_id):
        return WeKnoraSession(
            token=f"tenant-{tenant_id}",
            refresh_token=session.refresh_token,
            active_tenant={"id": tenant_id},
            memberships=[],
        )

    async def list_tenants(self, _session):
        return list(self.tenants)

    async def create_tenant(self, _token, *, name, description):
        tenant = {"id": 10001 + len(self.tenants), "name": name, "description": description}
        self.tenants.append(tenant)
        return tenant

    async def list_knowledge_bases(self, token):
        return list(self.knowledge_bases.get(token, []))

    async def create_knowledge_base(self, token, **kwargs):
        kb = {"id": str(uuid4()), **kwargs}
        self.knowledge_bases.setdefault(token, []).append(kb)
        return kb

    async def list_documents(self, token, knowledge_base_id):
        items = self.documents.get((token, knowledge_base_id), [])
        return {"items": list(items), "total": len(items)}

    async def upload_document(self, token, knowledge_base_id, *, filename, content, content_type):
        item = {
            "id": str(uuid4()), "title": filename, "file_type": content_type,
            "parse_status": "pending", "chunk_count": 0,
        }
        self.documents.setdefault((token, knowledge_base_id), []).append(item)
        self.uploads.append((filename, content))
        return item

    async def move_documents(self, token, knowledge_base_id, knowledge_ids, folder_path):
        for item in self.documents.get((token, knowledge_base_id), []):
            if item["id"] in knowledge_ids:
                item["folder_path"] = folder_path

    async def list_skills(self, _token):
        return [{"id": str(uuid4()), "name": "代码工程-单元测试生成", "description": "测试"}]

    async def list_skill_files(self, _token, _skill_id):
        return [{"path": "SKILL.md", "size": 12, "type": "file"}]

    async def list_models(self, _token):
        return [
            {"id": "embedding", "name": "bge-m3", "type": "Embedding"},
            {"id": "summary", "name": "qwen3-8b", "type": "KnowledgeQA"},
        ]

    async def clone_models(self, _token, source_models):
        return source_models


def seed_project(session_factory):
    project_id = str(uuid4())
    session_id = str(uuid4())
    spec_id = str(uuid4())
    root_id = str(uuid4())
    with session_factory() as db:
        db.add(Project(
            id=project_id,
            session_id=session_id,
            creation_request_id=str(uuid4()),
            brief={"motivation": "共享项目资料", "final_objective": "交付工作台"},
            final_approver="owner-1",
            project_manager_ids=[],
            root_owner_ids=["owner-1"],
            phase="REVIEW",
            state_version=1,
            current_spec_version_id=spec_id,
        ))
        db.add(WorkItem(
            id=root_id, project_id=project_id, session_id=session_id,
            local_key="root", kind="ROOT", title="项目工作台", status="in_progress",
        ))
        db.add(SpecVersion(
            id=spec_id, project_id=project_id, revision=2, content={},
            markdown="# 项目工作台", generation_source="agent", input_refs=[],
            generator_agent_session_id=str(uuid4()), generator_call_id=str(uuid4()),
            change_summary="初版", content_hash="a" * 64, status="APPROVED",
        ))
        db.add(PrdPrototype(
            id=str(uuid4()), project_id=project_id, spec_version_id=spec_id,
            generator_agent_session_id=str(uuid4()), generator_call_id=str(uuid4()),
            status="ready", title="工作台原型", html="<!doctype html><html></html>",
            content_hash="b" * 64,
        ))
        db.commit()
    return project_id


@pytest.mark.asyncio
async def test_overview_and_sync_project_assets(session_factory, tmp_path):
    project_id = seed_project(session_factory)
    remote = FakeWeKnora()
    personal_root = tmp_path / "personal"
    personal_file = personal_root / "owner-1" / "我的文档" / "新建文档.txt"
    personal_file.parent.mkdir(parents=True)
    personal_file.write_bytes(b"")
    service = WorkspacePortalService(session_factory, remote, personal_root)

    before = await service.overview("owner-1")
    assert before["connection"]["status"] == "online"
    assert [item["kind"] for item in before["spaces"]] == ["personal", "project"]
    assert before["spaces"][0]["files"][0]["folder_path"] == "我的文档"
    assert before["spaces"][0]["files"][0]["name"] == "新建文档.txt"
    assert before["spaces"][1]["project_id"] == project_id
    assert [item["status"] for item in before["spaces"][1]["assets"]] == [
        "ready", "ready", "reserved"
    ]

    after = await service.sync("owner-1")
    assert after["sync_result"] == {
        "created_spaces": 1,
        "uploaded_files": 3,
        "project_count": 1,
    }
    assert after["spaces"][1]["remote_status"] == "ready"
    assert {"README.md", "PRD-v2.md", "frontend-prototype-v2.html"} <= {
        name for name, _ in remote.uploads
    }
    repeated = await service.sync("owner-1")
    assert repeated["sync_result"] == {
        "created_spaces": 0, "uploaded_files": 0, "project_count": 1
    }
    assert len(remote.uploads) == 3
    assert {
        item.get("folder_path")
        for item in repeated["spaces"][1]["files"]
        if item["name"] != "README.md"
    } == {"docs", "prototype"}


@pytest.mark.asyncio
async def test_weknora_client_keeps_credentials_in_headers(test_settings):
    seen = {}

    async def handler(request):
        seen["url"] = str(request.url)
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={
            "token": "jwt", "refresh_token": "refresh",
            "active_tenant": {"id": 10000}, "memberships": [],
        })

    settings = replace(
        test_settings,
        weknora_base_url="http://weknora.test/api/v1",
        weknora_admin_email="admin@example.test",
        weknora_admin_password="secret-value",
        weknora_api_key="api-secret",
    )
    client = WeKnoraClient(settings, transport=httpx.MockTransport(handler))

    session = await client.login()
    assert session.token == "jwt"
    assert "secret-value" not in seen["url"]
    assert "api-secret" not in seen["url"]
    assert "secret-value" in seen["body"]
