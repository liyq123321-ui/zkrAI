"""Unit tests for gateway configuration, auth, exposure, rate limit, audit."""

from __future__ import annotations

import pytest

from app.mcp_gateway.audit import Auditor, summarize_args
from app.mcp_gateway.auth import Authenticator, mint_agent_token
from app.mcp_gateway.config import load_gateway_config
from app.mcp_gateway.errors import UNAUTHENTICATED, UNAUTHORIZED, McpError
from app.mcp_gateway.exposure import ExposureEngine
from app.mcp_gateway.ratelimit import SlidingWindowLimiter


@pytest.fixture()
def config(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_GATEWAY_AUDIT_DIR", str(tmp_path / "audit"))
    monkeypatch.setenv("MCP_GATEWAY_JWT_SECRET", "unit-test-secret")
    monkeypatch.delenv("MCP_GATEWAY_STATIC_TOKENS", raising=False)
    return load_gateway_config()


def test_default_config_registers_agents_and_skills(config):
    assert {"codex-main", "prd-agent", "worker-default", "readonly-viewer"} <= set(
        config.agents
    )
    skill_ids = {skill.id for skill in config.skills}
    assert {
        "gateway_ping",
        "gateway_whoami",
        "generate_image",
        "search_knowledge",
        "spaces-knowledge",
        "code-search",
        "code-review",
        "prd-review",
    } <= skill_ids
    assert config.settings.per_agent_per_minute == 60


def test_static_token_resolves_to_agent(config):
    authenticator = Authenticator(config)
    identity = authenticator.resolve("Bearer dev-token-codex")
    assert identity.agent_id == "codex-main"
    assert identity.auth_mode == "static"
    assert "dev" in identity.roles


def test_minted_jwt_token_resolves(config):
    authenticator = Authenticator(config, jwt_secret="unit-test-secret")
    token = mint_agent_token("unit-test-secret", "prd-agent", ["pm"], 600)
    identity = authenticator.resolve(f"Bearer {token}")
    assert identity.agent_id == "prd-agent"
    assert identity.auth_mode == "jwt"


def test_unknown_and_missing_tokens_are_rejected(config):
    authenticator = Authenticator(config, jwt_secret="unit-test-secret")
    with pytest.raises(McpError) as missing:
        authenticator.resolve(None)
    assert missing.value.code == UNAUTHENTICATED
    with pytest.raises(McpError) as bad:
        authenticator.resolve("Bearer nope")
    assert bad.value.code == UNAUTHENTICATED
    # token signed with another secret must not be accepted
    with pytest.raises(McpError) as wrong:
        authenticator.resolve(f"Bearer {mint_agent_token('other', 'codex-main')}")
    assert wrong.value.code == UNAUTHENTICATED


def test_unregistered_agent_is_denied(config, monkeypatch):
    monkeypatch.setenv("MCP_GATEWAY_STATIC_TOKENS", "ghost-token:not-registered")
    config = load_gateway_config()
    authenticator = Authenticator(config)
    with pytest.raises(McpError) as exc:
        authenticator.resolve("Bearer ghost-token")
    assert exc.value.code == UNAUTHORIZED


def test_exposure_is_deny_all(config):
    engine = ExposureEngine(config, resolver=None)
    codex = config.agents["codex-main"]
    worker = config.agents["worker-default"]

    assert engine.allows_tool(codex, "generate_image")
    assert not engine.allows_tool(worker, "generate_image")
    assert engine.allows_tool(codex, "code-search.search_repo")  # wildcard 命中级联工具
    assert not engine.allows_prompt(worker, "code-review")

    assert engine.allows_resource(codex, "spaces://collab/knowledge/doc-1")
    assert not engine.allows_resource(codex, "spaces://enterprise/knowledge/doc-1")
    assert engine.allows_resource(
        config.agents["readonly-viewer"], "spaces://enterprise/knowledge/doc-1"
    )


def test_exposure_summary_lists_only_allowed(config):
    engine = ExposureEngine(config, resolver=None)
    summary = engine.summary(config.agents["prd-agent"])
    assert "search_knowledge" in summary["tools"]
    assert "generate_image" not in summary["tools"]
    assert summary["prompts"] == ["prd-review"]


def test_rate_limiter_window():
    limiter = SlidingWindowLimiter(default_limit=3, enabled=True)
    for _ in range(3):
        assert limiter.check("agent-a").allowed
    decision = limiter.check("agent-a")
    assert not decision.allowed
    assert decision.retry_after > 0
    assert limiter.check("agent-b").allowed  # 配额按 agent 隔离
    limiter.reset("agent-a")
    assert limiter.check("agent-a").allowed


def test_rate_limiter_disabled():
    limiter = SlidingWindowLimiter(default_limit=1, enabled=False)
    for _ in range(5):
        assert limiter.check("agent-a").allowed


def test_audit_redacts_secrets(tmp_path):
    auditor = Auditor(enabled=True, log_dir=tmp_path / "audit")
    entry = auditor.record(
        agent_id="codex-main",
        method="tools/call",
        name="generate_image",
        args={"prompt": "一只在写代码的水獭" * 50, "api_key": "sk-should-not-appear"},
        latency_ms=12.5,
    )
    summary = entry["args_summary"]
    assert summary["api_key"] == "***redacted***"
    assert len(summary["prompt"]["preview"]) <= 201
    assert "sk-should-not-appear" not in str(summary)
    log_file = tmp_path / "audit" / f"audit-{entry['ts'][:10]}.jsonl"
    assert log_file.exists()
    assert "sk-should-not-appear" not in log_file.read_text(encoding="utf-8")
    assert auditor.tail(1)[0]["agent_id"] == "codex-main"


def test_summarize_args_handles_nested_structures():
    summary = summarize_args({"items": [1, "two", {"token": "x"}], "flag": True})
    assert summary["flag"]["type"] == "bool"
    assert summary["items"]["size"] == 3
