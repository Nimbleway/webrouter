"""What a router lends the calls it makes: its cooldowns, and its cap on vendor calls in flight.

Held in a context variable for the length of each routed call, as the deadline
is, so the providers underneath read it without it appearing in their
signatures; a provider used on its own, outside any router, sees none and
behaves as it always has.

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
from contextlib import AbstractContextManager, asynccontextmanager, contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from web_router.constants import DELEGATING_PROVIDERS
from web_router.constants_production import MSG_INVALID_MAX_CONCURRENCY
from web_router.exceptions import DeadlineExceededError
from web_router.exceptions_typed import InvalidParameterError
from web_router.providers._cooldown import Cooldowns

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


@dataclass(frozen=True)
class Runtime:
    """One router's cooldowns and cap, lent to the calls it makes."""

    cooldowns: Cooldowns = field(default_factory=Cooldowns)
    limiter: Limiter | None = None


_RUNTIME: ContextVar[Runtime | None] = ContextVar("web_router_runtime", default=None)
_HOLDING: ContextVar[bool] = ContextVar("web_router_holding_slot", default=False)


@contextmanager
def running(runtime: Runtime) -> Iterator[None]:
    """Lend ``runtime`` to the calls made inside the block."""
    token = _RUNTIME.set(runtime)
    try:
        yield
    finally:
        _RUNTIME.reset(token)


def lent(runtime: Runtime | None) -> AbstractContextManager[None]:
    """:func:`running` for a router's runtime; nothing for a router that has none."""
    return nullcontext() if runtime is None else running(runtime)


@contextmanager
def noting(runtime: Runtime | None, backend: object) -> Iterator[None]:
    """Start a named backend's cooldown when its call is throttled; ``auto``'s members are noted by their hops."""
    name = getattr(backend, "name", None)
    if runtime is None or not isinstance(name, str) or name in DELEGATING_PROVIDERS:
        yield
        return
    try:
        yield
    except Exception as exc:
        runtime.cooldowns.note(name, exc)
        raise


def current_runtime() -> Runtime | None:
    """The runtime of the router whose call is in flight, or ``None`` outside any router."""
    return _RUNTIME.get()


def _limiter_for(provider: str) -> Limiter | None:
    """The cap ``provider``'s call is held to: none for a delegating provider, or a call already holding a slot."""
    runtime = _RUNTIME.get()
    if runtime is None or runtime.limiter is None or provider in DELEGATING_PROVIDERS or _HOLDING.get():
        return None
    return runtime.limiter


@contextmanager
def slot(provider: str, deadline: Deadline | None) -> Iterator[None]:
    """Hold one of the router's slots while ``provider`` calls its vendor, waiting no later than ``deadline``."""
    limiter = _limiter_for(provider)
    if limiter is None:
        yield
        return
    with limiter.held(provider, deadline):
        token = _HOLDING.set(True)
        try:
            yield
        finally:
            _HOLDING.reset(token)


@asynccontextmanager
async def aslot(provider: str, deadline: Deadline | None) -> AsyncIterator[None]:
    """Async counterpart of :func:`slot`."""
    limiter = _limiter_for(provider)
    if limiter is None:
        yield
        return
    async with limiter.aheld(provider, deadline):
        token = _HOLDING.set(True)
        try:
            yield
        finally:
            _HOLDING.reset(token)
