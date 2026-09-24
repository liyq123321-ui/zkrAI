"""Per-agent sliding-window rate limiting (in-memory, single process)."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass

WINDOW_SECONDS = 60.0


@dataclass(frozen=True)
class RateDecision:
    allowed: bool
    limit: int
    used: int
    remaining: int
    retry_after: float


class SlidingWindowLimiter:
    """Count calls per agent inside a 60s window.

    The backend runs as a single worker (see ``docs/backend/README.md``), so an
    in-memory map is sufficient for v1. Swap in Redis later by implementing the
    same :meth:`check` contract.
    """

    def __init__(self, default_limit: int = 60, enabled: bool = True) -> None:
        self._default_limit = max(int(default_limit), 0)
        self._enabled = enabled
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    @property
    def enabled(self) -> bool:
        return self._enabled and self._default_limit > 0

    def _prune(self, bucket: deque[float], now: float) -> None:
        while bucket and (now - bucket[0]) >= WINDOW_SECONDS:
            bucket.popleft()

    def check(self, agent_id: str, limit: int | None = None) -> RateDecision:
        quota = self._default_limit if limit is None else max(int(limit), 0)
        now = time.monotonic()
        bucket = self._hits[agent_id]
        self._prune(bucket, now)
        used = len(bucket)
        if not self._enabled or quota <= 0:
            bucket.append(now)
            return RateDecision(True, quota, used + 1, max(quota - used - 1, 0), 0.0)
        if used >= quota:
            return RateDecision(
                False, quota, used, 0, max(WINDOW_SECONDS - (now - bucket[0]), 0.0)
            )
        bucket.append(now)
        return RateDecision(True, quota, used + 1, max(quota - used - 1, 0), 0.0)

    def reset(self, agent_id: str | None = None) -> None:
        if agent_id is None:
            self._hits.clear()
        else:
            self._hits.pop(agent_id, None)
