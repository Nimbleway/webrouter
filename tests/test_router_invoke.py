"""Attaching a router and calling it: the two forms, sync and async adaptation, the timeout and the failure policy."""

from __future__ import annotations

import asyncio
import functools
import inspect
import threading
import time
from typing import Any

import pytest

from web_router import AsyncWebRouter, Budget, Candidate, RouteDecision, RouteRequest, RouterError, WebRouter
from web_router.constants_invoke import ROUTER_THREAD_NAME
from web_router.exceptions import ConfigurationError
from web_router.providers._deadline import call_deadline
from web_router.routing.invoke import AttachedRouter

DECISION = RouteDecision(providers={"search": ["exa"]}, reason="fast")
SLEEP_S = 1.0
"""How long a slow router sleeps: far past its 50 ms, so a call that waited for it would show."""


def _req(deadline_s: float = 0.05) -> RouteRequest:
    return RouteRequest(
        prompt="q", candidates=(Candidate(tool="search", provider="exa"),), deadline_s=deadline_s, pinned_tool="search"
    )


def _sync(req: RouteRequest) -> RouteDecision:
    return DECISION


async def _async(req: RouteRequest) -> RouteDecision:
    return DECISION


class _Both:
    def __init__(self) -> None:
        self.sides: list[str] = []

    def route(self, req: RouteRequest) -> RouteDecision:
        self.sides.append("route")
        return DECISION

    async def aroute(self, req: RouteRequest) -> RouteDecision:
        self.sides.append("aroute")
        return DECISION


class _OnlyRoute:
    def route(self, req: RouteRequest) -> RouteDecision:
        return DECISION


class _OnlyAroute:
    async def aroute(self, req: RouteRequest) -> RouteDecision:
        return DECISION


FORMS = [_sync, _async, _OnlyRoute(), _OnlyAroute(), _Both()]
FORM_IDS = ["function", "coroutine-function", "route", "aroute", "both"]


@pytest.mark.parametrize("router", FORMS, ids=FORM_IDS)
def test_each_form_answers_from_sync_code(router: Any) -> None:
    outcome = AttachedRouter(router).call(_req())
    assert outcome.decision is DECISION
    assert outcome.failure is None


@pytest.mark.parametrize("router", FORMS, ids=FORM_IDS)
async def test_each_form_answers_from_async_code(router: Any) -> None:
    outcome = await AttachedRouter(router).acall(_req())
    assert outcome.decision is DECISION
    assert outcome.failure is None


def test_an_object_with_both_sides_is_called_on_the_matching_one() -> None:
    both = _Both()
    attached = AttachedRouter(both)
    attached.call(_req())
    asyncio.run(attached.acall(_req()))
    assert both.sides == ["route", "aroute"]


@pytest.mark.parametrize(
    "router", ["acme", "remote", 42, None.__class__, _OnlyRoute], ids=["name", "remote", "int", "NoneType", "class"]
)
def test_anything_else_is_a_configuration_error(router: Any) -> None:
    with pytest.raises(ConfigurationError):
        AttachedRouter(router)


@pytest.mark.parametrize("cls", [WebRouter, AsyncWebRouter])
@pytest.mark.parametrize("name", ["acme", "remote"])
def test_a_router_given_by_name_is_refused_by_both_routers(cls: type, name: str) -> None:
    with pytest.raises(ConfigurationError, match="router="):
        cls(router=name)


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
    AttachedRouter(_thread_of(log)).call(_req())
    assert log == [ROUTER_THREAD_NAME]


async def test_a_sync_router_runs_on_a_worker_thread_from_async_code() -> None:
    log: list[str] = []
    await AttachedRouter(_thread_of(log)).acall(_req())
    assert log == [ROUTER_THREAD_NAME]


def test_an_async_router_runs_on_the_private_loop_thread_from_sync_code() -> None:
    log: list[str] = []
    AttachedRouter(_loop_thread_of(log)).call(_req())
    assert log == [ROUTER_THREAD_NAME]


