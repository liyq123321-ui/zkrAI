"""Project/workspace projection backed by firstFlight and remote WeKnora."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from app.database.models import PrdPrototype, Project, SpecVersion, WorkItem
from app.services.weknora import WeKnoraClient, WeKnoraError, WeKnoraSession


PROJECT_MARKER = "firstFlight-project:"
PROJECT_KB_NAME = "firstFlight 项目资产"


def _plain(value: Any, fallback: str = "") -> str:
    return " ".join(str(value or fallback).split())


def _short_title(value: Any, fallback: str) -> str:
    title = _plain(value, fallback)
    for prefix in ("交付一个仅在本机运行的", "最终生成一个可以本地运行的"):
        if title.startswith(prefix):
            title = title[len(prefix):]
    first = re.split(r"[。！？.!?]", title, maxsplit=1)[0].strip()
    return (first or fallback)[:42]


def _tenant_project_id(tenant: dict[str, Any]) -> str | None:
    description = str(tenant.get("description") or "")
    match = re.search(rf"{re.escape(PROJECT_MARKER)}([0-9a-f-]{{36}})", description)
    return match.group(1) if match else None


def _document_name(item: dict[str, Any]) -> str:
    return _plain(item.get("title") or item.get("file_name") or item.get("name"), "未命名文件")


class WorkspacePortalService:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        weknora: WeKnoraClient,
    ) -> None:
        self._session_factory = session_factory
        self._weknora = weknora

    def _projects(self, actor_id: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        with self._session_factory() as db:
            projects = db.query(Project).order_by(Project.created_at, Project.id).all()
            roots = {
                item.project_id: item
                for item in db.query(WorkItem).filter_by(kind="ROOT").all()
            }
            for project in projects:
                owners = set(project.root_owner_ids or [])
                managers = set(project.project_manager_ids or [])
                if actor_id not in owners | managers | {project.final_approver}:
                    continue
                root = roots.get(project.id)
                spec = (
                    db.get(SpecVersion, project.current_spec_version_id)
                    if project.current_spec_version_id
                    else None
                )
                prototype = (
                    db.query(PrdPrototype)
                    .filter_by(spec_version_id=project.current_spec_version_id)
                    .one_or_none()
                    if project.current_spec_version_id
                    else None
                )
                title = _short_title(
                    root.title if root else None,
                    _plain((project.brief or {}).get("final_objective"), project.session_id),
                )
                assets: list[dict[str, Any]] = []
                if spec and root:
                    assets.append({
                        "kind": "prd",
                        "name": f"PRD-v{spec.revision}.md",
                        "folder": "docs",
                        "status": "ready",
                        "revision": spec.revision,
                        "source_url": f"/prd/{root.id}/v/{spec.revision}",
                    })
                if spec and prototype and prototype.status == "ready" and prototype.html and root:
                    assets.append({
                        "kind": "prototype",
                        "name": f"frontend-prototype-v{spec.revision}.html",
                        "folder": "prototype",
                        "status": "ready",
                        "revision": spec.revision,
                        "source_url": f"/prd/{root.id}/v/{spec.revision}/prototype",
                    })
                elif spec:
                    assets.append({
                        "kind": "prototype",
                        "name": f"frontend-prototype-v{spec.revision}.html",
                        "folder": "prototype",
                        "status": "not_generated",
                        "revision": spec.revision,
                        "source_url": None,
                    })
                assets.append({
                    "kind": "code",
                    "name": "code/",
                    "folder": "code",
                    "status": "reserved",
                    "revision": None,
                    "source_url": None,
                })
                rows.append({
                    "kind": "project",
                    "project_id": project.id,
                    "session_id": project.session_id,
                    "name": title,
                    "description": _plain(
                        (project.brief or {}).get("motivation"),
                        "firstFlight 共享项目工作空间",
                    )[:240],
                    "phase": project.phase,
                    "tenant_id": None,
                    "remote_status": "not_synced",
                    "knowledge_base_id": None,
                    "files": [],
                    "assets": assets,
                    "created_at": project.created_at.isoformat(),
                })
        return rows

    async def _remote_project_details(
        self,
        personal: WeKnoraSession,
        projects: list[dict[str, Any]],
        tenants: list[dict[str, Any]],
    ) -> None:
        by_project = {
            project_id: tenant
            for tenant in tenants
            if (project_id := _tenant_project_id(tenant))
        }
        last_session = personal
        for project in projects:
            tenant = by_project.get(str(project["project_id"]))
            if not tenant:
                continue
            tenant_id = int(tenant.get("id") or 0)
            project["tenant_id"] = tenant_id
            project["remote_status"] = "ready"
            try:
                scoped = await self._weknora.switch_tenant(personal, tenant_id)
                last_session = scoped
                bases = await self._weknora.list_knowledge_bases(scoped.token)
                kb = next((item for item in bases if item.get("name") == PROJECT_KB_NAME), None)
                if not kb:
                    project["remote_status"] = "workspace_only"
                    continue
                project["knowledge_base_id"] = kb.get("id")
                documents = await self._weknora.list_documents(scoped.token, str(kb["id"]))
                project["files"] = [self._document(item) for item in documents["items"]]
            except (WeKnoraError, ValueError):
                project["remote_status"] = "degraded"
        personal_tenant_id = int(personal.active_tenant.get("id") or 0)
        if personal_tenant_id and int(last_session.active_tenant.get("id") or 0) != personal_tenant_id:
            await self._weknora.switch_tenant(last_session, personal_tenant_id)

    @staticmethod
    def _knowledge_base(item: dict[str, Any], tenant_id: int) -> dict[str, Any]:
        return {
            "id": str(item.get("id") or ""),
            "tenant_id": tenant_id,
            "name": _plain(item.get("name"), "未命名知识库"),
            "description": _plain(item.get("description")),
            "knowledge_count": int(item.get("knowledge_count") or 0),
            "chunk_count": int(item.get("chunk_count") or 0),
            "processing_count": int(item.get("processing_count") or 0),
            "updated_at": item.get("updated_at"),
        }

    @staticmethod
    def _skill(item: dict[str, Any]) -> dict[str, Any]:
        name = _plain(item.get("name"), "未命名技能")
        domain, _, capability = name.partition("-")
        installations = item.get("installations") or []
        return {
            "id": str(item.get("id") or ""),
            "name": name,
            "domain": domain if capability else "其他",
            "capability": capability or name,
            "description": _plain(item.get("description")),
            "version": item.get("version"),
            "installed": bool(installations),
            "file_count": int(item.get("file_count") or 0),
        }

    @staticmethod
    def _document(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": str(item.get("id") or ""),
            "name": _document_name(item),
            "folder_path": _plain(item.get("folder_path")),
            "file_type": _plain(item.get("file_type") or item.get("type")),
            "parse_status": _plain(item.get("parse_status") or item.get("status")),
            "chunk_count": int(item.get("chunk_count") or 0),
            "updated_at": item.get("updated_at"),
        }

    async def overview(self, actor_id: str) -> dict[str, Any]:
        projects = self._projects(actor_id)
        base = {
            "actor_id": actor_id,
            "connection": {
                "status": "offline",
                "base_url": self._weknora.base_url,
                "web_url": self._weknora.web_url,
                "message": "WeKnora 尚未连接",
            },
            "spaces": projects,
            "knowledge_bases": [],
            "skills": [],
        }
        try:
            personal = await self._weknora.personal_session()
            tenants = await self._weknora.list_tenants(personal)
            tenant_id = int(personal.active_tenant.get("id") or 0)
            knowledge_bases = await self._weknora.list_knowledge_bases(personal.token)
            skills = await self._weknora.list_skills(personal.token)
            await self._remote_project_details(personal, projects, tenants)
            storage_used = int(personal.active_tenant.get("storage_used") or 0)
            storage_quota = int(personal.active_tenant.get("storage_quota") or 0)
            personal_space = {
                "kind": "personal",
                "project_id": None,
                "session_id": None,
                "name": f"{actor_id} 的个人空间",
                "description": "个人知识、技能目录与跨项目资料入口",
                "phase": "ACTIVE",
                "tenant_id": tenant_id,
                "remote_status": "ready",
                "knowledge_base_id": None,
                "files": [],
                "assets": [
                    {"kind": "knowledge", "name": f"{len(knowledge_bases)} 个知识库", "folder": "knowledge", "status": "ready", "revision": None, "source_url": None},
                    {"kind": "skill", "name": f"{len(skills)} 个技能", "folder": "skills", "status": "ready", "revision": None, "source_url": None},
                ],
                "storage_used": storage_used,
                "storage_quota": storage_quota,
                "created_at": personal.active_tenant.get("created_at"),
            }
            base.update({
                "connection": {
                    "status": "online",
                    "base_url": self._weknora.base_url,
                    "web_url": self._weknora.web_url,
                    "message": "远程 WeKnora 已连接",
                },
                "spaces": [personal_space, *projects],
                "knowledge_bases": [
                    self._knowledge_base(item, tenant_id) for item in knowledge_bases
                ],
                "skills": [self._skill(item) for item in skills],
            })
        except (WeKnoraError, ValueError) as error:
            base["connection"]["message"] = str(error)
        return base

    async def knowledge_documents(
        self, knowledge_base_id: str, tenant_id: int
    ) -> dict[str, Any]:
        personal = await self._weknora.personal_session()
        tenants = await self._weknora.list_tenants(personal)
        if tenant_id not in {int(item.get("id") or 0) for item in tenants}:
            raise ValueError("workspace is not accessible")
        scoped = await self._weknora.switch_tenant(personal, tenant_id)
        try:
            result = await self._weknora.list_documents(scoped.token, knowledge_base_id)
        finally:
            personal_tenant_id = int(personal.active_tenant.get("id") or 0)
            if personal_tenant_id != tenant_id:
                await self._weknora.switch_tenant(scoped, personal_tenant_id)
        return {
            "items": [self._document(item) for item in result["items"]],
            "total": result["total"],
        }

    async def skill_files(self, skill_id: str) -> list[dict[str, Any]]:
        personal = await self._weknora.personal_session()
        files = await self._weknora.list_skill_files(personal.token, skill_id)
        return [{
            "path": _plain(item.get("path") or item.get("name"), "未命名文件"),
            "size": int(item.get("size") or 0),
            "type": _plain(item.get("type"), "file"),
        } for item in files]

    async def sync(self, actor_id: str) -> dict[str, Any]:
        projects = self._projects(actor_id)
        personal = await self._weknora.personal_session()
        tenants = await self._weknora.list_tenants(personal)
        tenant_by_project = {
            project_id: tenant
            for tenant in tenants
            if (project_id := _tenant_project_id(tenant))
        }
        source_models = await self._weknora.list_models(personal.token)
        created_spaces = 0
        uploaded_files = 0

        for project in projects:
            project_id = str(project["project_id"])
            tenant = tenant_by_project.get(project_id)
            if tenant is None:
                created = await self._weknora.create_tenant(
                    personal.token,
                    name=f"{actor_id} · {project['name']}",
                    description=(
                        f"{PROJECT_MARKER}{project_id}; owner:{actor_id}; "
                        "由 firstFlight 管理的共享项目空间"
                    ),
                )
                tenant = dict(created.get("tenant") or created)
                if not tenant.get("id"):
                    raise WeKnoraError("WeKnora created a workspace without an id")
                tenant_by_project[project_id] = tenant
                created_spaces += 1

            scoped = await self._weknora.switch_tenant(personal, int(tenant["id"]))
            models = await self._weknora.clone_models(scoped.token, source_models)
            embedding = next((item for item in models if item.get("type") == "Embedding"), None)
            summary = next((item for item in models if item.get("type") == "KnowledgeQA"), None)
            bases = await self._weknora.list_knowledge_bases(scoped.token)
            kb = next((item for item in bases if item.get("name") == PROJECT_KB_NAME), None)
            if kb is None:
                kb = await self._weknora.create_knowledge_base(
                    scoped.token,
                    name=PROJECT_KB_NAME,
                    description="PRD、前端原型与后续代码产物的项目目录",
                    embedding_model_id=str(embedding.get("id")) if embedding else None,
                    summary_model_id=str(summary.get("id")) if summary else None,
                )
            kb_id = str(kb.get("id") or "")
            existing = await self._weknora.list_documents(scoped.token, kb_id)
            existing_by_name = {
                _document_name(item): item for item in existing["items"]
            }
            existing_names = set(existing_by_name)

            readme_name = "README.md"
            if readme_name not in existing_names:
                readme = (
                    f"# {project['name']}\n\n"
                    f"- firstFlight project: `{project_id}`\n"
                    f"- session: `{project['session_id']}`\n"
                    f"- owner: `{actor_id}`\n\n"
                    "目录约定：`docs/` 保存 PRD，`prototype/` 保存 HTML 原型，"
                    "`code/` 预留给后续生成的项目代码。\n"
                )
                uploaded = await self._weknora.upload_document(
                    scoped.token, kb_id, filename=readme_name,
                    content=readme.encode("utf-8"), content_type="text/markdown",
                )
                if uploaded:
                    uploaded_files += 1

            with self._session_factory() as db:
                db_project = db.get(Project, project_id)
                spec = db.get(SpecVersion, db_project.current_spec_version_id) if db_project and db_project.current_spec_version_id else None
                prototype = (
                    db.query(PrdPrototype).filter_by(spec_version_id=spec.id).one_or_none()
                    if spec else None
                )
                payloads: list[tuple[str, str, bytes, str]] = []
                if spec:
                    payloads.append((f"PRD-v{spec.revision}.md", "docs", spec.markdown.encode("utf-8"), "text/markdown"))
                if spec and prototype and prototype.status == "ready" and prototype.html:
                    payloads.append((f"frontend-prototype-v{spec.revision}.html", "prototype", prototype.html.encode("utf-8"), "text/html"))

            for filename, folder, content, content_type in payloads:
                if filename in existing_names:
                    current = existing_by_name[filename]
                    if _plain(current.get("folder_path")).strip("/") != folder:
                        knowledge_id = str(current.get("id") or "")
                        if knowledge_id:
                            await self._weknora.move_documents(
                                scoped.token, kb_id, [knowledge_id], folder
                            )
                    continue
                uploaded = await self._weknora.upload_document(
                    scoped.token, kb_id, filename=filename,
                    content=content, content_type=content_type,
                )
                if not uploaded:
                    continue
                uploaded_files += 1
                knowledge_id = str(uploaded.get("id") or "")
                if knowledge_id:
                    await self._weknora.move_documents(
                        scoped.token, kb_id, [knowledge_id], folder
                    )

        overview = await self.overview(actor_id)
        overview["sync_result"] = {
            "created_spaces": created_spaces,
            "uploaded_files": uploaded_files,
            "project_count": len(projects),
        }
        return overview
