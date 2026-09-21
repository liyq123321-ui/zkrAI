"""HTTP routes exposing the Semantica agent-behaviour audit graph.

These are read-only inspectors surfaced in the WebGUI audit tab. They never
block the main workflow: when Semantica is disabled or Neo4j is unreachable
they return a clear ``available: false`` payload instead of raising.
"""

from typing import Any, Optional

from fastapi import APIRouter, Query

from app.audit.recorder import get_audit_status, is_audit_enabled, query_decisions

def build_router() -> APIRouter:
    router = APIRouter(prefix="/semantica", tags=["semantica-audit"])

    @router.get("/status")
    def semantica_status() -> dict[str, Any]:
        """Health snapshot of the Semantica audit layer for the UI badge."""
        return get_audit_status()

    @router.get("/decisions")
    def semantica_decisions(
        project_id: str = Query(..., min_length=1),
        agent_session_id: Optional[str] = Query(default=None),
        limit: int = Query(default=500, ge=1, le=2000),
    ) -> dict[str, Any]:
        """LLM decision provenance for one project (optionally one agent session).

        The semantic value of the Semantica layer lives here: the runtime events
        are already visible from the SQL store, but the *decisions* (reasoning,
        outcome, confidence, decision maker) are captured only in the Neo4j PROV
        graph.
        """
        if not is_audit_enabled():
            return {"available": False, "reason": "disabled", "decisions": []}
        try:
            decisions = query_decisions(project_id, agent_session_id, limit, strict=True)
        except Exception:
            return {"available": False, "reason": "unreachable", "decisions": []}
        return {"available": True, "decisions": decisions}

    return router
