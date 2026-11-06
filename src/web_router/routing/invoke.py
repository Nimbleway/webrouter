"""Attaching a router to the routers and calling it, sync or async, within its deadline.

Experimental. A router is the caller's code, in one of two forms:

* a function or coroutine function, ``router(req) -> RouteDecision``;
* an object with ``route(req)`` or ``aroute(req)``, or both.

web-router adapts the side the author did not write. A sync router runs on a
worker thread, from sync and async code alike, so the call can stop waiting at
the deadline; Python cannot interrupt a thread, so a router still running then
keeps running, and its answer is dropped. An async router called from sync
code runs on a private event-loop thread and is cancelled at the deadline. A
function that does not look async but answers with a coroutine -- an async
router behind a plain decorator -- has that coroutine awaited the same way,
within what is left of the same deadline, and closed if it never runs.

A router declares its time limit as a ``timeout_s`` attribute, on the function
or the object; without one it has 50 ms. The time it gets is the smaller of
that and the time the call has left. A router that raises, runs out of time or
answers something other than a :class:`RouteDecision` leaves the call on the
static order, and the decision record says why; with ``fail_on_router_error``
the call raises :class:`~web_router.RouterError` instead.

The router is shown the :class:`RouteRequest` and nothing else: the prompt,
and candidates named ``tool:provider``. Those ids show which backends are
configured; no key value ever reaches a router.
"""

from __future__ import annotations

import asyncio
import contextvars
import inspect
import math
import threading
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from concurrent.futures import Future
from concurrent.futures import wait as wait_futures
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Protocol

from web_router.constants_invoke import (
    MSG_ROUTER_CLASS,
    MSG_ROUTER_FORM,
    MSG_ROUTER_TIMEOUT_S,
    ROUTER_ERROR,
    ROUTER_INVALID,
    ROUTER_THREAD_NAME,
    ROUTER_TIMEOUT,
    ROUTER_TIMEOUT_ATTR,
    ROUTER_TIMEOUT_S,
)
from web_router.exceptions import ConfigurationError, RouterError
from web_router.providers._deadline import current_deadline
from web_router.routing.contract import Budget, RouteDecision, RouteRequest


class SyncRouter(Protocol):
    """A router object that answers synchronously."""

    def route(self, req: RouteRequest) -> RouteDecision: ...


class AsyncRouter(Protocol):
    """A router object that answers asynchronously."""

    async def aroute(self, req: RouteRequest) -> RouteDecision: ...


RouterLike = (
    Callable[[RouteRequest], RouteDecision]
    | Callable[[RouteRequest], Awaitable[RouteDecision]]
    | SyncRouter
    | AsyncRouter
)
"""What ``router=`` takes: a function or coroutine function, or an object with ``route`` or ``aroute``."""

_Side = Callable[[RouteRequest], Any]


@dataclass(frozen=True)
class RouterOutcome:
    """What one call to a router came to: its decision, or why there is none, and how long it took."""

    decision: RouteDecision | None
    """The router's answer; ``None`` when it failed."""

    failure: str | None
    """``router_timeout``, ``router_error`` or ``router_invalid`` when it failed; ``None`` when it answered."""

    elapsed_ms: float
    """Milliseconds from the call to its answer, or to giving up on it."""


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


def _on_private_loop(awaitable: Awaitable[Any]) -> Future[Any]:
    """Await ``awaitable`` on the private loop.

    It runs in a copy of the caller's context, as :func:`_on_thread` runs: the loop
    schedules it with ``call_soon_threadsafe``, whose callback carries the context it
    was called in, and the task copies that.
    """
    return asyncio.run_coroutine_threadsafe(_awaited(awaitable), _PRIVATE_LOOP.get())


def _on_caller_loop(awaitable: Awaitable[Any]) -> asyncio.Future[Any]:
    """Schedule ``awaitable`` on the running loop, the async caller's own."""
    return asyncio.ensure_future(awaitable)


async def _awaited(awaitable: Awaitable[Any]) -> Any:
    return await awaitable


def _sides(router: object) -> tuple[_Side | None, _Side | None]:
    """The router's sync and async sides; either may be missing, and a non-callable such as a string has neither."""
    route = getattr(router, "route", None)
    aroute = getattr(router, "aroute", None)
    if callable(route) or callable(aroute):
        sync_fn = route if callable(route) and not inspect.iscoroutinefunction(route) else None
        async_fn = aroute if callable(aroute) else (route if callable(route) and sync_fn is None else None)
        return sync_fn, async_fn
    if not callable(router):
        return None, None
    dunder_call = type(router).__call__  # a callable instance's own method
    if inspect.iscoroutinefunction(router) or inspect.iscoroutinefunction(dunder_call):
        return None, router
    return router, None


def _declared_timeout(router: object) -> float:
    declared = getattr(router, ROUTER_TIMEOUT_ATTR, None)
    if declared is None:
        return ROUTER_TIMEOUT_S
    if isinstance(declared, bool) or not isinstance(declared, (int, float)) or not 0 < declared < math.inf:
        raise ConfigurationError(MSG_ROUTER_TIMEOUT_S.format(value=declared))
    return float(declared)


