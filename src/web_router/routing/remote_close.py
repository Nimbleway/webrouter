"""Closing the remote router's connection pool on the loop it belongs to, within a bounded wait.

:class:`PoolCloser` is the part of :class:`~.remote.RemoteRouter` that ``close()`` and ``aclose()`` run. Each
closes the pool on its own loop, waiting at most 2 s, and leaves the router closed: a later call is a router error
and opens nothing. A pool that does not close in time, or whose loop has ended, is dropped with a
``ResourceWarning``: ``aclose()`` the router before its loop ends.
"""

from __future__ import annotations

import asyncio
import threading
import warnings
import weakref
from collections.abc import Awaitable
from concurrent.futures import Future
from typing import TYPE_CHECKING

from web_router.constants_remote import (
    MSG_REMOTE_CLOSE_FAILED,
    REMOTE_CLOSE_WAIT_S,
    REMOTE_WHY_CLOSE_LOOP,
    REMOTE_WHY_CLOSE_SLOW,
)

if TYPE_CHECKING:
    import httpx


_CLOSING: set[asyncio.Task[None]] = set()
"""Pool closes ``close()`` started as tasks on the pool's own running loop, until each is done."""


def close_failed(host: str, why: str) -> None:
    """Record a pool that did not close: the library logs nothing, so it warns, as a leaked browser session does."""
    warnings.warn(MSG_REMOTE_CLOSE_FAILED.format(host=host, why=why), ResourceWarning, stacklevel=3)


def _why(exc: BaseException) -> str:
    """A close failure by its class only: an httpx error's text names the whole URL."""
    late = isinstance(exc, TimeoutError)
    return REMOTE_WHY_CLOSE_SLOW.format(wait=REMOTE_CLOSE_WAIT_S) if late else type(exc).__name__


class PoolCloser:
    """The close lifecycle of a router holding one httpx pool, bound to the event loop that made it."""

    host: str
    _lock: threading.Lock
    _closed: bool
    _pool: httpx.AsyncClient | None
    _owner: weakref.ref[asyncio.AbstractEventLoop] | None

    def close(self) -> None:
        """Close the pool on its own loop, waiting at most 2 s, and the router with it.

        From the pool's own running loop it cannot wait, so the close runs there as a task, bounded by the same
        2 s: use ``aclose()``.
        """
        pool, loop = self._shut()
        if pool is None or loop is None:
            return
        try:
            here: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            here = None
        if loop is here:
            task = loop.create_task(asyncio.wait_for(pool.aclose(), REMOTE_CLOSE_WAIT_S))
            _CLOSING.add(task)  # held until done: a loop keeps only a weak reference to a task
            task.add_done_callback(self._closed_task)
            return
        closing = self._submit(pool, loop)
        if closing is None:
            return
        try:
            closing.result(REMOTE_CLOSE_WAIT_S)
        except Exception as exc:
            closing.cancel()
            close_failed(self.host, _why(exc))

    async def aclose(self) -> None:
        """Close the pool and the router, 2 s at most: awaited here, or on its own loop without blocking this one."""
        pool, loop = self._shut()
        if pool is None or loop is None:
            return
        if loop is asyncio.get_running_loop():
            closing: Awaitable[None] = pool.aclose()
        else:
            submitted = self._submit(pool, loop)
            if submitted is None:
                return
            closing = asyncio.wrap_future(submitted)
        try:
            await asyncio.wait_for(closing, REMOTE_CLOSE_WAIT_S)  # cancels the close, on its own loop, when late
        except asyncio.CancelledError:
            close_failed(self.host, asyncio.CancelledError.__name__)  # the router stays closed; say so, then go
            raise
        except Exception as exc:
            close_failed(self.host, _why(exc))

    def _submit(self, pool: httpx.AsyncClient, loop: asyncio.AbstractEventLoop) -> Future[None] | None:
        """The pool's close, started on its own loop from another thread; ``None``, warned, once that loop stopped."""
        if loop.is_running():
            coro = pool.aclose()
            try:
                return asyncio.run_coroutine_threadsafe(coro, loop)
            except RuntimeError:  # the loop closed between the check and the hand-over
                coro.close()
        close_failed(self.host, REMOTE_WHY_CLOSE_LOOP)
        return None

    def _shut(self) -> tuple[httpx.AsyncClient | None, asyncio.AbstractEventLoop | None]:
        """Mark the router closed and take its pool, with its live loop: no call after this opens one.

        A pool whose loop has ended is dropped, warned: its sockets went with no close.
        """
        with self._lock:
            self._closed = True
            pool, owner = self._pool, self._owner
            self._pool = self._owner = None
        loop = None if owner is None else owner()
        if pool is not None and (loop is None or loop.is_closed()):
            close_failed(self.host, REMOTE_WHY_CLOSE_LOOP)
            return None, None
        return pool, loop

    def _closed_task(self, task: asyncio.Task[None]) -> None:
        _CLOSING.discard(task)
        failure = None if task.cancelled() else task.exception()
        if failure is not None:
            close_failed(self.host, _why(failure))
