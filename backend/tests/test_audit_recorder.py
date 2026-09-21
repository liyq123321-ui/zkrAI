"""Tests for the Semantica audit recorder (``app.audit.recorder``).

These run WITHOUT a live Neo4j: a fake in-memory ``GraphStore`` is injected so
the recorder's node/relationship/provenance logic is exercised end-to-end, and
we also assert that a disabled recorder is a complete no-op.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# Ensure the backend package is importable no matter where pytest runs from.
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

import app.audit.recorder as recorder  # noqa: E402


class FakeGraphStore:
    """In-memory stand-in for ``semantica.graph_store.GraphStore``."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.nodes = {}  # id -> {"labels", "properties"}
        self.rels = []  # list of (start, end, type, props)
        self._next = 1

    def create_node(self, labels, properties, **options):
        nid = self._next
        self._next += 1
        self.nodes[nid] = {"labels": list(labels), "properties": dict(properties)}
        return {"id": nid, "labels": list(labels), "properties": dict(properties)}

    def get_nodes(self, labels=None, properties=None, limit=100, **options):
        out = []
        for nid, n in self.nodes.items():
            if labels and not all(l in n["labels"] for l in labels):
                continue
            if properties and not all(
                n["properties"].get(k) == v for k, v in properties.items()
            ):
                continue
            out.append({"id": nid, "labels": n["labels"], "properties": n["properties"]})
        return out[:limit]

    def create_relationship(self, start, end, rel_type, properties=None, **options):
        self.rels.append((start, end, rel_type, dict(properties or {})))
        return {"id": len(self.rels), "type": rel_type}


@pytest.fixture(autouse=True)
def _reset_singleton():
    recorder._recorder_singleton = None
    recorder._circuit_until = 0.0
    yield
    recorder._recorder_singleton = None
    recorder._circuit_until = 0.0


