"""Semantica-backed recorder for firstFlight agent behaviour.

Design
------
* ``GraphStore`` (Neo4j property graph) is the durable, queryable backend.
* Every ``AgentRuntimeEvent`` is mirrored as a graph node (``AgentEvent``) linked
  to its ``Project`` / ``AgentSession`` / ``AgentCall`` and tagged with W3C PROV-O
  style edges (``WAS_GENERATED_BY`` / ``WAS_ASSOCIATED_WITH``).
* Each LLM operation in ``CodexProvider._call`` is recorded as a ``Decision`` node
  carrying ``reasoning`` / ``outcome`` / ``confidence`` and causally linked to the
  originating ``AgentCall``.

The recorder is fully defensive: the graph is built lazily, never blocks the main
path, and a circuit-breaker suppresses repeated connection attempts while Neo4j is
down (default 5-minute cooldown).
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from typing import Any, Mapping, Optional

from app.config import Settings

# Semantica's GraphStore re-enables the global progress tracker on construction
# and the FileProgressDisplay logs an INFO line per node/relationship. Across a
# busy agent that is ~15-20 log lines per event, which drowns real signal.
# Disable progress output for the audit layer (console is already suppressed in
# non-TTY environments; this also silences the file logger).
os.environ.setdefault("SEMANTICA_DISABLE_PROGRESS", "1")

logger = logging.getLogger(__name__)

# --- Graph vocabulary -------------------------------------------------------
L_PROJECT = "Project"
L_SESSION = "AgentSession"
L_CALL = "AgentCall"
L_EVENT = "AgentEvent"
L_DECISION = "Decision"
L_DECISION_AGENT = "AgentDecision"
L_AGENT = "SoftwareAgent"

R_HAS_SESSION = "HAS_SESSION"
R_HAS_CALL = "HAS_CALL"
R_EMITTED = "EMITTED"
R_HAS_DECISION = "HAS_DECISION"
R_WAS_GENERATED_BY = "WAS_GENERATED_BY"
R_WAS_ASSOCIATED_WITH = "WAS_ASSOCIATED_WITH"
R_WAS_ATTRIBUTED_TO = "WAS_ATTRIBUTED_TO"
R_CAUSED = "CAUSED"

SYSTEM_AGENT_ID = "firstflight-ai"
SYSTEM_AGENT_NAME = "FirstFlight PRD Agent"

# --- Tuning -----------------------------------------------------------------
COOLDOWN_SECONDS = 300
REASONING_LIMIT = 2000
EVENT_DETAIL_LIMIT = 500
# How long a connectivity probe result is reused before re-checking the graph.
STATUS_PROBE_TTL_SECONDS = 30

# --- Semantica availability (import is optional) -----------------------------
try:  # pragma: no cover - depends on environment
    from semantica.graph_store import GraphStore

    _SEMANTICA_AVAILABLE = True
except Exception:  # pragma: no cover
    GraphStore = None  # type: ignore[assignment]
    _SEMANTICA_AVAILABLE = False


# --- Module-level resilient state -------------------------------------------
_circuit_until: float = 0.0
_recorder_singleton: Optional["SemanticaRecorder"] = None


def _now() -> float:
    return time.time()


class SemanticaRecorder:
    """Records firstFlight agent behaviour into a Semantica Neo4j graph."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._enabled = bool(settings.semantica_enabled) and _SEMANTICA_AVAILABLE
        self._graph = self._build_graph() if self._enabled else None
        if self._graph is None:
            self._enabled = False
        # (cache_key) -> internal node id, so we don't recreate shared nodes
        self._node_cache: dict[tuple, Any] = {}
        # Cached result of the last connectivity probe (see ``probe``).
        self._probe_at: float = 0.0
        self._probe_ok: bool = False

    # -- construction -------------------------------------------------------
    def _build_graph(self) -> Optional["GraphStore"]:
        if not _SEMANTICA_AVAILABLE or GraphStore is None:
            logger.warning("Semantica not installed; audit layer disabled.")
            return None
        try:
            backend = (self._settings.semantica_backend or "neo4j").lower()
            if backend == "neo4j":
                return GraphStore(
                    backend="neo4j",
                    uri=self._settings.semantica_neo4j_uri,
                    user=self._settings.semantica_neo4j_user,
                    password=self._settings.semantica_neo4j_password or "",
                    database=self._settings.semantica_neo4j_database,
                )
            logger.warning("Unsupported semantica backend %r; audit disabled.", backend)
            return None
        except Exception:  # pragma: no cover
            logger.exception("Failed to build Semantica graph store")
            return None

    # -- state ---------------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return self._enabled and self._graph is not None

    def _available(self) -> bool:
        global _circuit_until
        if _circuit_until and _now() < _circuit_until:
            return False
        return self.enabled

    def _trip_circuit(self) -> None:
        global _circuit_until
        _circuit_until = _now() + COOLDOWN_SECONDS
        self._enabled = False

    # -- node helpers -------------------------------------------------------
    def _ensure_node(
        self, labels, match_props, create_props=None
    ) -> Optional[Any]:
        """Idempotently get-or-create a node; returns its internal id."""
        cache_key = (tuple(labels), tuple(sorted(match_props.items())))
        cached = self._node_cache.get(cache_key)
        if cached is not None:
            return cached
        store = self._graph
        assert store is not None
        existing = store.get_nodes(labels=list(labels), properties=match_props, limit=1)
        if existing:
            node_id = existing[0].get("id")
            if node_id is not None:
                self._node_cache[cache_key] = node_id
                return node_id
        created = store.create_node(
            labels=list(labels),
            properties={**match_props, **(create_props or {})},
        )
        node_id = created.get("id") if isinstance(created, dict) else None
        if node_id is not None:
            self._node_cache[cache_key] = node_id
        return node_id

    def _system_agent(self) -> Optional[Any]:
        return self._ensure_node(
            [L_AGENT],
            {"agent_id": SYSTEM_AGENT_ID},
            {"name": SYSTEM_AGENT_NAME},
        )

    def _link(self, start, end, rel_type, props=None) -> None:
        if start is None or end is None:
            return
        self._graph.create_relationship(start, end, rel_type, props or {})

    # -- public API ---------------------------------------------------------
    def record_runtime_event(
        self, context: Any, event: Mapping[str, Any]
    ) -> Optional[str]:
        """Mirror one agent runtime event into the audit graph."""
        if not self._available():
            return None
        try:
            store = self._graph
            assert store is not None
            project_id = getattr(context, "project_id", None)
            session_id = getattr(context, "agent_session_id", None)
            call_id = getattr(context, "agent_call_id", None)
            operation = getattr(context, "operation", None)

            # Without any identifying context there is nothing meaningful to
            # anchor the event to; skip rather than create orphan nodes.
            if not any((project_id, session_id, call_id)):
                return None

            proj = self._ensure_node(
                [L_PROJECT], {"project_id": project_id}, {"name": str(project_id)}
            )
            sess = self._ensure_node(
                [L_SESSION],
                {"agent_session_id": session_id},
                {"project_id": project_id},
            )
            call = self._ensure_node(
                [L_CALL, "Activity"],
                {"agent_call_id": call_id},
                {"project_id": project_id, "agent_session_id": session_id, "operation": operation},
            )
            self._link(proj, sess, R_HAS_SESSION)
            self._link(sess, call, R_HAS_CALL)
            agent = self._system_agent()
            self._link(call, agent, R_WAS_ASSOCIATED_WITH)

            event_type = str(event.get("type") or "unknown")
            item = event.get("item")
            item_type = (
                str(item.get("type")) if isinstance(item, dict) else None
            )
            status = _safe_status(event, item)
            detail = _safe_detail(event, item)

            node = store.create_node(
                labels=[L_EVENT],
                properties={
                    "event_type": event_type,
                    "item_type": item_type,
                    "status": status,
                    "operation": operation,
                    "project_id": project_id,
                    "agent_session_id": session_id,
                    "agent_call_id": call_id,
                    "ts": _now(),
                    "detail": detail,
                },
            )
            event_id = node.get("id") if isinstance(node, dict) else None
            self._link(call, event_id, R_EMITTED)
            self._link(event_id, call, R_WAS_GENERATED_BY)
            return event_id
        except Exception:
            logger.warning("Semantica audit: record_runtime_event failed", exc_info=True)
            self._trip_circuit()
            return None

    def record_llm_decision(
        self,
        operation: str,
        *,
        instructions: str,
        contract_name: str,
        context: Any = None,
        confidence: Optional[float] = None,
        decision_maker: str = "firstflight-codex-provider",
    ) -> Optional[str]:
        """Record one LLM call as a Decision node linked to its AgentCall."""
        if not self._available():
            return None
        try:
            store = self._graph
            assert store is not None
            decision_id = str(uuid.uuid4())
            node = store.create_node(
                labels=[L_DECISION, L_DECISION_AGENT],
                properties={
                    "decision_id": decision_id,
                    "category": "llm_call",
                    "scenario": operation,
                    "reasoning": (instructions or "")[:REASONING_LIMIT],
                    "outcome": contract_name or "unknown",
                    "confidence": float(confidence) if confidence is not None else 0.0,
                    "decision_maker": decision_maker,
                    "ts": _now(),
                    "project_id": getattr(context, "project_id", None),
                    "agent_session_id": getattr(context, "agent_session_id", None),
                    "agent_call_id": getattr(context, "agent_call_id", None),
                },
            )
            d_id = node.get("id") if isinstance(node, dict) else None
            if d_id is None:
                return decision_id

            call = self._ensure_node(
                [L_CALL, "Activity"],
                {"agent_call_id": getattr(context, "agent_call_id", None)}
                if context is not None
                else {"agent_call_id": "__none__"},
                {
                    "operation": operation,
                    "project_id": getattr(context, "project_id", None),
                    "agent_session_id": getattr(context, "agent_session_id", None),
                },
            )
            self._link(call, d_id, R_HAS_DECISION)
            self._link(d_id, call, R_WAS_GENERATED_BY)
            agent = self._system_agent()
            self._link(d_id, agent, R_WAS_ATTRIBUTED_TO)
            return decision_id
        except Exception:
            logger.warning("Semantica audit: record_llm_decision failed", exc_info=True)
            self._trip_circuit()
            return None

    def query_decisions(
        self,
        project_id: Optional[str],
        agent_session_id: Optional[str] = None,
        limit: int = 500,
        strict: bool = False,
    ) -> list[dict]:
        """Read back LLM ``Decision`` nodes for a project (optionally one session).

        Returns a list of plain dicts (newest first). By default it is safe to
        call when the graph is disabled or unreachable — it logs and returns
        ``[]``. Pass ``strict=True`` (used by the HTTP read API) to let the
        underlying connection error propagate, so callers can tell "no records"
        apart from "Neo4j unreachable".
        """
        if not self.enabled:
            if strict:
                raise RuntimeError("Semantica audit layer is disabled")
            return []
        store = self._graph
        assert store is not None
        filters: dict = {}
        if project_id is not None:
            filters["project_id"] = project_id
        if agent_session_id is not None:
            filters["agent_session_id"] = agent_session_id
        try:
            nodes = store.get_nodes(labels=[L_DECISION], properties=filters or None, limit=limit)
        except Exception:
            logger.warning("Semantica audit: query_decisions failed", exc_info=True)
            if strict:
                raise
            return []
        decisions = [_node_props(node) for node in nodes]
        decisions.sort(key=lambda d: float(d.get("ts") or 0), reverse=True)
        return decisions

    def probe(self, ttl: float = STATUS_PROBE_TTL_SECONDS) -> bool:
        """Cheap, cached reachability check for the audit graph.

        Issues a single-node read so the caller can distinguish "Neo4j is down"
        from "there simply are no decisions yet". The result is memoised for
        ``ttl`` seconds to keep the status endpoint off the hot path.
        """
        if not self.enabled:
            return False
        now = _now()
        if self._probe_at and now - self._probe_at < ttl:
            return self._probe_ok
        store = self._graph
        ok = False
        if store is not None:
            try:
                store.get_nodes(labels=[L_DECISION], limit=1)
                ok = True
            except Exception:
                logger.debug("Semantica audit: connectivity probe failed", exc_info=True)
                ok = False
        self._probe_at = now
        self._probe_ok = ok
        return ok

    def status(self) -> dict:
        """Read-only health snapshot for the audit tab / status endpoint."""
        settings = self._settings
        return {
            "configured": bool(settings.semantica_enabled),
            "available": self.probe(),
            "backend": settings.semantica_backend,
            "graph_name": settings.semantica_graph_name,
            "neo4j_uri": settings.semantica_neo4j_uri,
        }


