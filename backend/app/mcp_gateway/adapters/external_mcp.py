"""Cascade adapter: forward to an external MCP server as a client.

Upstream tools are discovered with ``tools/list`` and exposed as
``{skill_id}.{upstream_tool}``. When the upstream is unreachable the cascade
tools are simply omitted from ``tools/list`` instead of failing the whole call.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any, Mapping

from app.mcp_gateway.adapters.base import BaseAdapter, ToolResult, text_content
from app.mcp_gateway.config import SkillSpec
from app.mcp_gateway.errors import McpError, UPSTREAM_UNAVAILABLE

_MOCK_UPSTREAM_TOOL = "demo_search"


class ExternalMcpAdapter(BaseAdapter):
    name = "external-mcp"

    def __init__(self, ctx: Any) -> None:
        super().__init__(ctx)
        self._cache: dict[str, tuple[float, list[dict[str, Any]], dict[str, str]]] = {}

    def name_template(self, skill: SkillSpec) -> str:
        return str((skill.options or {}).get("name_template") or "{skill}.{tool}")

    def prefix_for(self, skill: SkillSpec) -> str:
        return self.name_template(skill).format(skill=skill.id, tool="")

    # --- catalogue ------------------------------------------------------------
    async def dynamic_tools(self, skill: SkillSpec) -> list[dict[str, Any]]:
        if self.ctx.mock:
            return [
                {
                    "name": self.name_template(skill).format(
                        skill=skill.id, tool=_MOCK_UPSTREAM_TOOL
                    ),
                    "description": f"[mock] 级联自 {skill.id} 的示例工具",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                }
            ]
        ttl = float((skill.options or {}).get("cache_ttl_sec") or 60)
        cached = self._cache.get(skill.id)
        now = time.monotonic()
        if cached and now < cached[0]:
            return cached[1]
        try:
            result = await self._rpc(skill, "tools/list")
        except McpError:
            # Degrade gracefully: return the stale catalogue if we have one.
            return cached[1] if cached else []
        tools = result.get("tools") if isinstance(result, Mapping) else None
        entries: list[dict[str, Any]] = []
        mapping: dict[str, str] = {}
        for raw in tools or ():
            if not isinstance(raw, Mapping):
                continue
            upstream_name = str(raw.get("name") or "").strip()
            if not upstream_name:
                continue
            exposed = self.name_template(skill).format(skill=skill.id, tool=upstream_name)
            mapping[exposed] = upstream_name
            entries.append(
                {
                    "name": exposed,
                    "description": str(raw.get("description") or "") or f"级联工具 {upstream_name}",
                    "inputSchema": raw.get("inputSchema")
                    or {"type": "object", "properties": {}},
                }
            )
        self._cache[skill.id] = (now + ttl, entries, mapping)
        return entries

    def upstream_tool_name(self, skill: SkillSpec, exposed_name: str) -> str:
        cached = self._cache.get(skill.id)
        if cached and exposed_name in cached[2]:
            return cached[2][exposed_name]
        prefix = self.prefix_for(skill)
        return exposed_name[len(prefix):] if exposed_name.startswith(prefix) else exposed_name

    # --- invocation -----------------------------------------------------------
    async def call_tool(
        self, skill: SkillSpec, args: Mapping[str, Any], *, agent: Any = None
    ) -> ToolResult:
        upstream_name = str(args.pop("__upstream_tool__", "") or skill.id)
        if self.ctx.mock:
            return ToolResult(
                content=[
                    text_content(
                        f"[mock] 级联调用 {skill.id}/{upstream_name}，"
                        f"参数 {json.dumps(dict(args), ensure_ascii=False)[:200]}"
                    )
                ]
            )
        result = await self._rpc(
            skill,
            "tools/call",
            {"name": upstream_name, "arguments": dict(args)},
        )
        if isinstance(result, Mapping):
            content = result.get("content") or []
            return ToolResult(
                content=[item for item in content if isinstance(item, dict)]
                or [text_content(json.dumps(result, ensure_ascii=False, default=str))],
                is_error=bool(result.get("isError")),
            )
        return ToolResult(content=[text_content(json.dumps(result, ensure_ascii=False, default=str))])

    # --- transport ------------------------------------------------------------
    async def _rpc(self, skill: SkillSpec, method: str, params: dict[str, Any] | None = None) -> Any:
        url = self.require_upstream(skill)
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        token_env = (skill.options or {}).get("token_env")
        token = os.environ.get(str(token_env), "").strip() if token_env else None
        if not token:
            token = self.secret(skill)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        body = {"jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": method}
        if params is not None:
            body["params"] = params

        try:
            async with self.ctx.client(timeout=skill.timeout_sec) as client:
                response = await client.post(url, json=body, headers=headers)
                response.raise_for_status()
                payload = self._decode(response)
        except McpError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise McpError(
                UPSTREAM_UNAVAILABLE,
                f"外部 MCP 服务不可达：{type(exc).__name__}: {exc}",
                data={"skill": skill.id, "method": method},
            ) from exc
        if isinstance(payload, Mapping) and payload.get("error"):
            error = payload["error"] if isinstance(payload["error"], Mapping) else {}
            raise McpError(
                UPSTREAM_UNAVAILABLE,
                f"外部 MCP 返回错误：{error.get('message', payload['error'])}",
                data={"skill": skill.id, "upstream_error": error},
            )
        return payload.get("result") if isinstance(payload, Mapping) else None

    def _decode(self, response: Any) -> Any:
        content_type = response.headers.get("content-type", "")
        text = response.text or ""
        if "text/event-stream" in content_type:
            for line in text.splitlines():
                if line.startswith("data:"):
                    try:
                        return json.loads(line[5:].strip())
                    except ValueError:
                        continue
            raise McpError(UPSTREAM_UNAVAILABLE, "外部 MCP 返回了无法解析的 SSE 响应。")
        try:
            return response.json()
        except ValueError:
            raise McpError(UPSTREAM_UNAVAILABLE, "外部 MCP 返回了非 JSON 响应。")
