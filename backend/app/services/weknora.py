"""Small server-side client for the shared WeKnora deployment.

Browser callers never receive the API key, administrator password, or JWTs.
Only viewer-safe catalogue data is projected through firstFlight's API.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx

from app.config import Settings


class WeKnoraError(RuntimeError):
    """A public-safe failure raised by the remote WeKnora client."""


@dataclass(frozen=True)
class WeKnoraSession:
    token: str
    refresh_token: str
    active_tenant: dict[str, Any]
    memberships: list[dict[str, Any]]


def _uuid(value: str, label: str) -> str:
    try:
        return str(UUID(value))
    except (ValueError, TypeError) as error:
        raise ValueError(f"invalid {label}") from error


class WeKnoraClient:
    """Typed boundary around the subset of WeKnora used by the portal."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = settings.weknora_base_url
        self.web_url = settings.weknora_web_url
        self.api_key = settings.weknora_api_key
        self.admin_email = settings.weknora_admin_email
        self.admin_password = settings.weknora_admin_password
        self.personal_tenant_id = settings.weknora_personal_tenant_id
        self.timeout = settings.weknora_timeout_seconds
        self._transport = transport

    @property
    def configured(self) -> bool:
        return bool(self.admin_email and self.admin_password)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        use_api_key: bool = False,
        json: dict[str, Any] | None = None,
        files: dict[str, tuple[str, bytes, str]] | None = None,
        data: dict[str, str] | None = None,
        expected: tuple[int, ...] = (200,),
    ) -> dict[str, Any]:
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        elif use_api_key and self.api_key:
            headers["X-API-Key"] = self.api_key
        async with httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout,
            transport=self._transport,
        ) as client:
            try:
                response = await client.request(
                    method, path, headers=headers, json=json, files=files, data=data
                )
            except httpx.HTTPError as error:
                raise WeKnoraError("WeKnora connection failed") from error
        if response.status_code not in expected:
            raise WeKnoraError(
                f"WeKnora {method.upper()} {path} returned HTTP {response.status_code}"
            )
        if not response.content:
            return {}
        try:
            value = response.json()
        except ValueError as error:
            raise WeKnoraError("WeKnora returned an invalid JSON response") from error
        return value if isinstance(value, dict) else {"data": value}

    async def login(self) -> WeKnoraSession:
        if not self.configured:
            raise WeKnoraError("WeKnora administrator credentials are not configured")
        payload = await self._request(
            "POST",
            "/auth/login",
            json={"email": self.admin_email, "password": self.admin_password},
        )
        token = str(payload.get("token") or "")
        if not token:
            raise WeKnoraError("WeKnora login did not return an access token")
        return WeKnoraSession(
            token=token,
            refresh_token=str(payload.get("refresh_token") or ""),
            active_tenant=dict(payload.get("active_tenant") or {}),
            memberships=[
                dict(item) for item in payload.get("memberships") or []
                if isinstance(item, dict)
            ],
        )

    async def switch_tenant(
        self, session: WeKnoraSession, tenant_id: int
    ) -> WeKnoraSession:
        if int(session.active_tenant.get("id") or 0) == tenant_id:
            return session
        payload = await self._request(
            "POST",
            "/auth/switch-tenant",
            token=session.token,
            json={"tenant_id": tenant_id, "refresh_token": session.refresh_token},
        )
        token = str(payload.get("token") or "")
        if not token:
            raise WeKnoraError("WeKnora workspace switch returned no access token")
        return WeKnoraSession(
            token=token,
            refresh_token=str(payload.get("refresh_token") or session.refresh_token),
            active_tenant=dict(payload.get("active_tenant") or {}),
            memberships=[
                dict(item) for item in payload.get("memberships") or session.memberships
                if isinstance(item, dict)
            ],
        )

    async def personal_session(self) -> WeKnoraSession:
        session = await self.login()
        tenant_id = self.personal_tenant_id or int(
            session.active_tenant.get("id") or 0
        )
        if not tenant_id:
            raise WeKnoraError("WeKnora personal workspace is unavailable")
        return await self.switch_tenant(session, tenant_id)

    async def list_tenants(self, session: WeKnoraSession) -> list[dict[str, Any]]:
        """Resolve every membership to its viewer-safe workspace profile.

        WeKnora's ``GET /tenants`` intentionally returns only the active
        workspace. Login memberships are the authoritative accessible list;
        switching supplies each full profile, including our integration marker.
        """
        memberships = session.memberships or [{
            "tenant_id": session.active_tenant.get("id"),
            "tenant_name": session.active_tenant.get("name"),
        }]
        tenants: list[dict[str, Any]] = []
        last_session = session
        for membership in memberships:
            tenant_id = int(membership.get("tenant_id") or 0)
            if not tenant_id:
                continue
            scoped = await self.switch_tenant(session, tenant_id)
            last_session = scoped
            tenant = dict(scoped.active_tenant)
            tenant.setdefault("id", tenant_id)
            tenant.setdefault("name", membership.get("tenant_name"))
            tenants.append(tenant)
        original_tenant_id = int(session.active_tenant.get("id") or 0)
        if original_tenant_id and int(last_session.active_tenant.get("id") or 0) != original_tenant_id:
            await self.switch_tenant(last_session, original_tenant_id)
        return tenants

    async def create_tenant(
        self, token: str, *, name: str, description: str
    ) -> dict[str, Any]:
        payload = await self._request(
            "POST",
            "/tenants",
            token=token,
            json={"name": name[:128], "description": description[:512]},
            expected=(200, 201),
        )
        data = payload.get("data") or payload
        return dict(data) if isinstance(data, dict) else {}

    async def list_knowledge_bases(self, token: str) -> list[dict[str, Any]]:
        payload = await self._request("GET", "/knowledge-bases", token=token)
        data = payload.get("data") or []
        if isinstance(data, dict):
            data = data.get("items") or data.get("knowledge_bases") or []
        return [dict(item) for item in data if isinstance(item, dict)]

    async def create_knowledge_base(
        self,
        token: str,
        *,
        name: str,
        description: str,
        embedding_model_id: str | None = None,
        summary_model_id: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "name": name,
            "description": description,
            "type": "document",
        }
        if embedding_model_id:
            body["embedding_model_id"] = embedding_model_id
        if summary_model_id:
            body["summary_model_id"] = summary_model_id
        payload = await self._request(
            "POST", "/knowledge-bases", token=token, json=body, expected=(200, 201)
        )
        data = payload.get("data") or payload
        return dict(data) if isinstance(data, dict) else {}

    async def list_documents(
        self, token: str, knowledge_base_id: str, *, page_size: int = 100
    ) -> dict[str, Any]:
        kb_id = _uuid(knowledge_base_id, "knowledge base id")
        payload = await self._request(
            "GET",
            f"/knowledge-bases/{kb_id}/knowledge?page=1&page_size={max(1, min(page_size, 100))}",
            token=token,
        )
        return {
            "items": [
                dict(item) for item in payload.get("data") or []
                if isinstance(item, dict)
            ],
            "total": int(payload.get("total") or 0),
        }

    async def upload_document(
        self,
        token: str,
        knowledge_base_id: str,
        *,
        filename: str,
        content: bytes,
        content_type: str,
    ) -> dict[str, Any] | None:
        kb_id = _uuid(knowledge_base_id, "knowledge base id")
        try:
            payload = await self._request(
                "POST",
                f"/knowledge-bases/{kb_id}/knowledge/file",
                token=token,
                files={"file": (filename, content, content_type)},
                data={"fileName": filename, "channel": "firstflight"},
                expected=(200, 201),
            )
        except WeKnoraError as error:
            if "HTTP 409" in str(error):
                return None
            raise
        data = payload.get("data") or payload
        return dict(data) if isinstance(data, dict) else {}

    async def move_documents(
        self,
        token: str,
        knowledge_base_id: str,
        knowledge_ids: list[str],
        folder_path: str,
    ) -> None:
        if not knowledge_ids:
            return
        await self._request(
            "POST",
            "/knowledge/folder",
            token=token,
            json={
                "kb_id": _uuid(knowledge_base_id, "knowledge base id"),
                "knowledge_ids": [_uuid(item, "knowledge id") for item in knowledge_ids],
                "folder_path": folder_path.strip("/"),
            },
        )

    async def list_skills(self, token: str) -> list[dict[str, Any]]:
        payload = await self._request("GET", "/skills/catalog", token=token)
        data = payload.get("data") or []
        if isinstance(data, dict):
            data = data.get("items") or []
        return [dict(item) for item in data if isinstance(item, dict)]

    async def list_skill_files(self, token: str, skill_id: str) -> list[dict[str, Any]]:
        value = _uuid(skill_id, "skill id")
        payload = await self._request(
            "GET", f"/skills/catalog/{value}/files", token=token
        )
        data = payload.get("data") or []
        if isinstance(data, dict):
            data = data.get("items") or data.get("files") or []
        return [dict(item) for item in data if isinstance(item, dict)]

    async def list_models(self, token: str) -> list[dict[str, Any]]:
        payload = await self._request("GET", "/models", token=token)
        data = payload.get("data") or []
        return [dict(item) for item in data if isinstance(item, dict)]

    async def clone_models(
        self, token: str, source_models: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        existing = await self.list_models(token)
        by_key = {(str(item.get("type")), str(item.get("name"))): item for item in existing}
        for source in source_models:
            key = (str(source.get("type")), str(source.get("name")))
            if key in by_key:
                continue
            body = {
                "name": source.get("name"),
                "display_name": source.get("display_name") or source.get("name"),
                "type": source.get("type"),
                "source": source.get("source"),
                "description": source.get("description") or "firstFlight shared workspace model",
                "parameters": source.get("parameters") or {},
            }
            payload = await self._request(
                "POST", "/models", token=token, json=body, expected=(200, 201)
            )
            item = payload.get("data") or payload
            if isinstance(item, dict):
                by_key[key] = dict(item)
        return list(by_key.values())
