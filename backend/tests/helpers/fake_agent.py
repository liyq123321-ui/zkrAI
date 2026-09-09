"""Deterministic typed AgentGateway double for service tests."""

from collections import deque

from app.domain.types import (
    ClarificationAnalysis,
    HtmlPrototypePayload,
    PrdRewriteOutput,
    ProjectSpecPayload,
    ReviewVerdict,
    SemanticReview,
    WorkBreakdown,
)
from app.domain.implementation_plan import ImplementationPlan
from app.domain.sdlc import LifecycleAssessment


class ScriptedAgentGateway:
    """Deque-backed gateway that records service-level calls."""

    def __init__(
        self,
        *,
        analyze_results: deque[ClarificationAnalysis | Exception] | None = None,
        lifecycle_results: deque[LifecycleAssessment | Exception] | None = None,
        generate_results: deque[ProjectSpecPayload | Exception] | None = None,
        prototype_results: deque[HtmlPrototypePayload | Exception] | None = None,
        review_results: deque[SemanticReview | Exception] | None = None,
        review_breakdown_results: deque[SemanticReview | Exception] | None = None,
        decompose_results: deque[WorkBreakdown | Exception] | None = None,
        plan_results: deque[ImplementationPlan | Exception] | None = None,
        rewrite_results: deque[PrdRewriteOutput | Exception] | None = None,
    ) -> None:
        self._results = {
            "recommend_lifecycle": lifecycle_results or deque(),
            "analyze_brief": analyze_results or deque(),
            "generate_spec": generate_results or deque(),
            "generate_prd_prototype": prototype_results or deque(),
            "review_spec": review_results or deque(),
            "review_breakdown": review_breakdown_results or deque([SemanticReview(verdict=ReviewVerdict.PASS, findings=[])]),
            "decompose_spec": decompose_results or deque(),
            "plan_task": plan_results or deque(),
            "rewrite_prd": rewrite_results or deque(),
        }
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.child_process_calls = 0
        self.prototype_enabled = prototype_results is not None

    async def recommend_lifecycle(self, payload: dict[str, object]) -> LifecycleAssessment:
        return self._next("recommend_lifecycle", payload)

    async def analyze_brief(self, payload: dict[str, object]) -> ClarificationAnalysis:
        return self._next("analyze_brief", payload)

    async def generate_spec(self, payload: dict[str, object]) -> ProjectSpecPayload:
        return self._next("generate_spec", payload)

    async def generate_prd_prototype(
        self, payload: dict[str, object]
    ) -> HtmlPrototypePayload:
        return self._next("generate_prd_prototype", payload)

    async def review_spec(self, payload: dict[str, object]) -> SemanticReview:
        return self._next("review_spec", payload)

    async def review_breakdown(self, payload: dict[str, object]) -> SemanticReview:
        return self._next("review_breakdown", payload)

    async def decompose_spec(self, payload: dict[str, object]) -> WorkBreakdown:
        return self._next("decompose_spec", payload)

    async def plan_task(self, payload: dict[str, object]) -> ImplementationPlan:
        return self._next("plan_task", payload)

    async def rewrite_prd(self, payload: dict[str, object]) -> PrdRewriteOutput:
        return self._next("rewrite_prd", payload)

    def _next(self, operation: str, payload: dict[str, object]):
        self.calls.append((operation, payload))
        results = self._results[operation]
        if not results:
            raise AssertionError(f"Unexpected Agent call: {operation}")
        result = results.popleft()
        if isinstance(result, Exception):
            raise result
        if callable(result):
            return result(payload)
        return result
