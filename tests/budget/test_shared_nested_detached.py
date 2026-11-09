"""Nested calls on one router that run beside each other, or after the call that started them has ended."""

from __future__ import annotations

import asyncio
import threading
from collections import Counter
from contextlib import suppress
from contextvars import copy_context
from typing import Any

import pytest

from tests.budget.conftest import Chain, Table
from tests.conftest import FakeProvider
from web_router import AsyncWebRouter, Budget, BudgetExceededError, WebRouter, prices
from web_router.exceptions import ConfigurationError, WebRouterError
from web_router.registry import register_provider, unregister_provider
from web_router.types import SearchRequest, SearchResponse

WAIT_S = 5.0


@pytest.fixture
def registered(fake_provider: object) -> Any:
    """Registers a search backend by name for the test, and unregisters it after."""
    names: list[str] = []

    def register(name: str, **entries: Any) -> None:
        register_provider(name, type(name.title(), (FakeProvider,), {"name": name, **entries}))
        names.append(name)

    yield register
    for name in names:
        unregister_provider(name)


def test_an_inner_refusal_takes_back_its_own_attempt_while_another_thread_admits_one(
    registered: Any, priced: Table, monkeypatch: pytest.MonkeyPatch
) -> None:
    router = WebRouter()

    def inner(provider: str, budget: Budget | None) -> None:
        with suppress(WebRouterError):
            router.search("inner", provider=provider, budget=budget)

    def wrapper(self: Any, search: SearchRequest) -> SearchResponse:
        refused = threading.Thread(target=copy_context().run, args=(inner, "a", Budget(max_cost_usd=0.005)))
        fits = threading.Thread(target=copy_context().run, args=(inner, "b", None))
        for thread in (refused, fits):
            thread.start()
        for thread in (refused, fits):
            thread.join(WAIT_S)
        return FakeProvider._respond(self, search)

    registered("wrapper", _search=wrapper)
    registered("a")
    registered("b")
    priced.update(wrapper=(0.02, None), a=(0.01, None), b=(0.04, None))
    _interleave(monkeypatch)
    with router.budget(Budget(max_cost_usd=1.0)) as shared:
        router.search("q", provider="wrapper")
    assert (shared.status().spent_usd, shared.status().reserved_usd) == (0.06, 0.0), "wrapper and b ran; a did not"


def _interleave(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hold ``a``'s inner check until ``b`` has been admitted by the enclosing call, so ``b`` is admitted last."""
    looked_up, b_admitted = Counter[str](), threading.Event()
    latency = prices.estimate_latency

    def held(provider: str, method: str, mode: str | None = None) -> Any:
        looked_up[provider] += 1  # once by the enclosing call's check, then by the inner call's own
        if provider == "b" and looked_up[provider] == 2:
            b_admitted.set()
        if provider == "a" and looked_up[provider] == 2:
            assert b_admitted.wait(WAIT_S)
        return latency(provider, method, mode)

    monkeypatch.setattr(prices, "estimate_latency", held)


@pytest.mark.parametrize("cap", [None, Budget(max_cost_usd=0.025)], ids=["no-cap", "outer-cap"])
async def test_a_call_a_backend_leaves_running_after_it_returns_settles_its_own_spend(
    registered: Any, priced: Table, cap: Budget | None
) -> None:
    """With ``outer-cap``, the later call would not fit the ended call's cap: that call is no longer its to check."""
    router, ended = AsyncWebRouter(), asyncio.Event()
    left: list[asyncio.Task[Any]] = []

    async def later() -> Any:
        await ended.wait()
        return await router.search("inner", provider="fake")

    async def wrapper(self: Any, search: SearchRequest) -> SearchResponse:
        left.append(asyncio.create_task(later()))
        return FakeProvider._respond(self, search)

    registered("wrapper", _asearch=wrapper)
    priced.update(wrapper=(0.02, None), fake=(0.01, None))
    async with router.budget(Budget(max_cost_usd=1.0)) as shared:
        await router.search("q", provider="wrapper", budget=cap)
        ended.set()
        later_response = await asyncio.wait_for(left[0], WAIT_S)
    assert later_response.provider == "fake"
    assert (shared.status().spent_usd, shared.status().reserved_usd) == (0.03, 0.0)


@pytest.mark.parametrize("tool", ["search"], indirect=True)
@pytest.mark.parametrize("joined", [False, True], ids=["left-running", "joined"])
def test_a_nested_call_left_running_never_takes_the_shared_budget_past_its_cap(
    chain: Chain, registered: Any, priced: Table, joined: bool
) -> None:
    chain.sleeps["first"], chain.raises["first"] = 0.3, ConfigurationError("first holds no key")
    priced.update(wrapper=(0.02, None), first=(0.01, None), second=(0.04, None), fake=(0.05, None))
    router, started = WebRouter(), []

    def inner() -> None:
        with suppress(WebRouterError):
            router.search("inner", provider=chain.auto())

    def wrapper(self: Any, search: SearchRequest) -> SearchResponse:
        started.append(threading.Thread(target=copy_context().run, args=(inner,)))
        started[0].start()
        if joined:
            started[0].join(WAIT_S)
        return FakeProvider._respond(self, search)

    registered("wrapper", _search=wrapper)
    ran = ["wrapper"]
    with router.budget(Budget(max_cost_usd=0.10)) as shared:
        router.search("q", provider="wrapper")
        with suppress(BudgetExceededError):
            router.search("q", provider="fake")
            ran.append("fake")
        started[0].join(WAIT_S)
    ran += chain.called
    cost = sum(priced[name][0] or 0.0 for name in ran)
    assert cost <= 0.10 + 1e-9, f"{ran} ran"
    assert (shared.status().spent_usd, shared.status().reserved_usd) == (pytest.approx(cost), 0.0)
    if joined:
        assert ran == ["wrapper", "first", "second"]
