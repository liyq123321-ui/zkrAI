"""Semantica agent-behaviour audit layer.

Thin, defensive wrapper around Semantica's ``GraphStore`` (Neo4j-backed) that
records every firstFlight agent runtime event and each LLM call as a queryable,
provenance-tagged node in a knowledge graph.

The layer is intentionally *additive* and *best-effort*: if Semantica is not
installed, Neo4j is unreachable, or the feature is disabled, every public call
returns ``None`` and never raises into the PRD-generation path.
"""

from app.audit.recorder import (
    SemanticaRecorder,
    get_audit_status,
    get_recorder,
    is_audit_available,
    is_audit_enabled,
    query_decisions,
    record_llm_decision,
    record_runtime_event,
)

__all__ = [
    "SemanticaRecorder",
    "get_recorder",
    "get_audit_status",
    "is_audit_available",
    "is_audit_enabled",
    "query_decisions",
    "record_runtime_event",
    "record_llm_decision",
]
