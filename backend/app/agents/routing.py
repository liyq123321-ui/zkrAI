"""Route the typed AgentGateway contract to the project's selected backend."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypeVar

from app.agents.codex import AgentExecutionError, CodexAgentGateway
from app.agents.gateway import AgentGateway
from app.schemas.workflow import AgentBackendName
from app.services.agent_backends import AgentBackendService


ResultT = TypeVar("ResultT")


class RoutingAgentGateway:
    def __init__(
        self,
        backends: dict[AgentBackendName, AgentGateway],
        selections: AgentBackendService,
    ) -> None:
        self._backends = backends
        self._selections = selections

    @property
    def prototype_enabled(self) -> bool:
        return any(
            bool(getattr(gateway, "prototype_enabled", False))
            for gateway in self._backends.values()
        )

    def ensure_available(self, provider: AgentBackendName) -> None:
        self._selections.ensure_available(provider)

    async def _call(
        self,
        operation: str,
        payload: dict[str, object],
        invoke: Callable[[AgentGateway], Awaitable[ResultT]],
    ) -> ResultT:
        try:
            selection = self._selections.resolve_call(operation, payload)
        except ValueError as error:
            raise AgentExecutionError(str(error)) from error
        gateway = self._backends.get(selection.provider)
        if gateway is None:
            raise AgentExecutionError(
                f"Agent backend is not configured: {selection.provider}"
            )
        return await invoke(gateway)

    async def recommend_lifecycle(self, payload):
        return await self._call(
            "recommend_lifecycle", payload,
            lambda gateway: gateway.recommend_lifecycle(payload),
        )

    async def analyze_brief(self, payload):
        return await self._call(
            "analyze_brief", payload, lambda gateway: gateway.analyze_brief(payload)
        )

    async def generate_spec(self, payload):
        return await self._call(
            "generate_spec", payload, lambda gateway: gateway.generate_spec(payload)
        )

    async def generate_prd_prototype(self, payload):
        return await self._call(
            "generate_prd_prototype",
            payload,
            lambda gateway: gateway.generate_prd_prototype(payload),
        )

    async def review_spec(self, payload):
        return await self._call(
            "review_spec", payload, lambda gateway: gateway.review_spec(payload)
        )

    async def review_breakdown(self, payload):
        return await self._call(
            "review_breakdown", payload,
            lambda gateway: gateway.review_breakdown(payload),
        )

    async def decompose_spec(self, payload):
        return await self._call(
            "decompose_spec", payload, lambda gateway: gateway.decompose_spec(payload)
        )

    async def plan_task(self, payload):
        return await self._call(
            "plan_task", payload, lambda gateway: gateway.plan_task(payload)
        )

    async def rewrite_prd(self, payload):
        return await self._call(
            "rewrite_prd", payload, lambda gateway: gateway.rewrite_prd(payload)
        )