async def test_an_async_router_runs_on_the_callers_loop_from_async_code() -> None:
    log: list[str] = []
    await AttachedRouter(_loop_thread_of(log)).acall(_req())
    assert log == [threading.current_thread().name]


def test_the_default_timeout_is_50_ms() -> None:
    assert AttachedRouter(_sync).timeout_s == 0.05


def test_a_function_or_an_object_declares_its_own_timeout() -> None:
    def slow(req: RouteRequest) -> RouteDecision:
        return DECISION

    slow.timeout_s = 3  # type: ignore[attr-defined]
    obj = _OnlyRoute()
    obj.timeout_s = 1.5  # type: ignore[attr-defined]
    assert AttachedRouter(slow).timeout_s == 3.0
    assert AttachedRouter(obj).timeout_s == 1.5


@pytest.mark.parametrize("value", [0, -1, "3", True, float("nan"), float("inf")])
def test_a_timeout_that_is_not_a_positive_number_is_refused(value: Any) -> None:
    obj = _OnlyRoute()
    obj.timeout_s = value  # type: ignore[attr-defined]
    with pytest.raises(ConfigurationError, match="timeout_s"):
        AttachedRouter(obj)


def test_the_router_gets_the_least_of_its_timeout_the_budget_and_the_call_deadline() -> None:
    obj = _OnlyRoute()
    obj.timeout_s = 2.0  # type: ignore[attr-defined]
    attached = AttachedRouter(obj)
    assert attached.deadline(None) == 2.0
    assert attached.deadline(Budget(time_limit_s=0.5)) == 0.5
    with call_deadline(0.2):
        assert 0 < attached.deadline(Budget(time_limit_s=0.5)) <= 0.2


def _sleeper(req: RouteRequest) -> RouteDecision:
    time.sleep(SLEEP_S)
    return DECISION


def test_a_sync_router_that_sleeps_is_dropped_at_the_deadline() -> None:
    started = time.perf_counter()
    outcome = AttachedRouter(_sleeper).call(_req(0.05))
    assert time.perf_counter() - started < SLEEP_S / 2
    assert outcome.decision is None
    assert outcome.failure == "router_timeout"


async def test_a_sync_router_that_sleeps_is_dropped_at_the_deadline_from_async_code() -> None:
    started = time.perf_counter()
    outcome = await AttachedRouter(_sleeper).acall(_req(0.05))
    assert time.perf_counter() - started < SLEEP_S / 2
    assert outcome.failure == "router_timeout"


def test_an_async_router_that_sleeps_is_cancelled_at_the_deadline_from_sync_code() -> None:
    cancelled = threading.Event()

    async def slow(req: RouteRequest) -> RouteDecision:
        try:
            await asyncio.sleep(SLEEP_S)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return DECISION

    outcome = AttachedRouter(slow).call(_req(0.05))
    assert outcome.failure == "router_timeout"
    assert cancelled.wait(1.0)


async def test_an_async_router_that_sleeps_is_cancelled_at_the_deadline_from_async_code() -> None:
    cancelled = asyncio.Event()

    async def slow(req: RouteRequest) -> RouteDecision:
        try:
            await asyncio.sleep(SLEEP_S)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return DECISION

    outcome = await AttachedRouter(slow).acall(_req(0.05))
    assert outcome.failure == "router_timeout"
    await asyncio.wait_for(cancelled.wait(), 1.0)  # while this loop still runs, not at its shutdown


def test_no_time_left_is_a_timeout_without_calling_the_router() -> None:
    called: list[RouteRequest] = []
    outcome = AttachedRouter(called.append).call(_req(0.0))
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
    outcome = asyncio.run(attached.acall(_req())) if from_async else attached.call(_req())
    assert outcome.decision is None
    assert outcome.failure == "router_error"


