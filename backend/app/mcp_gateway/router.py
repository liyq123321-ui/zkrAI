"""HTTP surface of the MCP gateway.

Mounted into the existing FastAPI backend (single process, port 8088):

* ``POST /mcp``        — Streamable HTTP endpoint (JSON or SSE response)
* ``GET  /mcp``        — optional server→client SSE keep-alive stream
* ``DELETE /mcp``      — end a session
* ``GET  /mcp-gateway/*`` — admin/management endpoints (admin token required)
"""

from __future__ import annotations

import asyncio
import hmac
import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from app.mcp_gateway.auth import mint_agent_token
from app.mcp_gateway.errors import PARSE_ERROR, error_response
from app.mcp_gateway.protocol import McpGateway

ADMIN_HEADER = "x-mcp-admin-token"


def _is_initialize(payload: Any) -> bool:
    if isinstance(payload, list):
        return any(
            isinstance(item, dict) and item.get("method") == "initialize"
            for item in payload
        )
    return isinstance(payload, dict) and payload.get("method") == "initialize"


async def _sse_stream(messages: list[dict[str, Any]]):
    """Emit every JSON-RPC message as one SSE ``message`` event."""

    for message in messages:
        yield f"event: message\ndata: {json.dumps(message, ensure_ascii=False)}\n\n"


