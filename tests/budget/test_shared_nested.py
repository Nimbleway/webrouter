"""A backend calling its own router: the inner call is charged once to a shared budget, and to the outer call."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from tests.budget.conftest import Table
from tests.conftest import FakeProvider
from web_router import Budget, WebRouter
from web_router.exceptions import WebRouterError
from web_router.providers.auto import AutoProvider
from web_router.registry import register_provider, unregister_provider
from web_router.types import SearchRequest, SearchResponse

NAMED, AUTO = "named", "auto"
"""How ``wrapper`` calls ``fake``: by name, or as the one member of an ``auto`` chain."""


@pytest.fixture
def inner() -> dict[str, Any]:
    """How ``wrapper`` makes its inner call: ``budget``, its own ``budget=``; ``catch``, whether it swallows errors."""
    return {"budget": None, "catch": False}


@pytest.fixture(params=[NAMED, AUTO])
def router(
    request: pytest.FixtureRequest, inner: dict[str, Any], fake_provider: object, priced: Table
) -> Iterator[WebRouter]:
    """A router with ``wrapper`` ($0.02), whose search calls ``fake`` ($0.01) on the same router."""
    made = WebRouter()

    def _search(self: Any, search: SearchRequest) -> SearchResponse:
        provider = "fake" if request.param == NAMED else AutoProvider(chain=[("fake", {})])
        try:
            made.search("inner", provider=provider, budget=inner["budget"])
        except WebRouterError:
            if not inner["catch"]:
                raise
        return FakeProvider._respond(self, search)

    _register("wrapper", _search)
    priced.update(wrapper=(0.02, None), fake=(0.01, None))
    yield made
    unregister_provider("wrapper")


def _register(name: str, search: Any) -> None:
    register_provider(name, type(name.title(), (FakeProvider,), {"name": name, "_search": search}))


def test_a_nested_call_on_the_same_router_is_charged_once_to_the_shared_budget(router: WebRouter) -> None:
    with router.budget(Budget(max_cost_usd=1.0)) as shared:
        router.search("q", provider="wrapper")
        shared.search("q", provider="wrapper")
    assert (shared.status().spent_usd, shared.status().reserved_usd) == (0.06, 0.0)


@pytest.mark.parametrize("router", [AUTO], indirect=True)
def test_a_nested_auto_call_counts_toward_the_outer_calls_own_cap(router: WebRouter) -> None:
    response = router.search("q", provider="wrapper", budget=Budget(max_cost_usd=1.0))
    assert response.decision.estimated_cost_usd == pytest.approx(0.03)
    with pytest.raises(WebRouterError):  # the outer cap refuses fake; an inner auto call raises its own error
        router.search("q", provider="wrapper", budget=Budget(max_cost_usd=0.025))


def test_inside_a_shared_budget_a_nested_call_still_counts_toward_the_outer_calls_own_cap(router: WebRouter) -> None:
    with router.budget(Budget(max_cost_usd=1.0)) as shared:
        with pytest.raises(WebRouterError):  # the outer cap refuses fake; an inner auto call raises its own error
            router.search("q", provider="wrapper", budget=Budget(max_cost_usd=0.025))
        response = router.search("q", provider="wrapper", budget=Budget(max_cost_usd=1.0))
    assert response.decision.estimated_cost_usd == pytest.approx(0.03)
    assert (shared.status().spent_usd, shared.status().reserved_usd) == (0.05, 0.0)


def test_an_inner_call_its_own_budget_refuses_is_charged_to_no_one(router: WebRouter, inner: dict[str, Any]) -> None:
    inner.update(budget=Budget(max_cost_usd=0.005), catch=True)
    alone = router.search("q", provider="wrapper", budget=Budget(max_cost_usd=1.0))
    with router.budget(Budget(max_cost_usd=1.0)) as shared:
        scoped = router.search("q", provider="wrapper")
    assert (alone.decision.estimated_cost_usd, scoped.decision.estimated_cost_usd) == (0.02, 0.02)
    assert (shared.status().spent_usd, shared.status().reserved_usd) == (0.02, 0.0)
