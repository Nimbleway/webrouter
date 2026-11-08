"""Calling a router: the thread or loop each form runs on, and an async router behind a plain decorator."""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

import pytest

from tests.invoke_helpers import DECISION, SLEEP_S, async_router, closes, decorated, make_req, slow_router
from web_router import RouteDecision, RouteRequest
from web_router.constants_invoke import ROUTER_THREAD_NAME
from web_router.routing.invoke import AttachedRouter


def _thread_of(log: list[str]) -> Any:
    def router(req: RouteRequest) -> RouteDecision:
        log.append(threading.current_thread().name)
        return DECISION

    return router


def _loop_thread_of(log: list[str]) -> Any:
    async def router(req: RouteRequest) -> RouteDecision:
        log.append(threading.current_thread().name)
        return DECISION

    return router


def test_a_sync_router_runs_on_a_worker_thread_from_sync_code() -> None:
    log: list[str] = []
    AttachedRouter(_thread_of(log)).call(make_req())
    assert log == [ROUTER_THREAD_NAME]


async def test_a_sync_router_runs_on_a_worker_thread_from_async_code() -> None:
    log: list[str] = []
    await AttachedRouter(_thread_of(log)).acall(make_req())
    assert log == [ROUTER_THREAD_NAME]


def test_an_async_router_runs_on_the_private_loop_thread_from_sync_code() -> None:
    log: list[str] = []
    AttachedRouter(_loop_thread_of(log)).call(make_req())
    assert log == [ROUTER_THREAD_NAME]


async def test_an_async_router_runs_on_the_callers_loop_from_async_code() -> None:
    log: list[str] = []
    await AttachedRouter(_loop_thread_of(log)).acall(make_req())
    assert log == [threading.current_thread().name]


@pytest.mark.parametrize("from_async", [False, True], ids=["from-sync", "from-async"])
def test_a_decorated_async_router_is_awaited(from_async: bool) -> None:
    attached = AttachedRouter(decorated(async_router))
    outcome = asyncio.run(attached.acall(make_req())) if from_async else attached.call(make_req())
    assert outcome.decision is DECISION


@pytest.mark.parametrize("from_async", [False, True], ids=["from-sync", "from-async"])
def test_a_decorated_async_router_is_held_to_the_deadline_and_closed(from_async: bool) -> None:
    coroutines: list[Any] = []
    slow = slow_router(threading.Event())

    def router(req: RouteRequest) -> Any:
        coroutines.append(slow(req))
        return coroutines[-1]

    attached = AttachedRouter(router)
    started = time.perf_counter()
    outcome = asyncio.run(attached.acall(make_req())) if from_async else attached.call(make_req())
    assert time.perf_counter() - started < SLEEP_S / 2
    assert outcome.failure == "router_timeout"
    closes(coroutines[0])


@pytest.mark.parametrize("from_async", [False, True], ids=["from-sync", "from-async"])
def test_a_coroutine_handed_back_after_the_deadline_is_closed(from_async: bool) -> None:
    coroutines: list[Any] = []

    def late(req: RouteRequest) -> Any:
        time.sleep(0.2)
        coroutines.append(async_router(req))
        return coroutines[-1]

    attached = AttachedRouter(late)
    outcome = asyncio.run(attached.acall(make_req())) if from_async else attached.call(make_req())
    assert outcome.failure == "router_timeout"
    deadline = time.monotonic() + 1.0
    while not coroutines and time.monotonic() < deadline:
        time.sleep(0.01)
    closes(coroutines[0])
