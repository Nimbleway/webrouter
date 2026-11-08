"""Nested calls count toward the enclosing call's own cap, and one they refuse is taken back from it.

``wrapper`` ($0.02) makes the inner calls a test lists, one after another, on
the same router, each with a budget of its own; ``a`` and ``b`` cost $0.01
each. The enclosing call's cap is $0.035: room for one inner call, not two.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import suppress
from typing import Any

import pytest

from tests.budget.conftest import Chain
from tests.conftest import FakeProvider
from tests.routing_helpers import Table
from web_router import Budget, BudgetExceededError, WebRouter
from web_router.exceptions import WebRouterError
from web_router.registry import register_provider, unregister_provider
from web_router.types import SearchRequest, SearchResponse

OUTER_CAP = Budget(max_cost_usd=0.035)
ROOMY = Budget(max_cost_usd=1.0)
"""An inner call's own budget, room for either backend."""


@pytest.fixture
def calls(priced: Table) -> Iterator[tuple[WebRouter, list[tuple[str, Budget | None]], list[str]]]:
    """The router, the inner calls ``wrapper`` makes (backend, its own budget), and the backends that ran."""
    router, inner, ran = WebRouter(), [], []

    def wrapper(self: Any, search: SearchRequest) -> SearchResponse:
        for provider, budget in inner:
            with suppress(WebRouterError):
                router.search("inner", provider=provider, budget=budget)
        return FakeProvider._respond(self, search)

    def logged(self: Any, search: SearchRequest) -> SearchResponse:
        ran.append(self.name)
        return FakeProvider._respond(self, search)

    names = {"wrapper": wrapper, "a": logged, "b": logged}
    for name, search in names.items():
        register_provider(name, type(name.title(), (FakeProvider,), {"name": name, "_search": search}))
    priced.update(wrapper=(0.02, None), a=(0.01, None), b=(0.01, None))
    yield router, inner, ran
    for name in names:
        unregister_provider(name)


def test_two_nested_calls_that_each_fit_alone_do_not_both_fit_the_outer_cap(calls: Any) -> None:
    router, inner, ran = calls
    inner.extend([("a", ROOMY), ("b", ROOMY)])
    response = router.search("q", provider="wrapper", budget=OUTER_CAP)
    assert ran == ["a"], "b would take the outer call to $0.04"
    assert response.decision.estimated_cost_usd == pytest.approx(0.03)


def test_a_nested_call_its_own_budget_refuses_leaves_room_for_the_next(calls: Any) -> None:
    router, inner, ran = calls
    inner.extend([("a", Budget(max_cost_usd=0.005)), ("b", ROOMY)])
    response = router.search("q", provider="wrapper", budget=OUTER_CAP)
    assert ran == ["b"], "a was refused and taken back, so b still fits"
    assert response.decision.estimated_cost_usd == pytest.approx(0.03)


@pytest.mark.parametrize("tool", ["search"], indirect=True)
@pytest.mark.parametrize("named", [True, False], ids=["named", "auto"])
@pytest.mark.parametrize("deep", [False, True], ids=["a-b", "a-b-c"])
def test_a_nested_call_with_no_budget_on_another_router_is_reserved_in_the_shared_budget(
    chain: Chain, fake_provider: object, priced: Table, named: bool, deep: bool
) -> None:
    """``wrapper`` on router A calls B; deep, B's ``middle`` ($0.01) calls C. The innermost call costs $0.05."""
    a, b, c = WebRouter(), WebRouter(), WebRouter()

    def innermost(router: WebRouter) -> None:
        router.search("inner", provider="first" if named else chain.auto())

    def wrapper(self: Any, search: SearchRequest) -> SearchResponse:
        if deep:
            b.search("middle", provider="middle")
        else:
            innermost(b)
        return FakeProvider._respond(self, search)

    def middle(self: Any, search: SearchRequest) -> SearchResponse:
        innermost(c)
        return FakeProvider._respond(self, search)

    for name, search in {"wrapper": wrapper, "middle": middle}.items():
        register_provider(name, type(name.title(), (FakeProvider,), {"name": name, "_search": search}))
    priced.update(wrapper=(0.02, None), middle=(0.01, None), first=(0.05, None), fake=(0.05, None))
    try:
        with a.budget(Budget(max_cost_usd=0.10)) as shared:
            a.search("q", provider="wrapper")
            with pytest.raises(BudgetExceededError):
                a.search("q", provider="fake")
    finally:
        unregister_provider("wrapper")
        unregister_provider("middle")
    assert chain.called == ["first"]
    assert shared.status().spent_usd == pytest.approx(0.08 if deep else 0.07)
