"""Agent gateway contract."""
from typing import Protocol
from app.domain.implementation_plan import ImplementationPlan

from app.domain.types import (
    ClarificationAnalysis,
    PrdRewriteOutput,
    ProjectSpecPayload,
    SemanticReview,
    WorkBreakdown,
)


class AgentGateway(Protocol):
    async def analyze_brief(self, payload: dict[str, object]) -> ClarificationAnalysis:
        """Assess whether a Project Brief is ready for specification."""

    async def generate_spec(self, payload: dict[str, object]) -> ProjectSpecPayload:
        """Generate a typed Project Spec payload."""

    async def review_spec(self, payload: dict[str, object]) -> SemanticReview:
        """Review a Project Spec for semantic quality."""

    async def review_breakdown(self, payload: dict[str, object]) -> SemanticReview:
        """Review a validated work breakdown for semantic consistency."""

    async def decompose_spec(self, payload: dict[str, object]) -> WorkBreakdown:
        """Convert an approved Project Spec into a typed work breakdown."""

    async def plan_task(self, payload: dict[str, object]) -> ImplementationPlan:
        """Detail an existing task without changing its approved boundaries."""

    async def rewrite_prd(self, payload: dict[str, object]) -> PrdRewriteOutput:
        """Rewrite a PRD from a frozen Gitea review-comment snapshot."""
