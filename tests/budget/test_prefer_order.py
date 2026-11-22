"""``prefer``'s order on ``auto``: backends by estimated cost or p90 latency, with or without a router or a cap.

The members are the harness's three fakes, their prices and p90s set per test
(see :mod:`tests.budget.conftest`); ``auto``'s default order is assumed, as a
caller who sets no ``chain`` gets it.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.budget.conftest import ARGS, Chain, Table
from tests.routing_helpers import MEMBERS, Ranker
from web_router import Budget, WebRouter
from web_router.providers.auto import AutoResearchProvider
from web_router.routing import cost_order
from web_router.routing.prefer_order import preferred
from web_router.types import SearchRequest


@pytest.fixture
def default_order(monkeypatch: pytest.MonkeyPatch, priced: Table) -> Table:
    """``auto`` read as running its default order; ``first`` dearest and slowest, ``second`` unpriced but quick."""
    monkeypatch.setattr(cost_order, "_runs_default", lambda _chain: True)
    priced.update(first=(0.008, 9.0), second=(None, 1.0), third=(0.001, None))
    return priced


def _call(chain: Chain, prefer: Any, budget: Budget | None = None, router: Any = "rules") -> Any:
    method = getattr(WebRouter(prefer=prefer, router=router), chain.tool.name)
    return method(ARGS[chain.tool.name], provider=chain.auto(), budget=budget)


@pytest.mark.parametrize(
    ("prefer", "expected"), [("cheap", ["third", "first", "second"]), ("fast", ["second", "first", "third"])]
)
def test_each_preference_sorts_by_its_estimate_unknowns_last(
    default_order: Table, prefer: Any, expected: list[str]
) -> None:
    assert preferred("search", list(MEMBERS), SearchRequest(query="q"), prefer) == expected


def test_free_comes_first_and_ties_keep_the_static_order(priced: Table) -> None:
    priced.update(first=(0.002, None), second=(0.0, None), third=(0.002, None))
    assert preferred("search", list(MEMBERS), SearchRequest(query="q"), "cheap") == ["second", "first", "third"]


@pytest.mark.usefixtures("default_order")
def test_every_method_runs_in_the_preferred_order_with_no_cap_and_records_it(chain: Chain) -> None:
    response = _call(chain, "cheap")
    assert (response.provider, chain.called[0]) == ("third", "third")
    assert response.decision.merged == [f"{chain.tool.name}:{name}" for name in ("third", "first", "second")]
    assert response.decision.prefer == "cheap"
    assert response.decision.decision_id and response.decision.prompt_sha256


@pytest.mark.usefixtures("default_order")
@pytest.mark.parametrize("tool", ["search"], indirect=True)
def test_with_no_preference_and_no_cap_the_static_order_runs(chain: Chain) -> None:
    response = _call(chain, None)
    assert response.provider == "first"
    assert response.decision is None


@pytest.mark.parametrize("tool", ["search"], indirect=True)
def test_a_preference_replaces_the_accuracy_tiers_under_a_cap(
    chain: Chain, priced: Table, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = {
        "search": {"noise_floor": {"value": 0.01}, "measured": {"first": {"accuracy": 0.9}, "third": {"accuracy": 0.4}}}
    }
    monkeypatch.setattr(cost_order, "chains", lambda: data)
    monkeypatch.setattr(cost_order, "_runs_default", lambda _chain: True)
    priced.update(first=(0.008, None), second=(0.005, None), third=(0.001, None))
    cap = Budget(max_cost_usd=1.0)
    assert _call(chain, None, cap).provider == "first", "capped, no preference: the more accurate tier first"
    chain.called.clear()
    assert _call(chain, "cheap", cap).provider == "third"


@pytest.mark.parametrize("tool", ["search"], indirect=True)
def test_a_chain_the_caller_set_runs_as_given(chain: Chain, priced: Table) -> None:
    priced.update(first=(0.008, None), second=(0.005, None), third=(0.001, None))
    assert _call(chain, "cheap").provider == "first"


@pytest.mark.usefixtures("default_order")
@pytest.mark.parametrize("tool", ["search"], indirect=True)
def test_a_router_that_ranks_nothing_leaves_the_preferred_order(chain: Chain) -> None:
    response = _call(chain, "fast", router=Ranker())
    assert response.provider == "second"
    assert response.decision.prefer == "fast"


@pytest.mark.usefixtures("default_order")
@pytest.mark.parametrize("tool", ["search"], indirect=True)
def test_a_router_that_ranks_the_backends_keeps_its_order(chain: Chain) -> None:
    assert _call(chain, "cheap", router=Ranker("first")).provider == "first"


@pytest.mark.usefixtures("default_order", "chain")
@pytest.mark.parametrize("tool", ["search"], indirect=True)
def test_a_plan_on_the_default_router_is_in_the_preferred_order(chain: Chain) -> None:
    router = WebRouter(provider_config={"auto": {"chain": list(MEMBERS)}}, prefer="cheap")
    plan = router.plan("q")
    assert (plan.providers, plan.decision.prefer) == ({"search": ("third", "first", "second")}, "cheap")
    assert router.run(plan).provider == "third"


def test_a_budgeted_research_call_keeps_the_mapped_effort_beside_the_order(
    research_backends: Any, priced: Table, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("dear", "cheap"):
        research_backends(name)
    priced.update(dear=(0.02, None), cheap=(0.01, None))
    monkeypatch.setattr(cost_order, "_runs_default", lambda _chain: True)
    auto = AutoResearchProvider(chain=[("dear", {}), ("cheap", {})])
    response = WebRouter(prefer="cheap").research("q", provider=auto, budget=Budget(max_cost_usd=1.0))
    record = response.decision
    assert (response.provider, record.merged, record.prefer) == ("cheap", ["research:cheap", "research:dear"], "cheap")
    assert record.params_source == "budget", "the budget's effort mapping is kept beside the preferred order"