def build_mcp_gateway_router(
    settings: Any | None = None,
    *,
    config_dir: str | None = None,
    gateway: McpGateway | None = None,
) -> APIRouter:
    jwt_secret = ""
    if settings is not None:
        jwt_secret = getattr(settings, "auth_jwt_secret", "") or ""
    runtime = gateway or McpGateway.build(jwt_secret=jwt_secret, config_dir=config_dir)

    router = APIRouter(tags=["mcp-gateway"])

    # ------------------------------------------------------------------ MCP ---
    @router.post("/mcp")
    async def mcp_endpoint(request: Request) -> Response:
        try:
            payload = await request.json()
        except Exception:  # noqa: BLE001 - malformed body
            return JSONResponse(
                status_code=400,
                content=error_response(None, PARSE_ERROR, "请求体不是合法 JSON。"),
            )

        accept = request.headers.get("accept", "")
        response = await runtime.handle(request.headers, payload)
        headers = dict(response.headers)
        if _is_initialize(payload) and runtime.config.settings.sessions_enabled:
            headers["Mcp-Session-Id"] = runtime.new_session()

        if response.body is None:  # notification / batch of notifications
            return Response(status_code=202, headers=headers)
        if "text/event-stream" in accept:
            messages = (
                [response.body]
                if isinstance(response.body, dict)
                else list(response.body)
            )
            return StreamingResponse(
                _sse_stream(messages),
                media_type="text/event-stream",
                headers=headers,
            )
        return JSONResponse(status_code=response.status, content=response.body, headers=headers)

    @router.get("/mcp")
    async def mcp_stream(request: Request) -> Response:
        if "text/event-stream" not in request.headers.get("accept", ""):
            return JSONResponse(
                status_code=405,
                content={
                    "error": "GET /mcp 仅支持 Accept: text/event-stream；"
                    "常规调用请使用 POST /mcp。"
                },
            )

        async def generator():
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    yield ": keep-alive\n\n"
                    await asyncio.sleep(15)
            except asyncio.CancelledError:  # client gone
                return

        return StreamingResponse(generator(), media_type="text/event-stream")

    @router.delete("/mcp")
    async def mcp_session_end(request: Request) -> Response:
        runtime.drop_session(
            request.headers.get("mcp-session-id") or request.headers.get("Mcp-Session-Id")
        )
        return Response(status_code=204)

    # ------------------------------------------------------------ management ---
    def require_admin(request: Request) -> None:
        token = request.headers.get(ADMIN_HEADER) or ""
        if not token:
            authorization = request.headers.get("authorization", "")
            token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
        expected = runtime.config.settings.admin_token
        if not expected:
            raise HTTPException(
                status_code=503,
                detail=(
                    "管理端点已禁用：请设置环境变量 "
                    f"{runtime.config.settings.admin_token_env}。"
                ),
            )
        if not token or not hmac.compare_digest(token, expected):
            raise HTTPException(status_code=401, detail="管理令牌无效。")

    @router.get("/mcp-gateway/healthz")
    def healthz() -> dict[str, Any]:
        settings_obj = runtime.config.settings
        return {
            "status": "ok",
            "endpoint": "POST /mcp",
            "transport": "streamable-http",
            "protocol_version": settings_obj.protocol_version,
            "auth_mode": settings_obj.auth_mode,
            "jwt_configured": bool(runtime.authenticator.jwt_configured),
            "static_tokens": runtime.authenticator.static_token_count,
            "agents": len(runtime.config.agents),
            "skills": len(runtime.config.skills),
            "ratelimit": {
                "enabled": settings_obj.ratelimit_enabled,
                "per_agent_per_minute": settings_obj.per_agent_per_minute,
            },
            "audit": {
                "enabled": settings_obj.audit_enabled,
                "dir": str(settings_obj.audit_dir),
            },
            "mock_upstream": settings_obj.mock_upstream,
        }

    @router.get("/mcp-gateway/agents")
    def list_agents(request: Request) -> dict[str, Any]:
        require_admin(request)
        return {
            "agents": [
                {
                    "id": policy.id,
                    "roles": list(policy.roles),
                    "enabled": policy.enabled,
                    "description": policy.description,
                    "rate_limit_per_minute": policy.rate_limit_per_minute
                    or runtime.config.settings.per_agent_per_minute,
                    "exposure": runtime.exposure.summary(policy),
                }
                for policy in runtime.config.agents.values()
            ]
        }

    @router.get("/mcp-gateway/skills")
    def list_skills(request: Request) -> dict[str, Any]:
        require_admin(request)
        return {
            "skills": [
                {
                    "id": skill.id,
                    "kind": skill.kind,
                    "name": skill.name,
                    "description": skill.description,
                    "adapter": skill.adapter,
                    "enabled": skill.enabled,
                    "uri_template": skill.uri_template,
                    # 注意：这里刻意不返回任何凭据或上游地址
                }
                for skill in runtime.config.skills
            ]
        }

    @router.get("/mcp-gateway/audit")
    def audit_tail(
        request: Request,
        limit: int = Query(default=50, ge=1, le=500),
    ) -> dict[str, Any]:
        require_admin(request)
        return {
            "enabled": runtime.config.settings.audit_enabled,
            "dir": str(runtime.config.settings.audit_dir),
            "last_error": runtime.auditor.last_error,
            "entries": runtime.auditor.tail(limit),
        }

    @router.post("/mcp-gateway/reload")
    def reload(request: Request, config_dir: str | None = Query(default=None)) -> dict[str, Any]:
        require_admin(request)
        config = runtime.reload(config_dir)
        return {
            "status": "ok",
            "agents": len(config.agents),
            "skills": len(config.skills),
            "mock_upstream": config.settings.mock_upstream,
        }

    @router.post("/mcp-gateway/tokens")
    async def mint_token(request: Request) -> dict[str, Any]:
        """Mint a gateway token for a registered agent (admin only)."""

        require_admin(request)
        body = await request.json() if request.headers.get("content-type") else {}
        body = body if isinstance(body, dict) else {}
        agent_id = str(body.get("agent_id") or "").strip()
        if agent_id not in runtime.config.agents:
            raise HTTPException(status_code=404, detail=f"agent '{agent_id}' 未注册。")
        secret = runtime.authenticator._jwt_secret
        if not secret:
            raise HTTPException(
                status_code=503,
                detail="未配置网关签名密钥（MCP_GATEWAY_JWT_SECRET / GATEWAY_JWT_SECRET）。",
            )
        roles = body.get("roles") or list(runtime.config.agents[agent_id].roles)
        ttl = int(body.get("ttl_seconds") or 43200)
        return {
            "agent_id": agent_id,
            "token": mint_agent_token(secret, agent_id, roles, ttl),
            "ttl_seconds": ttl,
            "header": f"Authorization: Bearer <token>",
        }

    return router


def build_router(settings: Any | None = None) -> APIRouter:
    """Convenience alias matching the other ``build_router`` factories."""

    return build_mcp_gateway_router(settings)
