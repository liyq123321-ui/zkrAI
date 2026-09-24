"""Self-operated MCP gateway for firstFlight.

Exposes approved tools / resources / prompts to AI agents over MCP
Streamable HTTP,裁剪 by caller identity, and proxies calls to the backing
services while keeping credentials server-side.
"""

from app.mcp_gateway.protocol import McpGateway
from app.mcp_gateway.router import build_mcp_gateway_router, build_router

__all__ = ["McpGateway", "build_mcp_gateway_router", "build_router"]
