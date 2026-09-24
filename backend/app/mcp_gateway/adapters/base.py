"""Adapter contract shared by every backend binding.

All adapters implement the same handler interface so the router never needs to
know what sits behind a skill. Credentials are resolved *inside* the adapter
from environment variables and are never forwarded to the agent.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping

import httpx

from app.mcp_gateway.audit import Auditor
from app.mcp_gateway.config import GatewayConfig, SkillSpec
from app.mcp_gateway.errors import McpError, SKILL_NOT_FOUND, UPSTREAM_UNAVAILABLE
from app.mcp_gateway.exposure import ExposureEngine
from app.mcp_gateway.ratelimit import SlidingWindowLimiter


def text_content(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def image_content(data: str, mime_type: str = "image/png") -> dict[str, Any]:
    return {"type": "image", "data": data, "mimeType": mime_type}


def resource_text_content(uri: str, text: str, mime_type: str = "text/plain") -> dict[str, Any]:
    return {
        "type": "resource",
        "resource": {"uri": uri, "mimeType": mime_type, "text": text},
    }


@dataclass
class ToolResult:
    content: list[dict[str, Any]]
    is_error: bool = False


@dataclass
class AdapterContext:
    """Everything an adapter may need, injected at construction time."""

    config: GatewayConfig
    exposure: ExposureEngine
    limiter: SlidingWindowLimiter
    auditor: Auditor
    mock: bool = False
    http_timeout: float = 30.0

    def client(self, timeout: float | None = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout or self.http_timeout)


class BaseAdapter:
    """Default (unsupported) behaviour for every handler method."""

    name = "base"

    def __init__(self, ctx: AdapterContext) -> None:
        self.ctx = ctx

    # --- tools ----------------------------------------------------------------
    async def call_tool(
        self, skill: SkillSpec, args: Mapping[str, Any], *, agent: Any = None
    ) -> ToolResult:
        raise McpError(SKILL_NOT_FOUND, f"技能 '{skill.id}' 不支持工具调用。")

    async def dynamic_tools(self, skill: SkillSpec) -> list[dict[str, Any]]:
        """Cascade adapters return the upstream tool catalogue here."""

        return []

    # --- resources ------------------------------------------------------------
    async def list_resources(
        self, skill: SkillSpec, *, agent: Any = None
    ) -> list[dict[str, Any]]:
        raise McpError(SKILL_NOT_FOUND, f"技能 '{skill.id}' 不支持资源列举。")

    async def read_resource(
        self, skill: SkillSpec, uri: str, *, agent: Any = None
    ) -> list[dict[str, Any]]:
        raise McpError(SKILL_NOT_FOUND, f"技能 '{skill.id}' 不支持资源读取。")

    # --- helpers --------------------------------------------------------------
    def upstream_url(self, skill: SkillSpec) -> str | None:
        """Resolve the upstream endpoint from env (never from YAML literals)."""

        options = skill.options or {}
        env_name = options.get("upstream_env")
        if env_name:
            value = os.environ.get(str(env_name), "").strip()
            if value:
                return value
        if skill.upstream:
            return skill.upstream
        fallback_env = options.get("fallback_upstream_env")
        if fallback_env:
            base = os.environ.get(str(fallback_env), "").strip().rstrip("/")
            if base:
                return base + str(options.get("fallback_path") or "")
        return None

    def secret(self, skill: SkillSpec) -> str | None:
        env_name = skill.api_key_env or (skill.options or {}).get("api_key_env")
        if not env_name:
            return None
        value = os.environ.get(str(env_name), "").strip()
        return value or None

    def require_upstream(self, skill: SkillSpec) -> str:
        url = self.upstream_url(skill)
        if not url:
            raise McpError(
                UPSTREAM_UNAVAILABLE,
                f"技能 '{skill.id}' 未配置上游地址：请设置环境变量 "
                f"{skill.options.get('upstream_env') or 'UPSTREAM'}，或用 MOCK_UPSTREAM=1 跑 mock 模式。",
            )
        return url

    def auth_headers(self, skill: SkillSpec) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        token = self.secret(skill)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers
