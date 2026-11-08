"""A router that runs out of time, raises, answers wrongly or is cancelled, and ``fail_on_router_error``."""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

import pytest

from tests.invoke_helpers import SLEEP_S, make_req, sleeper, slow_router
from web_router import RouteDecision, RouteRequest, RouterError
from web_router.routing.invoke import AttachedRouter


def test_a_sync_router_that_sleeps_is_dropped_at_the_deadline() -> None:
    started = time.perf_counter()
    outcome = AttachedRouter(sleeper).call(make_req(0.05))
    assert time.perf_counter() - started < SLEEP_S / 2
    assert outcome.decision is None
    assert outcome.failure == "router_timeout"


async def test_a_sync_router_that_sleeps_is_dropped_at_the_deadline_from_async_code() -> None:
    started = time.perf_counter()
    outcome = await AttachedRouter(sleeper).acall(make_req(0.05))
    assert time.perf_counter() - started < SLEEP_S / 2
    assert outcome.failure == "router_timeout"


def test_an_async_router_that_sleeps_is_cancelled_at_the_deadline_from_sync_code() -> None:
    cancelled = threading.Event()
    outcome = AttachedRouter(slow_router(cancelled)).call(make_req(0.05))
    assert outcome.failure == "router_timeout"
    assert cancelled.wait(1.0)


async def test_an_async_router_that_sleeps_is_cancelled_at_the_deadline_from_async_code() -> None:
    cancelled = asyncio.Event()
    outcome = await AttachedRouter(slow_router(cancelled)).acall(make_req(0.05))
    assert outcome.failure == "router_timeout"
    await asyncio.wait_for(cancelled.wait(), 1.0)  # while this loop still runs, not at its shutdown


def test_no_time_left_is_a_timeout_without_calling_the_router() -> None:
    called: list[RouteRequest] = []
    outcome = AttachedRouter(called.append).call(make_req(0.0))
    assert outcome.failure == "router_timeout"
    assert called == []


def _raises(req: RouteRequest) -> RouteDecision:
    raise ValueError("boom")


async def _araises(req: RouteRequest) -> RouteDecision:
    raise ValueError("boom")


@pytest.mark.parametrize("router", [_raises, _araises], ids=["sync", "async"])
@pytest.mark.parametrize("from_async", [False, True], ids=["from-sync", "from-async"])
def test_a_router_that_raises_is_an_error_not_a_crash(router: Any, from_async: bool) -> None:
    attached = AttachedRouter(router)
    outcome = asyncio.run(attached.acall(make_req())) if from_async else attached.call(make_req())
    assert outcome.decision is None
    assert outcome.failure == "router_error"


@pytest.mark.parametrize(
    "answer",
    [None, {"providers": {"search": ["exa"]}}, RouteDecision(providers={"search": "exa"}), RouteDecision(providers=[])],  # type: ignore[arg-type]
    ids=["None", "dict", "string-ranking", "list-providers"],
)
def test_an_answer_that_is_not_a_route_decision_is_invalid(answer: Any) -> None:
    outcome = AttachedRouter(lambda req: answer).call(make_req())
    assert outcome.decision is None
    assert outcome.failure == "router_invalid"


@pytest.mark.parametrize(
    ("router", "code"),
    [(_raises, "router_error"), (sleeper, "router_timeout"), (lambda req: None, "router_invalid")],
    ids=["error", "timeout", "invalid"],
)
def test_fail_on_router_error_raises_instead(router: Any, code: str) -> None:
    with pytest.raises(RouterError) as raised:
        AttachedRouter(router, fail_on_error=True).call(make_req(0.05))
    assert raised.value.code == code
    if code == "router_error":
        assert isinstance(raised.value.__cause__, ValueError)


async def test_a_caller_cancelled_while_waiting_cancels_the_router_and_is_cancelled() -> None:
    started, cancelled = asyncio.Event(), asyncio.Event()
    waiting = asyncio.ensure_future(AttachedRouter(slow_router(cancelled, started)).acall(make_req(SLEEP_S)))
    await started.wait()
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    await asyncio.wait_for(cancelled.wait(), 1.0)


async def _cancels_itself(req: RouteRequest) -> RouteDecision:
    raise asyncio.CancelledError


def _sync_cancels_itself(req: RouteRequest) -> RouteDecision:
    raise asyncio.CancelledError


@pytest.mark.parametrize("router", [_cancels_itself, _sync_cancels_itself], ids=["async", "sync"])
@pytest.mark.parametrize("from_async", [False, True], ids=["from-sync", "from-async"])
def test_a_router_that_cancels_itself_is_a_router_error(router: Any, from_async: bool) -> None:
    attached = AttachedRouter(router)
    outcome = asyncio.run(attached.acall(make_req())) if from_async else attached.call(make_req())
    assert (outcome.decision, outcome.failure) == (None, "router_error")
    with pytest.raises(RouterError, match="router_error"):
        AttachedRouter(router, fail_on_error=True).call(make_req())
