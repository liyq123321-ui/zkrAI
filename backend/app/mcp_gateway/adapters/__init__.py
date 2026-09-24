"""Adapter layer for the MCP gateway."""

from app.mcp_gateway.adapters.base import (
    AdapterContext,
    BaseAdapter,
    ToolResult,
    image_content,
    resource_text_content,
    text_content,
)
from app.mcp_gateway.adapters.builtin import BuiltinAdapter
from app.mcp_gateway.adapters.external_mcp import ExternalMcpAdapter
from app.mcp_gateway.adapters.http_json import HttpJsonAdapter
from app.mcp_gateway.adapters.rag_knowledge import RagKnowledgeAdapter
from app.mcp_gateway.adapters.registry import AdapterRegistry

__all__ = [
    "AdapterContext",
    "AdapterRegistry",
    "BaseAdapter",
    "BuiltinAdapter",
    "ExternalMcpAdapter",
    "HttpJsonAdapter",
    "RagKnowledgeAdapter",
    "ToolResult",
    "image_content",
    "resource_text_content",
    "text_content",
]
