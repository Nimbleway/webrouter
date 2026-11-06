"""Calling a router at the edges: the caller's context, the call's own deadline, and a task the call gave up on."""

from __future__ import annotations

import asyncio
import contextvars
import gc
import time
from typing import Any

import pytest

from tests.test_router_invoke import DECISION, SLEEP_S, _decorated, _req, _sleeper
from web_router import RouteDecision, RouteRequest
from web_router.providers._deadline import call_deadline
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
