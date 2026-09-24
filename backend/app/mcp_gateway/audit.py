"""Structured audit logging for gateway calls.

Every ``tools/call`` / ``resources/read`` / ``prompts/get`` is recorded as a
JSON line under ``<audit_dir>/audit-YYYY-MM-DD.jsonl`` and mirrored into a
small in-memory ring buffer exposed by the admin endpoints.

Argument summaries keep the key names and types only. Values that look like
credentials are redacted, everything else is truncated.
"""

from __future__ import annotations

import json
import re
import time
from collections import deque
from pathlib import Path
from typing import Any, Mapping

_SECRET_HINT = re.compile(
    r"(api[_-]?key|token|secret|password|passwd|authorization|credential|cookie|signature)",
    re.IGNORECASE,
)


def summarize_value(value: Any, preview_chars: int = 200) -> Any:
    if isinstance(value, Mapping):
        return summarize_args(value, preview_chars)
    if isinstance(value, (list, tuple)):
        items = [summarize_value(v, preview_chars) for v in value[:10]]
        return {"type": "array", "size": len(value), "items": items}
    if isinstance(value, str):
        text = value if len(value) <= preview_chars else value[:preview_chars] + "…"
        return {"type": "string", "chars": len(value), "preview": text}
    if isinstance(value, (int, float, bool)) or value is None:
        return {"type": type(value).__name__, "value": value}
    return {"type": type(value).__name__}


def summarize_args(args: Any, preview_chars: int = 200) -> Any:
    """Return a redacted, truncated description of the call arguments."""

    if not isinstance(args, Mapping):
        return summarize_value(args, preview_chars)
    summary: dict[str, Any] = {}
    for key, value in args.items():
        if _SECRET_HINT.search(str(key)):
            summary[str(key)] = "***redacted***"
        else:
            summary[str(key)] = summarize_value(value, preview_chars)
    return summary


class Auditor:
    def __init__(
        self,
        enabled: bool = True,
        log_dir: Path | str | None = None,
        memory_size: int = 200,
        preview_chars: int = 200,
    ) -> None:
        self.enabled = enabled
        self.log_dir = Path(log_dir) if log_dir else None
        self.preview_chars = preview_chars
        self._buffer: deque[dict[str, Any]] = deque(maxlen=max(memory_size, 1))
        self._last_error: str | None = None

    def _path(self) -> Path | None:
        if self.log_dir is None:
            return None
        return self.log_dir / f"audit-{time.strftime('%Y-%m-%d')}.jsonl"

    def record(
        self,
        *,
        agent_id: str,
        method: str,
        name: str,
        args: Any = None,
        status: str = "ok",
        result_size: int | None = None,
        latency_ms: float | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "agent_id": agent_id,
            "method": method,
            "name": name,
            "args_summary": summarize_args(args or {}, self.preview_chars),
            "result_size": result_size,
            "latency_ms": round(latency_ms, 2) if latency_ms is not None else None,
            "status": status,
        }
        if error:
            entry["error"] = error
        self._buffer.append(entry)
        if self.enabled and self.log_dir is not None:
            try:
                self.log_dir.mkdir(parents=True, exist_ok=True)
                with self._path().open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
            except OSError as exc:  # never let auditing break a call
                self._last_error = str(exc)
        return entry

    def tail(self, limit: int = 50) -> list[dict[str, Any]]:
        items = list(self._buffer)
        return items[-max(limit, 1):] if limit else items

    @property
    def last_error(self) -> str | None:
        return self._last_error
