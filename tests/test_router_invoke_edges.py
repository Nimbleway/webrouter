"""Calling a router at the edges: the caller's context, the call's own deadline, and a task the call gave up on."""

from __future__ import annotations

import asyncio
import contextvars
import gc
import inspect
import threading
import time
from typing import Any

import pytest

from tests.test_router_invoke import DECISION, SLEEP_S, _async, _decorated, _req, _sleeper
from web_router import RouteDecision, RouteRequest
from web_router.providers._deadline import call_deadline
from web_router.routing import invoke
from web_router.routing.invoke import AttachedRouter

_SEEN_BY_ROUTER: contextvars.ContextVar[str] = contextvars.ContextVar("seen_by_router", default="unset")


def _reads_context(req: RouteRequest) -> RouteDecision:
    return RouteDecision(reason=_SEEN_BY_ROUTER.get())


async def _areads_context(req: RouteRequest) -> RouteDecision:
    return RouteDecision(reason=_SEEN_BY_ROUTER.get())


@pytest.mark.parametrize(
    "router", [_reads_context, _areads_context, _decorated(_areads_context)], ids=["sync", "async", "decorated"]
)
@pytest.mark.parametrize("from_async", [False, True], ids=["from-sync", "from-async"])
def test_the_router_runs_in_the_callers_context(router: Any, from_async: bool) -> None:
    attached = AttachedRouter(router)

    def ask() -> Any:
        _SEEN_BY_ROUTER.set("caller")
        return asyncio.run(attached.acall(_req(1.0))) if from_async else attached.call(_req(1.0))

    outcome = contextvars.copy_context().run(ask)
    assert outcome.decision is not None
    assert outcome.decision.reason == "caller"


@pytest.mark.parametrize("from_async", [False, True], ids=["from-sync", "from-async"])
def test_the_wait_never_passes_the_calls_own_deadline(from_async: bool) -> None:
    attached = AttachedRouter(_sleeper)
    req = _req(SLEEP_S)  # built while time was left; the call's deadline is nearer by the time it waits
    started = time.perf_counter()
    with call_deadline(0.1):
        outcome = asyncio.run(attached.acall(req)) if from_async else attached.call(req)
    assert time.perf_counter() - started < SLEEP_S / 2
    assert outcome.failure == "router_timeout"


async def test_an_abandoned_awaitable_is_never_reported_unretrieved() -> None:
    reported: list[dict[str, Any]] = []
    asyncio.get_running_loop().set_exception_handler(lambda loop, context: reported.append(context))

    async def fails_late(req: RouteRequest) -> RouteDecision:
        try:
            await asyncio.sleep(SLEEP_S)
        except asyncio.CancelledError:
            raise ValueError("raised while being cancelled") from None
        return DECISION

    outcome = await AttachedRouter(_decorated(fails_late)).acall(_req())
    assert outcome.failure == "router_timeout"
    await asyncio.sleep(0.05)
    gc.collect()
    assert reported == []


@pytest.mark.parametrize("from_async", [False, True], ids=["from-sync", "from-async"])
def test_a_decorated_router_scheduled_past_the_deadline_is_a_timeout(
    from_async: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    def slow_scheduling(real: Any) -> Any:
        def schedule(awaitable: Any) -> Any:
            time.sleep(0.2)  # longer than the 0.1 s the call has left
            return real(awaitable)

        return schedule

    name = "_on_caller_loop" if from_async else "_on_private_loop"
    monkeypatch.setattr(invoke, name, slow_scheduling(getattr(invoke, name)))
    attached = AttachedRouter(_decorated(_async))
    outcome = asyncio.run(attached.acall(_req(0.1))) if from_async else attached.call(_req(0.1))
    assert outcome.failure == "router_timeout"


async def test_a_coroutine_a_worker_returns_after_its_caller_was_cancelled_is_closed() -> None:
    started, coroutines = threading.Event(), []

    def late(req: RouteRequest) -> Any:
        started.set()
        time.sleep(0.1)
        coroutines.append(_async(req))
        return coroutines[-1]

    waiting = asyncio.ensure_future(AttachedRouter(late).acall(_req(SLEEP_S)))
    await asyncio.to_thread(started.wait)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    for _ in range(100):
        if coroutines and inspect.getcoroutinestate(coroutines[0]) == inspect.CORO_CLOSED:
            break
        await asyncio.sleep(0.01)
    assert coroutines
    assert inspect.getcoroutinestate(coroutines[0]) == inspect.CORO_CLOSED


async def test_a_task_nobody_will_await_is_cancelled_and_its_error_read() -> None:
    reported: list[dict[str, Any]] = []
    asyncio.get_running_loop().set_exception_handler(lambda loop, context: reported.append(context))

    async def fails_when_cancelled() -> None:
        try:
            await asyncio.sleep(SLEEP_S)
        except asyncio.CancelledError:
            raise ValueError("raised while being cancelled") from None

    task = asyncio.ensure_future(fails_when_cancelled())
    await asyncio.sleep(0)
    invoke._close(task)
    await asyncio.sleep(0.05)
    assert task.done()
    del task
    gc.collect()
    assert reported == []
