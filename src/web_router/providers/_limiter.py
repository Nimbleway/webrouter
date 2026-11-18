"""A router's cap on vendor calls in flight (``max_concurrency``).

The cap counts vendor calls, not router calls: one extract of twenty URLs
through a one-URL-per-call backend takes up to twenty slots, one at a time, and
an ``auto`` chain takes a slot per member it tries. A delegating provider
(``auto``, ``max``) takes none of its own. The sync router's cap is one
semaphore across its threads; the async router's is one per event loop, since
an asyncio semaphore belongs to one loop.
"""

from __future__ import annotations

import asyncio
import threading
import weakref
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import TYPE_CHECKING

from web_router.constants_production import MSG_INVALID_MAX_CONCURRENCY
from web_router.exceptions import DeadlineExceededError
from web_router.exceptions_typed import InvalidParameterError

if TYPE_CHECKING:
    from web_router.providers._deadline import Deadline


class Limiter:
    """At most ``limit`` vendor calls in flight: one semaphore for threads, one per event loop for tasks."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self._threads = threading.BoundedSemaphore(limit)
        self._loops: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = (
            weakref.WeakKeyDictionary()
        )
        self._lock = threading.Lock()

    def _for_loop(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        with self._lock:
            if loop not in self._loops:
                self._loops[loop] = asyncio.Semaphore(self.limit)
            return self._loops[loop]

    @contextmanager
    def held(self, provider: str, deadline: Deadline | None) -> Iterator[None]:
        """A slot for one call, waited for no longer than ``deadline`` allows."""
        acquired = self._threads.acquire(timeout=None if deadline is None else max(0.0, deadline.left()))
        if not acquired:
            assert deadline is not None  # an unbounded acquire always succeeds
            raise DeadlineExceededError(provider, deadline.seconds)
        try:
            yield
        finally:
            self._threads.release()

    @asynccontextmanager
    async def aheld(self, provider: str, deadline: Deadline | None) -> AsyncIterator[None]:
        """Async counterpart of :meth:`held`, on this loop's semaphore."""
        semaphore = self._for_loop()
        try:
            await asyncio.wait_for(semaphore.acquire(), None if deadline is None else max(0.0, deadline.left()))
        except TimeoutError:
            assert deadline is not None
            raise DeadlineExceededError(provider, deadline.seconds) from None
        try:
            yield
        finally:
            semaphore.release()


def checked_limit(value: int | None) -> Limiter | None:
    """A :class:`Limiter` for ``max_concurrency``; ``None`` caps nothing.

    Raises:
        InvalidParameterError: for anything but a whole number of 1 or more (a bool included).
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise InvalidParameterError("max_concurrency", MSG_INVALID_MAX_CONCURRENCY.format(value=value))
    return Limiter(value)