# --- small helpers ----------------------------------------------------------
def _safe_status(event: Mapping[str, Any], item) -> Optional[str]:
    raw = event.get("status")
    if raw is None and isinstance(item, dict):
        raw = item.get("status")
    return str(raw) if raw is not None else None


def _safe_detail(event: Mapping[str, Any], item) -> Optional[str]:
    """Derive a short, non-sensitive summary (never the raw payload)."""
    if isinstance(item, dict):
        for key in ("text", "summary", "command", "message", "query", "explanation"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:EVENT_DETAIL_LIMIT]
    for key in ("message", "error", "text"):
        value = event.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:EVENT_DETAIL_LIMIT]
    return None


def _node_props(node: Mapping[str, Any]) -> dict:
    """Normalise a GraphStore node dict to its property map.

    Some Semantica stores return ``{"id", "labels", "properties": {...}}`` while
    others flatten properties to the top level; handle both shapes.
    """
    if not isinstance(node, dict):
        return {}
    nested = node.get("properties")
    if isinstance(nested, dict):
        return dict(nested)
    return {k: v for k, v in node.items() if k not in ("id", "labels")}


# --- singleton + convenience functions --------------------------------------
def get_recorder(settings: Optional[Settings] = None) -> SemanticaRecorder:
    global _recorder_singleton
    if _recorder_singleton is None:
        if settings is None:
            settings = Settings.from_env()
        _recorder_singleton = SemanticaRecorder(settings)
    return _recorder_singleton


def is_audit_enabled() -> bool:
    return get_recorder().enabled


def is_audit_available() -> bool:
    """Whether the audit graph is actually reachable right now (cached probe)."""
    return get_recorder().probe()


def get_audit_status() -> dict:
    return get_recorder().status()


def query_decisions(
    project_id: Optional[str],
    agent_session_id: Optional[str] = None,
    limit: int = 500,
    strict: bool = False,
) -> list[dict]:
    return get_recorder().query_decisions(project_id, agent_session_id, limit, strict)


def is_audit_available() -> bool:
    """Whether the audit graph is actually reachable right now."""
    return get_recorder().probe()


def record_runtime_event(context: Any, event: Mapping[str, Any]) -> Optional[str]:
    return get_recorder().record_runtime_event(context, event)


def record_llm_decision(
    operation: str,
    *,
    instructions: str,
    contract_name: str,
    context: Any = None,
    confidence: Optional[float] = None,
    decision_maker: str = "firstflight-codex-provider",
) -> Optional[str]:
    return get_recorder().record_llm_decision(
        operation,
        instructions=instructions,
        contract_name=contract_name,
        context=context,
        confidence=confidence,
        decision_maker=decision_maker,
    )
