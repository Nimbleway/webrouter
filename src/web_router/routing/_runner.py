"""The threads and loops a router runs on, and waiting on it within its deadline.

Experimental. See :mod:`web_router.routing.invoke`.
"""

from __future__ import annotations

import asyncio
import contextvars
import inspect
import threading
import time
from collections.abc import Awaitable, Callable
from concurrent.futures import Future
from concurrent.futures import wait as wait_futures
from contextlib import suppress
from typing import Any

from web_router.constants_invoke import MSG_ROUTER_OTHER_LOOP, ROUTER_THREAD_NAME
from web_router.constants_merge import ROUTER_ERROR, ROUTER_INVALID, ROUTER_TIMEOUT
from web_router.providers._deadline import current_deadline
from web_router.routing.contract import RouteRequest

_Side = Callable[[RouteRequest], Any]


class _LoopThread:
    """The private event loop an async router runs on when called from sync code: one, started on first use."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def get(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is None:
                loop = asyncio.new_event_loop()
                threading.Thread(target=loop.run_forever, name=ROUTER_THREAD_NAME, daemon=True).start()
                self._loop = loop
            return self._loop


_PRIVATE_LOOP = _LoopThread()


def _on_thread(fn: _Side, req: RouteRequest) -> Future[Any]:
    """Run a sync router on a thread of its own, so the caller can stop waiting at the deadline.

    It runs in a copy of the caller's context, so the call's deadline and route reach what it calls.
    """
    future: Future[Any] = Future()
    context = contextvars.copy_context()

    def run() -> None:
        if not future.set_running_or_notify_cancel():
            return
        try:
            future.set_result(fn(req))
        except (Exception, asyncio.CancelledError) as exc:  # the router's own failure, reported through the future
            future.set_exception(exc)

    threading.Thread(target=context.run, args=(run,), name=ROUTER_THREAD_NAME, daemon=True).start()
    return future


class InvalidAnswer(Exception):
    """Raised by a router whose answer arrived but cannot be read as a decision: recorded as ``router_invalid``.

    The ``remote`` router raises it for a body that is not a well-formed decision; any router may.
    """


class TimedOut(Exception):
    """Raised by a router whose own transport gave up at the deadline: recorded as ``router_timeout``.

    The ``remote`` router's HTTP timeout is the call's deadline, so it may fire just before the wait does.
    """


def _on_private_loop(awaitable: Awaitable[Any]) -> Future[Any]:
    """Await ``awaitable`` on the private loop.

    It runs in a copy of the caller's context, as :func:`_on_thread` runs: the loop
    schedules it with ``call_soon_threadsafe``, whose callback carries the context it
    was called in, and the task copies that.
    """
    return asyncio.run_coroutine_threadsafe(_awaited(awaitable), _PRIVATE_LOOP.get())


def _on_caller_loop(awaitable: Awaitable[Any]) -> asyncio.Future[Any]:
    """Schedule ``awaitable`` on the running loop, the async caller's own; a future of another loop is a ``ValueError``.

    Such a future can never be awaited here, and Python versions differ on when they say so.
    """
    if isinstance(awaitable, asyncio.Future) and awaitable.get_loop() is not asyncio.get_running_loop():
        raise ValueError(MSG_ROUTER_OTHER_LOOP)
    return asyncio.ensure_future(awaitable)


async def _awaited(awaitable: Awaitable[Any]) -> Any:
    return await awaitable


def _close(value: object) -> None:
    """Drop an awaitable nobody will await: close a coroutine; cancel a task, reading its error, on its own loop."""
    if inspect.iscoroutine(value):
        value.close()
    elif isinstance(value, asyncio.Future):
        with suppress(RuntimeError):  # its loop is closed: nothing left to run it
            value.get_loop().call_soon_threadsafe(_abandon, value)


def _abandon(task: asyncio.Future[Any]) -> None:
    """Cancel a task the call gave up on, and read its error once it ends, so it is not logged as never retrieved."""
    task.cancel()
    task.add_done_callback(_retrieved)


def _close_late(future: Future[Any]) -> None:
    """Close the coroutine a dropped sync router hands back after its deadline, whenever it does."""
    future.add_done_callback(lambda done: None if done.cancelled() or done.exception() else _close(done.result()))


def _retrieved(task: asyncio.Future[Any]) -> None:
    if not task.cancelled():
        task.exception()


def _classified(done: Future[Any] | asyncio.Future[Any]) -> tuple[str | None, Any]:
    """A finished router call: ``(None, result)``, or ``router_error`` and the error behind it.

    A router that cancelled itself is a failure of the router's, not the caller's.
    """
    if done.cancelled():
        return ROUTER_ERROR, None
    if (error := done.exception()) is not None:
        if isinstance(error, TimedOut):
            return ROUTER_TIMEOUT, error
        return (ROUTER_INVALID if isinstance(error, InvalidAnswer) else ROUTER_ERROR), error
    return None, done.result()


def _settled(future: Future[Any], seconds: float) -> tuple[str | None, Any]:
    """Wait up to ``seconds`` for ``future``: ``(None, result)``, or a failure code and the error behind it."""
    if seconds <= 0 or not wait_futures([future], timeout=seconds).done:
        future.cancel()  # cancels an async router; a sync one runs on, unheard
        _close_late(future)
        return ROUTER_TIMEOUT, None
    return _classified(future)


async def _asettled(task: asyncio.Future[Any], seconds: float) -> tuple[str | None, Any]:
    """:func:`_settled` from async code. A caller cancelled while waiting cancels the router and re-raises."""
    try:
        done, _ = await asyncio.wait({task}, timeout=seconds) if seconds > 0 else (set(), set())
    except asyncio.CancelledError:
        _abandon(task)
        raise
    if not done:
        _abandon(task)
        return ROUTER_TIMEOUT, None
    return _classified(task)


def _left(req: RouteRequest, started: float) -> float:
    """Seconds left to wait, read just before waiting: never past the call's deadline, however long setup took."""
    left = req.deadline_s - (time.perf_counter() - started)
    if (deadline := current_deadline()) is not None:
        left = min(left, deadline.left())
    return left
