"""End-to-end protocol tests: Streamable HTTP + per-agent exposure."""

from __future__ import annotations

import base64

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.mcp_gateway.protocol import McpGateway
from app.mcp_gateway.router import build_mcp_gateway_router

CODEX = "dev-token-codex"
PRD = "dev-token-prd"
WORKER = "dev-token-worker"


def _gateway_and_client(monkeypatch, tmp_path, **env):
    monkeypatch.setenv("MCP_GATEWAY_MOCK_UPSTREAM", "1")
    monkeypatch.setenv("MCP_GATEWAY_AUDIT_DIR", str(tmp_path / "audit"))
    monkeypatch.setenv("MCP_GATEWAY_JWT_SECRET", "e2e-secret")
    monkeypatch.delenv("MCP_GATEWAY_STATIC_TOKENS", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    gateway = McpGateway.build()
    app = FastAPI()
    app.include_router(build_mcp_gateway_router(gateway=gateway))
    return gateway, TestClient(app)


@pytest.fixture()
def client(monkeypatch, tmp_path):
    _, test_client = _gateway_and_client(monkeypatch, tmp_path)
    return test_client


def rpc(test_client, method, params=None, token=CODEX, request_id=1, accept="application/json"):
    headers = {"Accept": accept}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return test_client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}},
        headers=headers,
    )


def test_healthz(client):
    payload = client.get("/mcp-gateway/healthz").json()
    assert payload["status"] == "ok"
    assert payload["endpoint"] == "POST /mcp"
    assert payload["mock_upstream"] is True
    assert payload["agents"] >= 4


def test_initialize_handshake(client):
    response = rpc(client, "initialize", {"protocolVersion": "2025-06-18"}, token=None)
    assert response.status_code == 200
    body = response.json()["result"]
    assert body["protocolVersion"] == "2025-06-18"
    assert {"tools", "resources", "prompts"} <= set(body["capabilities"])
    assert body["serverInfo"]["name"] == "firstflight-mcp-gateway"
    assert response.headers.get("Mcp-Session-Id")


def test_tools_list_is_filtered_per_agent(client):
    codex_tools = {t["name"] for t in rpc(client, "tools/list").json()["result"]["tools"]}
    worker_tools = {
        t["name"] for t in rpc(client, "tools/list", token=WORKER).json()["result"]["tools"]
    }
    prd_tools = {t["name"] for t in rpc(client, "tools/list", token=PRD).json()["result"]["tools"]}

    assert {"gateway_ping", "gateway_whoami", "generate_image", "search_knowledge"} <= codex_tools
    assert any(name.startswith("code-search.") for name in codex_tools)
    assert worker_tools == {"gateway_ping"}
    assert "generate_image" not in prd_tools
    assert "search_knowledge" in prd_tools


def test_unauthorized_tool_call_returns_32001(client):
    body = rpc(
        client,
        "tools/call",
        {"name": "generate_image", "arguments": {"prompt": "x"}},
        token=WORKER,
    ).json()
    assert body["error"]["code"] == -32001


def test_missing_credential_returns_32000(client):
    body = rpc(client, "tools/list", token=None).json()
    assert body["error"]["code"] == -32000


def test_tool_call_returns_image_content(client):
    body = rpc(
        client,
        "tools/call",
        {"name": "generate_image", "arguments": {"prompt": "一只写代码的水獭"}},
    ).json()["result"]
    image = next(item for item in body["content"] if item["type"] == "image")
    assert base64.b64decode(image["data"]).startswith(b"\x89PNG")


def test_invalid_params_returns_32002(client):
    body = rpc(client, "tools/call", {"name": "generate_image", "arguments": {}}).json()
    assert body["error"]["code"] == -32002
    assert "prompt" in body["error"]["data"]["errors"][0]


def test_unknown_tool_returns_32005(client):
    body = rpc(client, "tools/call", {"name": "not_a_tool", "arguments": {}}).json()
    assert body["error"]["code"] == -32005


def test_notification_is_accepted_without_body(client):
    response = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers={"Authorization": f"Bearer {CODEX}"},
    )
    assert response.status_code == 202