def _ctx(**over):
    base = dict(
        project_id="proj-1",
        agent_session_id="sess-1",
        agent_call_id="call-1",
        operation="generate_prd",
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_disabled_is_noop(monkeypatch):
    # Force-disabled regardless of backend/.env (load_dotenv uses override=False).
    monkeypatch.setenv("SEMATICA_ENABLED", "false")
    rec = recorder.get_recorder()
    assert rec.enabled is False
    assert recorder.is_audit_enabled() is False
    # Never builds a graph; recording is a silent no-op.
    assert rec.record_runtime_event(_ctx(), {"type": "x"}) is None
    assert (
        rec.record_llm_decision(operation="o", instructions="i", contract_name="C")
        is None
    )


def test_records_event_and_decision(monkeypatch):
    monkeypatch.setenv("SEMATICA_ENABLED", "true")
    monkeypatch.setattr(recorder, "GraphStore", FakeGraphStore)

    rec = recorder.get_recorder()
    assert rec.enabled is True
    assert isinstance(rec._graph, FakeGraphStore)

    eid = rec.record_runtime_event(
        _ctx(),
        {
            "type": "tool_call",
            "status": "ok",
            "item": {"type": "bash", "command": "ls -la"},
        },
    )
    assert eid is not None

    did = rec.record_llm_decision(
        operation="generate_prd",
        instructions="Write a PRD for a login feature",
        contract_name="PRDContract",
        context=_ctx(),
        confidence=0.87,
        decision_maker="firstflight-codex-provider",
    )
    assert did is not None

    # Vocabulary: all expected labels produced.
    labels_seen = {lbl for n in rec._graph.nodes.values() for lbl in n["labels"]}
    assert {
        "Project",
        "AgentSession",
        "AgentCall",
        "SoftwareAgent",
        "AgentEvent",
        "Decision",
        "AgentDecision",
    }.issubset(labels_seen)

    # Provenance-style relationships exist.
    rel_types = {r[2] for r in rec._graph.rels}
    assert {
        "HAS_SESSION",
        "HAS_CALL",
        "EMITTED",
        "WAS_GENERATED_BY",
        "WAS_ASSOCIATED_WITH",
        "HAS_DECISION",
        "WAS_ATTRIBUTED_TO",
    }.issubset(rel_types)

    # Decision node carries reasoning / contract / confidence.
    dec = next(n for n in rec._graph.nodes.values() if "Decision" in n["labels"])
    assert dec["properties"]["category"] == "llm_call"
    assert dec["properties"]["outcome"] == "PRDContract"
    assert dec["properties"]["confidence"] == 0.87
    assert dec["properties"]["reasoning"].startswith("Write a PRD")


def test_empty_context_is_skipped(monkeypatch):
    monkeypatch.setenv("SEMATICA_ENABLED", "true")
    monkeypatch.setattr(recorder, "GraphStore", FakeGraphStore)
    rec = recorder.get_recorder()
    # Context with no ids -> nothing anchored, no nodes created.
    assert rec.record_runtime_event(SimpleNamespace(), {"type": "x"}) is None
    assert rec._graph.nodes == {}


def test_circuit_breaker_trips_on_failure(monkeypatch):
    monkeypatch.setenv("SEMATICA_ENABLED", "true")

    class BoomGraphStore(FakeGraphStore):
        def get_nodes(self, *a, **k):
            raise RuntimeError("neo4j down")

    monkeypatch.setattr(recorder, "GraphStore", BoomGraphStore)
    rec = recorder.get_recorder()
    # First call hits the failure, trips the circuit, returns None.
    assert rec.record_runtime_event(_ctx(), {"type": "x"}) is None
    # Circuit now open for the cooldown window.
    assert rec.enabled is False


def test_query_decisions_filters_by_project_and_session(monkeypatch):
    monkeypatch.setenv("SEMATICA_ENABLED", "true")
    monkeypatch.setattr(recorder, "GraphStore", FakeGraphStore)
    rec = recorder.get_recorder()

    rec.record_llm_decision(
        operation="generate_prd",
        instructions="PRD for login",
        contract_name="PRDContract",
        context=_ctx(project_id="proj-1", agent_session_id="sess-1", agent_call_id="c1"),
    )
    rec.record_llm_decision(
        operation="review_spec",
        instructions="Review the spec",
        contract_name="SpecContract",
        context=_ctx(project_id="proj-1", agent_session_id="sess-2", agent_call_id="c2"),
    )
    rec.record_llm_decision(
        operation="generate_spec",
        instructions="Spec for search",
        contract_name="SpecContract",
        context=_ctx(project_id="proj-2", agent_session_id="sess-3", agent_call_id="c3"),
    )

    # All decisions for proj-1 (across both sessions).
    proj1 = rec.query_decisions("proj-1")
    assert len(proj1) == 2
    assert {d["project_id"] for d in proj1} == {"proj-1"}

    # Narrow to one session within proj-1.
    sess2 = rec.query_decisions("proj-1", "sess-2")
    assert len(sess2) == 1
    assert sess2[0]["agent_session_id"] == "sess-2"
    assert sess2[0]["reasoning"] == "Review the spec"

    # Other project is isolated.
    assert len(rec.query_decisions("proj-2")) == 1

    # Newest first (later recorded decision sorts ahead).
    assert proj1[0]["ts"] >= proj1[1]["ts"]


def test_query_decisions_returns_empty_when_unavailable(monkeypatch):
    monkeypatch.setenv("SEMATICA_ENABLED", "true")

    class SilentBoomGraphStore(FakeGraphStore):
        def get_nodes(self, *a, **k):
            raise RuntimeError("neo4j down")

    monkeypatch.setattr(recorder, "GraphStore", SilentBoomGraphStore)
    rec = recorder.get_recorder()
    assert rec.query_decisions("proj-1") == []


def test_query_decisions_strict_raises_when_unreachable(monkeypatch):
    """The HTTP read API needs to tell 'no records' apart from 'graph down'."""
    monkeypatch.setenv("SEMATICA_ENABLED", "true")

    class BoomGraphStore(FakeGraphStore):
        def get_nodes(self, *a, **k):
            raise RuntimeError("neo4j down")

    monkeypatch.setattr(recorder, "GraphStore", BoomGraphStore)
    rec = recorder.get_recorder()
    # Default is defensive...
    assert rec.query_decisions("proj-1") == []
    # ...but strict mode surfaces the failure.
    with pytest.raises(RuntimeError):
        rec.query_decisions("proj-1", strict=True)


def test_status_reflects_graph_reachability(monkeypatch):
    monkeypatch.setenv("SEMATICA_ENABLED", "true")
    monkeypatch.setattr(recorder, "GraphStore", FakeGraphStore)

    # Enabled and reachable -> available.
    rec = recorder.get_recorder()
    status = recorder.get_audit_status()
    assert status["configured"] is True
    assert status["available"] is True
    assert status["backend"] == "neo4j"

    # Same recorder, but the backend is now unreachable -> available flips to
    # False once the probe cache expires.
    def boom(*a, **k):
        raise RuntimeError("neo4j down")

    rec._graph.get_nodes = boom  # type: ignore[assignment]
    rec._probe_at = 0.0  # expire the cached probe result
    assert recorder.get_audit_status()["available"] is False


def test_probe_is_cached(monkeypatch):
    monkeypatch.setenv("SEMATICA_ENABLED", "true")
    monkeypatch.setattr(recorder, "GraphStore", FakeGraphStore)
    rec = recorder.get_recorder()

    calls = {"n": 0}
    original = rec._graph.get_nodes

    def counting(*a, **k):
        calls["n"] += 1
        return original(*a, **k)

    rec._graph.get_nodes = counting  # type: ignore[assignment]
    assert rec.probe() is True
    assert rec.probe() is True
    # Second call inside the TTL window is served from cache.
    assert calls["n"] == 1


def test_status_disabled_when_not_enabled(monkeypatch):
    monkeypatch.setenv("SEMATICA_ENABLED", "false")
    status = recorder.get_audit_status()
    assert status["configured"] is False
    assert status["available"] is False
