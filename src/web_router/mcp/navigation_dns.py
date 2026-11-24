"""The navigation guard's short-lived DNS answers, apart from the guard's routing rules."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

MCP_BROWSER_DNS_CACHE_TTL_S = 1.0
"""How long one resolved answer is reused before the name is looked up again.

The browser does its own lookup once the route lets a request through, so an
answer is held only briefly: a name rebound in between must not be served a
stale allow.
"""

MCP_BROWSER_DNS_CACHE_MAX = 128
"""Most names one guard keeps; past that the oldest answer is dropped."""


class ResolutionCache:
    """Short-lived answers per (host, port), one lookup in flight per key, at most MCP_BROWSER_DNS_CACHE_MAX kept."""

    def __init__(self) -> None:
        self._answers: dict[tuple[str, int | None], tuple[float, str | None]] = {}
        self._pending: dict[tuple[str, int | None], asyncio.Task[str | None]] = {}

    async def get(self, key: tuple[str, int | None], compute: Callable[[], Awaitable[str | None]]) -> str | None:
        """The stored answer for ``key``, or the one ``compute`` gives, looked up once per key."""
        cached = self._answers.get(key)
        if cached is not None and cached[0] > time.monotonic():
            # A short-lived answer is safe to reuse and saves a slow lookup.
            return cached[1]
        lookup = self._pending.get(key)
        if lookup is None:
            lookup = asyncio.ensure_future(self._lookup(key, compute))
            self._pending[key] = lookup
        return await lookup

    async def _lookup(self, key: tuple[str, int | None], compute: Callable[[], Awaitable[str | None]]) -> str | None:
        try:
            reason = await compute()
            self._answers[key] = (time.monotonic() + MCP_BROWSER_DNS_CACHE_TTL_S, reason)
            if len(self._answers) > MCP_BROWSER_DNS_CACHE_MAX:
                # Drop the oldest name: a busy page must not grow the cache forever.
                del self._answers[next(iter(self._answers))]
            return reason
        finally:
            self._pending.pop(key, None)
