"""Built-in diagnostic tools (no external dependency, always available)."""

from __future__ import annotations

import json
from typing import Any, Mapping

from app.mcp_gateway.adapters.base import BaseAdapter, ToolResult, text_content
from app.mcp_gateway.config import SkillSpec
from app.mcp_gateway.errors import McpError, SKILL_NOT_FOUND


class BuiltinAdapter(BaseAdapter):
    name = "builtin"

    async def call_tool(
        self, skill: SkillSpec, args: Mapping[str, Any], *, agent: Any = None
    ) -> ToolResult:
        if skill.id == "gateway_ping":
            return ToolResult(content=[text_content(self._ping(args, agent))])
        if skill.id == "gateway_whoami":
            return ToolResult(content=[text_content(self._whoami(agent))])
        raise McpError(SKILL_NOT_FOUND, f"内置工具 '{skill.id}' 未实现。")

    def _ping(self, args: Mapping[str, Any], agent: Any) -> str:
        message = str(args.get("message") or "").strip() or "pong"
        visible = self.ctx.exposure.summary(agent) if agent is not None else {}
        counts = {key: len(value) for key, value in visible.items()}
        return json.dumps(
            {
                "echo": message,
                "mock_upstream": self.ctx.mock,
                "visible": counts,
            },
            ensure_ascii=False,
        )

    def _whoami(self, agent: Any) -> str:
        if agent is None:  # pragma: no cover - defensive
            return json.dumps({"agent_id": None}, ensure_ascii=False)
        policy = self.ctx.config.agents.get(agent.agent_id)
        quota = policy.rate_limit_per_minute if policy else None
        return json.dumps(
            {
                "agent_id": agent.agent_id,
                "roles": list(agent.roles),
                "auth_mode": agent.auth_mode,
                "rate_limit_per_minute": quota
                if quota is not None
                else self.ctx.config.settings.per_agent_per_minute,
                "exposure": self.ctx.exposure.summary(policy) if policy else {},
                "mock_upstream": self.ctx.mock,
            },
            ensure_ascii=False,
        )