@pytest.mark.parametrize(
    "answer",
    [None, {"providers": {"search": ["exa"]}}, RouteDecision(providers={"search": "exa"}), RouteDecision(providers=[])],  # type: ignore[arg-type]
    ids=["None", "dict", "string-ranking", "list-providers"],
)
def test_an_answer_that_is_not_a_route_decision_is_invalid(answer: Any) -> None:
    outcome = AttachedRouter(lambda req: answer).call(_req())
    assert outcome.decision is None
    assert outcome.failure == "router_invalid"


@pytest.mark.parametrize(
    ("router", "code"),
    [(_raises, "router_error"), (_sleeper, "router_timeout"), (lambda req: None, "router_invalid")],
    ids=["error", "timeout", "invalid"],
)
def test_fail_on_router_error_raises_instead(router: Any, code: str) -> None:
    with pytest.raises(RouterError) as raised:
        AttachedRouter(router, fail_on_error=True).call(_req(0.05))
    assert raised.value.code == code
    if code == "router_error":
        assert isinstance(raised.value.__cause__, ValueError)


async def test_a_caller_cancelled_while_waiting_cancels_the_router_and_is_cancelled() -> None:
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def slow(req: RouteRequest) -> RouteDecision:
        started.set()
        try:
            await asyncio.sleep(SLEEP_S)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return DECISION

    waiting = asyncio.ensure_future(AttachedRouter(slow).acall(_req(SLEEP_S)))
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
    outcome = asyncio.run(attached.acall(_req())) if from_async else attached.call(_req())
    assert (outcome.decision, outcome.failure) == (None, "router_error")
    with pytest.raises(RouterError, match="router_error"):
        AttachedRouter(router, fail_on_error=True).call(_req())


def _decorated(fn: Any) -> Any:
    """A plain decorator: the wrapper looks sync, and hands back ``fn``'s coroutine."""

    @functools.wraps(fn)
    def wrapper(req: RouteRequest) -> Any:
        return fn(req)

    return wrapper


@pytest.mark.parametrize("from_async", [False, True], ids=["from-sync", "from-async"])
def test_a_decorated_async_router_is_awaited(from_async: bool) -> None:
    attached = AttachedRouter(_decorated(_async))
    outcome = asyncio.run(attached.acall(_req())) if from_async else attached.call(_req())
    assert outcome.decision is DECISION


@pytest.mark.parametrize("from_async", [False, True], ids=["from-sync", "from-async"])
def test_a_decorated_async_router_is_held_to_the_deadline_and_closed(from_async: bool) -> None:
    coroutines: list[Any] = []

    async def slow(req: RouteRequest) -> RouteDecision:
        await asyncio.sleep(SLEEP_S)
        return DECISION

    def router(req: RouteRequest) -> Any:
        coroutines.append(slow(req))
        return coroutines[-1]

    attached = AttachedRouter(router)
    started = time.perf_counter()
    outcome = asyncio.run(attached.acall(_req())) if from_async else attached.call(_req())
    assert time.perf_counter() - started < SLEEP_S / 2
    assert outcome.failure == "router_timeout"
    _closes(coroutines[0])


@pytest.mark.parametrize("from_async", [False, True], ids=["from-sync", "from-async"])
def test_a_coroutine_handed_back_after_the_deadline_is_closed(from_async: bool) -> None:
    coroutines: list[Any] = []

    def late(req: RouteRequest) -> Any:
        time.sleep(0.2)
        coroutines.append(_async(req))
        return coroutines[-1]

    attached = AttachedRouter(late)
    outcome = asyncio.run(attached.acall(_req())) if from_async else attached.call(_req())
    assert outcome.failure == "router_timeout"
    deadline = time.monotonic() + 1.0
    while not coroutines and time.monotonic() < deadline:
        time.sleep(0.01)
    _closes(coroutines[0])


def _closes(coroutine: Any) -> None:
    deadline = time.monotonic() + 1.0
    while inspect.getcoroutinestate(coroutine) != inspect.CORO_CLOSED and time.monotonic() < deadline:
        time.sleep(0.01)
    assert inspect.getcoroutinestate(coroutine) == inspect.CORO_CLOSED