def test_resources_list_and_read(client):
    payload = rpc(client, "resources/list").json()["result"]
    templates = payload.get("resourceTemplates") or []
    assert any(t["uriTemplate"] == "spaces://{scope}/knowledge/{docId}" for t in templates)

    content = rpc(
        client,
        "resources/read",
        {"uri": "spaces://collab/knowledge/README"},
    ).json()["result"]["contents"]
    assert content[0]["resource"]["uri"] == "spaces://collab/knowledge/README"
    assert "MOCK_UPSTREAM" in content[0]["resource"]["text"]

    denied = rpc(client, "resources/read", {"uri": "spaces://enterprise/knowledge/x"}).json()
    assert denied["error"]["code"] == -32001


def test_resource_read_denied_for_worker(client):
    body = rpc(
        client, "resources/read", {"uri": "spaces://collab/knowledge/README"}, token=WORKER
    ).json()
    assert body["error"]["code"] == -32001


def test_prompts_list_and_get(client):
    prompts = {p["name"] for p in rpc(client, "prompts/list").json()["result"]["prompts"]}
    assert prompts == {"code-review", "prd-review"}

    result = rpc(
        client,
        "prompts/get",
        {"name": "code-review", "arguments": {"diff": "--- a\n+++ b\n"}},
    ).json()["result"]
    assert "--- a" in result["messages"][0]["content"]["text"]

    missing = rpc(client, "prompts/get", {"name": "code-review", "arguments": {}}).json()
    assert missing["error"]["code"] == -32002

    denied = rpc(
        client, "prompts/get", {"name": "code-review", "arguments": {"diff": "x"}}, token=WORKER
    ).json()
    assert denied["error"]["code"] == -32001


def test_rate_limit_returns_32004(monkeypatch, tmp_path):
    # prd-agent 没有 per-agent 配额覆盖，走全局 MCP_GATEWAY_RATE_LIMIT_PER_MINUTE
    _, test_client = _gateway_and_client(
        monkeypatch, tmp_path, MCP_GATEWAY_RATE_LIMIT_PER_MINUTE="2"
    )
    assert rpc(test_client, "tools/list", token=PRD).status_code == 200
    assert rpc(test_client, "tools/list", token=PRD).status_code == 200
    body = rpc(test_client, "tools/list", token=PRD).json()
    assert body["error"]["code"] == -32004
    assert body["error"]["data"]["limit"] == 2
    # 另一个 agent 的配额独立（worker-default 在 YAML 里是 30/分钟）
    assert rpc(test_client, "tools/list", token=WORKER).status_code == 200


def test_sse_response_when_client_asks_for_stream(client):
    response = rpc(client, "tools/list", accept="text/event-stream")
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    assert '"tools"' in response.text or "tools" in response.text


def test_admin_endpoints_require_token(monkeypatch, tmp_path):
    _, test_client = _gateway_and_client(monkeypatch, tmp_path)
    assert test_client.get("/mcp-gateway/agents").status_code == 503  # 未配置 admin token

    monkeypatch.setenv("MCP_GATEWAY_ADMIN_TOKEN", "s3cr3t")
    _, test_client = _gateway_and_client(monkeypatch, tmp_path)
    assert test_client.get("/mcp-gateway/agents").status_code == 401
    guarded = test_client.get(
        "/mcp-gateway/agents", headers={"X-MCP-Admin-Token": "s3cr3t"}
    )
    assert guarded.status_code == 200
    agents = {item["id"]: item for item in guarded.json()["agents"]}
    assert "generate_image" in agents["codex-main"]["exposure"]["tools"]
    assert agents["worker-default"]["exposure"]["tools"] == ["gateway_ping"]

    skills = test_client.get(
        "/mcp-gateway/skills", headers={"X-MCP-Admin-Token": "s3cr3t"}
    ).json()["skills"]
    assert all("upstream" not in item for item in skills)


def test_audit_endpoint_records_calls(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_GATEWAY_ADMIN_TOKEN", "s3cr3t")
    _, test_client = _gateway_and_client(monkeypatch, tmp_path)
    rpc(test_client, "tools/call", {"name": "gateway_ping", "arguments": {"message": "hi"}})
    audit = test_client.get(
        "/mcp-gateway/audit?limit=10", headers={"X-MCP-Admin-Token": "s3cr3t"}
    ).json()
    assert audit["enabled"] is True
    assert any(entry["name"] == "gateway_ping" for entry in audit["entries"])
