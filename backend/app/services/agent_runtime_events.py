"""Persist the public subset of Codex CLI JSONL runtime events."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from sqlalchemy.orm import Session

from app.database.models import AgentCall, AgentRuntimeEvent


logger = logging.getLogger(__name__)

_SENSITIVE_KEY = re.compile(
    r"(?:authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret|cookie)",
    re.IGNORECASE,
)
_SECRET_TEXT = (
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)((?:api[_-]?key|token|password|secret)\s*[:=]\s*)[^\s,;]+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
)
_MAX_TEXT = 64_000
_MAX_PAYLOAD = 512_000
@dataclass(frozen=True)
class AgentRuntimeContext:
    project_id: str
    agent_session_id: str
    agent_call_id: str
    operation: str


_runtime_context: ContextVar[AgentRuntimeContext | None] = ContextVar(
    "agent_runtime_context", default=None
)


@contextmanager
def bind_agent_runtime_context(
    context: AgentRuntimeContext | None,
) -> Iterator[None]:
    token = _runtime_context.set(context)
    try:
        yield
    finally:
        _runtime_context.reset(token)


def current_agent_runtime_context() -> AgentRuntimeContext | None:
    return _runtime_context.get()


def _redact_text(value: str, limit: int = _MAX_TEXT) -> str:
    safe = value
    for pattern in _SECRET_TEXT:
        safe = pattern.sub(lambda match: f"{match.group(1) if match.lastindex else ''}[REDACTED]", safe)
    if len(safe) > limit:
        safe = safe[:limit] + "…"
    return safe


def _safe_value(value: object, *, key: str | None = None, depth: int = 0) -> object:
    if key is not None and _SENSITIVE_KEY.search(key):
        return "[REDACTED]"
    if depth >= 6:
        return "[TRUNCATED]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, Mapping):
        return {
            str(child_key): _safe_value(child_value, key=str(child_key), depth=depth + 1)
            for child_key, child_value in list(value.items())[:100]
        }
    if isinstance(value, (list, tuple)):
        return [_safe_value(item, depth=depth + 1) for item in value[:100]]
    return _redact_text(str(value))


def _first_text(item: Mapping[str, object], *keys: str) -> str | None:
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return _redact_text(value.strip())
        if isinstance(value, list):
            parts = [str(part).strip() for part in value if str(part).strip()]
            if parts:
                return _redact_text("\n".join(parts))
    return None


def _event_view(event: Mapping[str, object]) -> tuple[str | None, str | None, str, str | None, dict[str, object]]:
    event_type = str(event.get("type") or "unknown")
    raw_item = event.get("item")
    item = raw_item if isinstance(raw_item, Mapping) else {}
    item_type_value = item.get("type")
    item_type = str(item_type_value) if isinstance(item_type_value, str) else None
    status_value = item.get("status", event.get("status"))
    status = str(status_value) if isinstance(status_value, str) else None

    titles = {
        "thread.started": "Codex CLI 会话已连接",
        "turn.started": "开始生成与推理",
        "turn.completed": "本轮生成完成",
        "turn.failed": "本轮生成失败",
        "error": "Codex CLI 报告错误",
        "attempt.started": "开始一次 Agent 生成尝试",
        "validation.warning": "结构化结果校验未通过，准备自动修复",
        "runtime.error": "Agent 运行失败",
        "stderr": "Codex 运行诊断",
        "stdout": "Codex 标准输出",
        "structured_output": "Codex 最终结构化输出",
        "agent_message": "Agent 消息",
        "reasoning": "推理摘要",
        "command_execution": "命令执行",
        "file_change": "文件修改",
        "mcp_tool_call": "MCP 工具调用",
        "web_search": "网页搜索",
        "plan_update": "计划更新",
    }
    title = titles.get(item_type or event_type, item_type or event_type)

    if item_type == "reasoning":
        detail = _first_text(item, "text", "summary", "content")
    elif item_type == "agent_message":
        detail = _first_text(item, "text", "message", "content")
    elif item_type == "command_execution":
        detail = _first_text(item, "command")
    elif item_type == "file_change":
        detail = _first_text(item, "path", "file", "summary")
    elif item_type == "mcp_tool_call":
        server = _first_text(item, "server", "server_name")
        tool = _first_text(item, "tool", "tool_name", "name")
        detail = " / ".join(part for part in (server, tool) if part) or None
    elif item_type == "web_search":
        detail = _first_text(item, "query", "text")
    elif item_type == "plan_update":
        detail = _first_text(item, "text", "summary", "explanation")
    elif event_type in {
        "error",
        "attempt.started",
        "validation.warning",
        "runtime.error",
        "stderr",
        "stdout",
        "structured_output",
    }:
        detail = _first_text(event, "message", "error", "text")
    else:
        detail = _first_text(item, "text", "summary", "message")

    safe_payload = _safe_value(dict(event))
    assert isinstance(safe_payload, dict)
    encoded = json.dumps(safe_payload, ensure_ascii=False, separators=(",", ":"))
    if len(encoded) > _MAX_PAYLOAD:
        safe_payload = {
            "type": event_type,
            "item": {
                "type": item_type,
                "status": status,
                "summary": "事件内容过长，已截断。",
            },
        }
    return item_type, status, title[:200], detail, safe_payload


class AgentRuntimeEventStore:
    """Resolve Agent calls and append sanitized runtime events without breaking work."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def resolve(self, operation: str, request: Mapping[str, object]) -> AgentRuntimeContext | None:
        try:
            with self._session_factory() as db:
                calls = (
                    db.query(AgentCall)
                    .filter_by(operation=operation, status="PENDING")
                    .order_by(AgentCall.started_at.desc(), AgentCall.id.desc())
                    .all()
                )
                expected = dict(request)
                call = next((candidate for candidate in calls if candidate.request == expected), None)
                if call is None:
                    return None
                return AgentRuntimeContext(
                    project_id=call.project_id,
                    agent_session_id=call.agent_session_id,
                    agent_call_id=call.id,
                    operation=call.operation,
                )
        except Exception:
            logger.exception("Could not resolve Agent runtime context")
            return None

    def append(self, context: AgentRuntimeContext, event: Mapping[str, object]) -> None:
        try:
            item_type, status, title, detail, payload = _event_view(event)
            with self._session_factory() as db:
                db.add(
                    AgentRuntimeEvent(
                        project_id=context.project_id,
                        agent_session_id=context.agent_session_id,
                        agent_call_id=context.agent_call_id,
                        operation=context.operation,
                        event_type=str(event.get("type") or "unknown")[:100],
                        item_type=item_type[:100] if item_type else None,
                        status=status[:100] if status else None,
                        title=title,
                        detail=detail,
                        payload=payload,
                    )
                )
                db.commit()
        except Exception:
            logger.exception("Could not persist a Codex runtime event")
