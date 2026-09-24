"""Standalone runner: same gateway, own process/port.

    set MCP_GATEWAY_PORT=8099
    .venv\\Scripts\\python.exe -m app.mcp_gateway.standalone

Prefer the mounted mode (``POST http://127.0.0.1:8088/mcp``); use this only
when the gateway must be isolated from the main backend process.
"""

from __future__ import annotations

import os

import uvicorn
from fastapi import FastAPI

from app.mcp_gateway.protocol import McpGateway
from app.mcp_gateway.router import build_mcp_gateway_router


def create_app() -> FastAPI:
    gateway = McpGateway.build()
    app = FastAPI(title="firstflight-mcp-gateway", version="0.1.0")
    app.state.mcp_gateway = gateway
    app.include_router(build_mcp_gateway_router(gateway=gateway))

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


def main() -> None:
    port = int(os.getenv("MCP_GATEWAY_PORT", "8099"))
    host = os.getenv("MCP_GATEWAY_HOST", "127.0.0.1")
    uvicorn.run(create_app(), host=host, port=port, log_level="info")


if __name__ == "__main__":  # pragma: no cover
    main()
