"""Nested calls on one router that run beside each other."""

from __future__ import annotations

import threading
from collections import Counter
from contextlib import suppress
from contextvars import copy_context
from typing import Any

import pytest

from tests.budget.conftest import Table
from tests.conftest import FakeProvider
from web_router import Budget, WebRouter, prices
from web_router.exceptions import WebRouterError
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
