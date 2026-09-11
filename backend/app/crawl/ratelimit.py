"""令牌桶 + 自适应退避。"""
from __future__ import annotations

import asyncio
import random
import time
from collections import defaultdict

from app.core.errors import RateLimitedError


class RateLimiter:
    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._next_ok: dict[str, float] = defaultdict(float)
        self._backoff: dict[str, float] = defaultdict(lambda: 1.0)

    async def wait(self, key: str, min_interval: float, jitter: float = 0.35) -> None:
        async with self._locks[key]:
            now = time.monotonic()
            due = self._next_ok[key]
            delay = max(0.0, due - now)
            extra = min_interval * random.uniform(0, jitter)
            await asyncio.sleep(delay + extra)
            self._next_ok[key] = time.monotonic() + min_interval
            self._backoff[key] = 1.0

    def penalize(self, key: str, seconds: float | None = None) -> None:
        current = self._backoff[key]
        nxt = seconds if seconds is not None else min(300.0, current * 2)
        self._backoff[key] = nxt
        self._next_ok[key] = max(self._next_ok[key], time.monotonic() + nxt)

    def from_error(self, key: str, error: RateLimitedError) -> None:
        self.penalize(key, error.retry_after_seconds)


rate_limiter = RateLimiter()