def _valid(result: object) -> bool:
    """Whether ``result`` is a :class:`RouteDecision` whose ``providers``, when set, map tools to lists of names."""
    if not isinstance(result, RouteDecision):
        return False
    if result.providers is None:
        return True
    return isinstance(result.providers, Mapping) and all(
        isinstance(ranked, Sequence) and not isinstance(ranked, str) and all(isinstance(p, str) for p in ranked)
        for ranked in result.providers.values()
    )


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
        return ROUTER_ERROR, error
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


class AttachedRouter:
    """A router given to ``router=``, adapted to be called from sync and async code alike, within its deadline."""

    def __init__(self, router: RouterLike, *, fail_on_error: bool = False) -> None:
        """
        Raises:
            ConfigurationError: for anything but the two forms -- a string or a class included -- or a
                ``timeout_s`` that is not a positive number of seconds.
        """
        if isinstance(router, type):
            raise ConfigurationError(MSG_ROUTER_CLASS.format(name=router.__name__))
        self._sync, self._async = _sides(router)
        if self._sync is None and self._async is None:
            raise ConfigurationError(MSG_ROUTER_FORM.format(kind=type(router).__name__))
        self.timeout_s = _declared_timeout(router)
        self.fail_on_error = fail_on_error
        self.name: str = getattr(router, "__qualname__", None) or type(router).__qualname__

    def deadline(self, budget: Budget | None) -> float:
        """Seconds the router gets: its ``timeout_s``, cut to the call's deadline and its budget's time limit."""
        limits = [self.timeout_s]
        if budget is not None and budget.time_limit_s is not None:
            limits.append(budget.time_limit_s)
        if (deadline := current_deadline()) is not None:
            limits.append(deadline.left())
        return max(min(limits), 0.0)

    def call(self, req: RouteRequest) -> RouterOutcome:
        """Ask the router from sync code, waiting at most ``req.deadline_s``."""
        started = time.perf_counter()
        if req.deadline_s <= 0:
            return self._failed(ROUTER_TIMEOUT, started)
        try:
            future = _on_thread(self._sync, req) if self._sync is not None else _on_private_loop(self._coroutine(req))
        except Exception as exc:  # the router failed before it could be waited on
            return self._failed(ROUTER_ERROR, started, exc)
        code, value = _settled(future, self._left(req, started))
        pending = self._pending(code, value, req, started)
        if isinstance(pending, RouterOutcome):
            return pending
        if pending:
            scheduled = _on_private_loop(value)
            code, value = _settled(scheduled, self._left(req, started))  # measured after scheduling
        return self._outcome(code, value, started)

    async def acall(self, req: RouteRequest) -> RouterOutcome:
        """Ask the router from async code, waiting at most ``req.deadline_s``."""
        started = time.perf_counter()
        if req.deadline_s <= 0:
            return self._failed(ROUTER_TIMEOUT, started)
        on_thread: Future[Any] | None = None
        try:
            if self._async is not None:
                task: asyncio.Future[Any] = asyncio.ensure_future(self._coroutine(req))
            else:
                assert self._sync is not None  # one side is always set
                on_thread = _on_thread(self._sync, req)
                task = asyncio.wrap_future(on_thread)
        except Exception as exc:  # the router failed before it could be waited on
            return self._failed(ROUTER_ERROR, started, exc)
        try:
            code, value = await _asettled(task, self._left(req, started))
        except asyncio.CancelledError:
            if on_thread is not None:
                _close_late(on_thread)
            raise
        if code == ROUTER_TIMEOUT and on_thread is not None:
            _close_late(on_thread)
        pending = self._pending(code, value, req, started)
        if isinstance(pending, RouterOutcome):
            return pending
        if pending:
            scheduled_task = _on_caller_loop(value)
            code, value = await _asettled(scheduled_task, self._left(req, started))  # measured after scheduling
        return self._outcome(code, value, started)

    def _pending(self, code: str | None, value: Any, req: RouteRequest, started: float) -> RouterOutcome | bool:
        """Whether a sync-looking router answered with a coroutine to await, with time left to await it.

        The timeout's outcome, the coroutine closed unrun, when no time is left.
        """
        if code is not None or not inspect.isawaitable(value):
            return False
        if self._left(req, started) > 0:
            return True
        _close(value)
        return self._failed(ROUTER_TIMEOUT, started)

    def _outcome(self, code: str | None, value: Any, started: float) -> RouterOutcome:
        return self._failed(code, started, value) if code is not None else self._read(value, started)

    @staticmethod
    def _left(req: RouteRequest, started: float) -> float:
        """Seconds left to wait, read just before waiting: never past the call's deadline, however long setup took."""
        left = req.deadline_s - (time.perf_counter() - started)
        if (deadline := current_deadline()) is not None:
            left = min(left, deadline.left())
        return left

    async def _coroutine(self, req: RouteRequest) -> Any:
        assert self._async is not None  # called only when the router has an async side
        return await self._async(req)

    def _read(self, result: object, started: float) -> RouterOutcome:
        if not isinstance(result, RouteDecision) or not _valid(result):
            _close(result)
            return self._failed(ROUTER_INVALID, started)
        return RouterOutcome(decision=result, failure=None, elapsed_ms=(time.perf_counter() - started) * 1000)

    def _failed(self, code: str, started: float, error: Any = None) -> RouterOutcome:
        if self.fail_on_error:
            raise RouterError(self.name, code) from error
        return RouterOutcome(decision=None, failure=code, elapsed_ms=(time.perf_counter() - started) * 1000)
