"""Attaching a router to the routers and calling it, sync or async, within its deadline.

Experimental. A router is the caller's code, in one of two forms:

* a function or coroutine function, ``router(req) -> RouteDecision``;
* an object with ``route(req)`` or ``aroute(req)``, or both.

web-router adapts the side the author did not write. A sync router runs on a worker thread, from
sync and async code alike, so the call can stop waiting at the deadline; Python cannot interrupt a
thread, so a router still running then keeps running, and its answer is dropped. An async router
called from sync code runs on a private event-loop thread and is cancelled at the deadline. A
function that does not look async but answers with a coroutine -- an async router behind a plain
decorator -- has it awaited the same way, within what is left of the deadline, or closed unrun.

A router declares its time limit as a ``timeout_s`` attribute, on the function
or the object; without one it has 50 ms. The time it gets is the smaller of
that and the time the call has left. A router that raises, runs out of time or
answers something other than a :class:`RouteDecision` leaves the call on the
static order, and the decision record says why; with ``fail_on_router_error``
the call raises :class:`~web_router.RouterError` instead, as it does when a
composite router's members all fail or answer empty.

The router is shown the :class:`RouteRequest` alone: the prompt, and candidates named
``tool:provider``, which show the backends configured; no key value reaches a router.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Any, Protocol

from web_router.constants_invoke import MSG_ROUTER_CLASS, MSG_ROUTER_FORM
from web_router.constants_merge import ROUTER_EMPTY, ROUTER_ERROR, ROUTER_INVALID, ROUTER_TIMEOUT
from web_router.exceptions import ConfigurationError, RouterError
from web_router.providers._deadline import current_deadline
from web_router.routing._forms import _declared_timeout, _name_of, _sides
from web_router.routing._runner import (
    _asettled,
    _close,
    _close_late,
    _left,
    _on_caller_loop,
    _on_private_loop,
    _on_thread,
    _settled,
)
from web_router.routing.composite import none_answered
from web_router.routing.contract import Budget, RouteDecision, RouteRequest
from web_router.routing.merge_shape import well_formed


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


@dataclass(frozen=True)
class RouterOutcome:
    """What one call to a router came to: its decision, or why there is none, and how long it took."""

    decision: RouteDecision | None
    """The router's answer; ``None`` when it failed."""

    failure: str | None
    """``router_timeout``, ``router_error`` or ``router_invalid`` when it failed; ``None`` when it answered."""

    elapsed_ms: float
    """Milliseconds from the call to its answer, or to giving up on it."""


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
        self.name: str = _name_of(router)

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
        code, value = _settled(future, _left(req, started))
        if self._answered_awaitable(code, value):
            if (early := self._expired(value, req, started)) is not None:
                return early
            scheduled = _on_private_loop(value)
            code, value = _settled(scheduled, _left(req, started))  # measured after scheduling
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
            code, value = await _asettled(task, _left(req, started))
        except asyncio.CancelledError:
            if on_thread is not None:
                _close_late(on_thread)
            raise
        if code == ROUTER_TIMEOUT and on_thread is not None:
            _close_late(on_thread)
        if self._answered_awaitable(code, value):
            if (early := self._expired(value, req, started)) is not None:
                return early
            try:
                scheduled_task = _on_caller_loop(value)
            except Exception as exc:  # e.g. a future bound to another loop
                _close(value)
                return self._failed(ROUTER_ERROR, started, exc)
            code, value = await _asettled(scheduled_task, _left(req, started))  # measured after scheduling
        return self._outcome(code, value, started)

    @staticmethod
    def _answered_awaitable(code: str | None, value: Any) -> bool:
        """Whether a sync-looking router answered with a coroutine to await."""
        return code is None and inspect.isawaitable(value)

    def _expired(self, value: Any, req: RouteRequest, started: float) -> RouterOutcome | None:
        """The timeout's outcome, the coroutine closed unrun, when no time is left to await it; else ``None``."""
        if _left(req, started) > 0:
            return None
        _close(value)
        return self._failed(ROUTER_TIMEOUT, started)

    def _outcome(self, code: str | None, value: Any, started: float) -> RouterOutcome:
        return self._failed(code, started, value) if code is not None else self._read(value, started)

    async def _coroutine(self, req: RouteRequest) -> Any:
        assert self._async is not None  # called only when the router has an async side
        return await self._async(req)

    def _read(self, result: object, started: float) -> RouterOutcome:
        if not well_formed(result):
            _close(result)
            return self._failed(ROUTER_INVALID, started)
        if self.fail_on_error and none_answered(result):  # a composite whose every member failed or was empty
            raise RouterError(self.name, ROUTER_EMPTY)
        return RouterOutcome(decision=result, failure=None, elapsed_ms=(time.perf_counter() - started) * 1000)

    def _failed(self, code: str, started: float, error: Any = None) -> RouterOutcome:
        if self.fail_on_error:
            raise RouterError(self.name, code) from error
        return RouterOutcome(decision=None, failure=code, elapsed_ms=(time.perf_counter() - started) * 1000)
