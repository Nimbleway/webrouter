"""What the router-invocation tests share: a decision, a request, a router of each form, and slow routers."""

from __future__ import annotations

import asyncio
import functools
import inspect
import threading
import time
from typing import Any

from web_router import Candidate, RouteDecision, RouteRequest

DECISION = RouteDecision(providers={"search": ["exa"]}, reason="fast")
SLEEP_S = 1.0
"""How long a slow router sleeps: far past its 50 ms, so a call that waited for it would show."""


def make_req(deadline_s: float = 0.05) -> RouteRequest:
    return RouteRequest(
        prompt="q", candidates=(Candidate(tool="search", provider="exa"),), deadline_s=deadline_s, pinned_tool="search"
    )


def sync_router(req: RouteRequest) -> RouteDecision:
    return DECISION


async def async_router(req: RouteRequest) -> RouteDecision:
    return DECISION


class Both:
    def __init__(self) -> None:
        self.sides: list[str] = []

    def route(self, req: RouteRequest) -> RouteDecision:
        self.sides.append("route")
        return DECISION

    async def aroute(self, req: RouteRequest) -> RouteDecision:
        self.sides.append("aroute")
        return DECISION


def sleeper(req: RouteRequest) -> RouteDecision:
    time.sleep(SLEEP_S)
    return DECISION


def slow_router(cancelled: threading.Event | asyncio.Event, started: asyncio.Event | None = None) -> Any:
    """An async router that sleeps ``SLEEP_S``, setting ``started`` when it begins and ``cancelled`` if cancelled."""

    async def slow(req: RouteRequest) -> RouteDecision:
        if started is not None:
            started.set()
        try:
            await asyncio.sleep(SLEEP_S)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return DECISION

    return slow


def decorated(fn: Any) -> Any:
    """A plain decorator: the wrapper looks sync, and hands back ``fn``'s coroutine."""

    @functools.wraps(fn)
    def wrapper(req: RouteRequest) -> Any:
        return fn(req)

    return wrapper


def closes(coroutine: Any) -> None:
    deadline = time.monotonic() + 1.0
    while inspect.getcoroutinestate(coroutine) != inspect.CORO_CLOSED and time.monotonic() < deadline:
        time.sleep(0.01)
    assert inspect.getcoroutinestate(coroutine) == inspect.CORO_CLOSED
