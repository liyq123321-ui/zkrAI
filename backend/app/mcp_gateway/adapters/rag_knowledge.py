"""Knowledge-resource adapter: RAG retrieval behind tools and resources.

``spaces://{scope}/knowledge/{docId}`` resources are resolved through the
configured retrieval service (WeKnora by default). Results are truncated to
``options.max_chars`` (default 64 KB) before being handed to the agent.
"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping
from urllib.parse import unquote

from app.mcp_gateway.adapters.base import (
    BaseAdapter,
    ToolResult,
    resource_text_content,
    text_content,
)
from app.mcp_gateway.config import SkillSpec
from app.mcp_gateway.errors import McpError, UNAUTHORIZED, UPSTREAM_UNAVAILABLE

_URI_PATTERN = re.compile(r"^spaces://(?P<scope>[^/]+)/knowledge/(?P<doc>.+)$")
_RESULT_KEYS = ("results", "data", "chunks", "documents", "answer", "content")


class RagKnowledgeAdapter(BaseAdapter):
    name = "rag-knowledge"

    # --- tools ----------------------------------------------------------------
    async def call_tool(
        self, skill: SkillSpec, args: Mapping[str, Any], *, agent: Any = None
    ) -> ToolResult:
        query = str(args.get("query") or "").strip()
        if not query:
            raise McpError(-32002, "参数 'query' 不能为空。")
        scope = str(args.get("scope") or (skill.options or {}).get("default_scope") or "collab")
        top_k = int(args.get("top_k") or (skill.options or {}).get("top_k") or 5)
        payload = await self._query(skill, query=query, scope=scope, top_k=top_k)
        return ToolResult(content=[text_content(self._render(skill, payload))])

    # --- resources ------------------------------------------------------------
    async def list_resources(
        self, skill: SkillSpec, *, agent: Any = None
    ) -> list[dict[str, Any]]:
        if not self.ctx.mock:
            return []
        scopes = (skill.options or {}).get("scopes") or ("collab",)
        items: list[dict[str, Any]] = []
        for scope in scopes:
            items.append(
                {
                    "uri": f"spaces://{scope}/knowledge/README",
                    "name": f"{scope}/README",
                    "description": "[mock] 示例知识条目",
                    "mimeType": skill.mime_type or "text/markdown",
                }
            )
        return items

    async def read_resource(
        self, skill: SkillSpec, uri: str, *, agent: Any = None
    ) -> list[dict[str, Any]]:
        parsed = self.parse_uri(uri)
        if parsed is None:
            raise McpError(
                -32002,
                f"资源 URI 无法解析：{uri}（期望 spaces://{{scope}}/knowledge/{{docId}}）",
            )
        scope, doc_id = parsed
        allowed_scopes = (skill.options or {}).get("scopes")
        if allowed_scopes and scope not in [str(s) for s in allowed_scopes]:
            raise McpError(UNAUTHORIZED, f"scope '{scope}' 不在该资源允许范围内。")
        payload = await self._query(skill, query=doc_id, scope=scope, top_k=5)
        text = self._render(skill, payload)
        return [
            resource_text_content(
                uri, text, (skill.mime_type or "text/markdown")
            )
        ]

    # --- internals ------------------------------------------------------------
    @staticmethod
    def parse_uri(uri: str) -> tuple[str, str] | None:
        match = _URI_PATTERN.match(uri or "")
        if not match:
            return None
        return match.group("scope"), unquote(match.group("doc"))

    async def _query(
        self, skill: SkillSpec, *, query: str, scope: str, top_k: int
    ) -> Any:
        if self.ctx.mock:
            return {
                "scope": scope,
                "query": query,
                "results": [
                    {
                        "title": f"[mock] {query}",
                        "snippet": (
                            "MOCK_UPSTREAM=1：未连接真实检索服务。"
                            "配置 RAG_SEARCH_URL + RAG_API_KEY 后返回真实命中片段。"
                        ),
                    }
                ],
            }
        url = self.require_upstream(skill)
        body = {"query": query, "scope": scope, "top_k": top_k}
        try:
            async with self.ctx.client(timeout=skill.timeout_sec) as client:
                response = await client.post(url, json=body, headers=self.auth_headers(skill))
                response.raise_for_status()
                try:
                    return response.json()
                except ValueError:
                    return {"content": response.text}
        except McpError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise McpError(
                UPSTREAM_UNAVAILABLE,
                f"检索服务调用失败：{type(exc).__name__}: {exc}",
                data={"skill": skill.id},
            ) from exc

    def _render(self, skill: SkillSpec, payload: Any) -> str:
        max_chars = int((skill.options or {}).get("max_chars") or 64000)
        if isinstance(payload, Mapping):
            for key in _RESULT_KEYS:
                if key in payload:
                    value = payload[key]
                    if isinstance(value, str):
                        text = value
                    else:
                        text = json.dumps(value, ensure_ascii=False, default=str)
                    break
            else:
                text = json.dumps(payload, ensure_ascii=False, default=str)
        elif isinstance(payload, str):
            text = payload
        else:
            text = json.dumps(payload, ensure_ascii=False, default=str)
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n…[已截断，上限 {max_chars} 字符]"
        return text
