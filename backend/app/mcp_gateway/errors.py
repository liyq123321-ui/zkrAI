"""Unified JSON-RPC error model for the MCP gateway.

Business codes are documented in ``docs/mcp-gateway.md``:

- ``-32000`` unauthenticated   (no/invalid credential)
- ``-32001`` unauthorized      (agent is known but not allowed for this item)
- ``-32002`` invalid params    (JSON-Schema validation failed)
- ``-32003`` upstream unavailable
- ``-32004`` rate limited
- ``-32005`` skill/resource/prompt not found
"""

from __future__ import annotations

from typing import Any

# --- JSON-RPC 2.0 standard codes -------------------------------------------------
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS_STD = -32602
INTERNAL_ERROR = -32603

# --- gateway business codes ------------------------------------------------------
UNAUTHENTICATED = -32000
UNAUTHORIZED = -32001
INVALID_PARAMS = -32002
UPSTREAM_UNAVAILABLE = -32003
RATE_LIMITED = -32004
SKILL_NOT_FOUND = -32005


class McpError(Exception):
    """Domain error carrying a JSON-RPC code plus optional structured data."""

    def __init__(
        self,
        code: int,
        message: str,
        data: Any | None = None,
        http_status: int = 200,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data
        self.http_status = http_status

    def to_response(self, request_id: Any) -> dict[str, Any]:
        error: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.data is not None:
            error["data"] = self.data
        return {"jsonrpc": "2.0", "id": request_id, "error": error}


def error_response(
    request_id: Any,
    code: int,
    message: str,
    data: Any | None = None,
) -> dict[str, Any]:
    """Build a bare JSON-RPC error envelope."""

    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def ok_response(request_id: Any, result: Any) -> dict[str, Any]:
    """Build a JSON-RPC success envelope."""

    return {"jsonrpc": "2.0", "id": request_id, "result": result}
