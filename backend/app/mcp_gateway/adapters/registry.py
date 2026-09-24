"""Adapter registry: adapter name -> handler instance."""

from __future__ import annotations

import logging
from typing import Any

from app.mcp_gateway.adapters.base import AdapterContext, BaseAdapter
from app.mcp_gateway.adapters.builtin import BuiltinAdapter
from app.mcp_gateway.adapters.external_mcp import ExternalMcpAdapter
from app.mcp_gateway.adapters.http_json import HttpJsonAdapter
from app.mcp_gateway.adapters.rag_knowledge import RagKnowledgeAdapter
from app.mcp_gateway.config import SkillSpec

LOGGER = logging.getLogger(__name__)


class AdapterRegistry:
    def __init__(self, ctx: AdapterContext) -> None:
        self.ctx = ctx
        self._adapters: dict[str, BaseAdapter] = {
            BuiltinAdapter.name: BuiltinAdapter(ctx),
            HttpJsonAdapter.name: HttpJsonAdapter(ctx),
            RagKnowledgeAdapter.name: RagKnowledgeAdapter(ctx),
            ExternalMcpAdapter.name: ExternalMcpAdapter(ctx),
        }

    def get(self, skill: SkillSpec) -> BaseAdapter:
        adapter = self._adapters.get(skill.adapter)
        if adapter is None:
            LOGGER.warning(
                "mcp-gateway: skill '%s' 使用了未注册适配器 '%s'，回退到 builtin",
                skill.id,
                skill.adapter,
            )
            return self._adapters[BuiltinAdapter.name]
        return adapter

    def is_dynamic(self, skill: SkillSpec) -> bool:
        return skill.adapter == ExternalMcpAdapter.name

    def tool_names(self, skill: SkillSpec) -> tuple[str, ...] | None:
        """Static tool names, or ``None`` when the adapter expands dynamically."""

        if self.is_dynamic(skill):
            return None
        return (skill.tool_name,)

    def register(self, name: str, adapter: BaseAdapter) -> None:
        self._adapters[name] = adapter

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._adapters))

    def __getitem__(self, name: str) -> BaseAdapter:  # pragma: no cover - convenience
        return self._adapters[name]

    def as_dict(self) -> dict[str, Any]:
        return dict(self._adapters)
