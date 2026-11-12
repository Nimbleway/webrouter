"""``auto`` under a cost cap: cheapest first among the backends the benchmark cannot tell apart.

The members are the harness's three fakes; the benchmark data the order reads
is set per test, as the prices are (see :mod:`tests.budget.conftest`). Only
search and answer are ranked in the shipped data; a method given data here is
reordered the same way.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.budget.conftest import Chain, Table, call
from web_router import Budget
from web_router.providers.auto import AutoProvider
from web_router.routing import cost_order
from web_router.routing.cost_order import cost_ordered
from web_router.types import SearchRequest

CAP = Budget(max_cost_usd=1.0)


def _benchmark(monkeypatch: pytest.MonkeyPatch, tool: str, floor: float, **accuracy: float) -> None:
    """The benchmark behind ``tool``'s order: each member's accuracy, and the method's noise floor."""
    measured = {name: {"accuracy": value} for name, value in accuracy.items()}
    data: dict[str, Any] = {tool: {"noise_floor": {"value": floor}, "measured": measured}}
    monkeypatch.setattr(cost_order, "chains", lambda: data)


def test_within_the_noise_floor_the_cheapest_runs_first(monkeypatch: pytest.MonkeyPatch, priced: Table) -> None:
    _benchmark(monkeypatch, "search", 0.02, first=0.50, second=0.49, third=0.40)
    priced.update(first=(0.008, None), second=(0.005, None), third=(0.0, None))
    order = cost_ordered("search", ["first", "second", "third"], SearchRequest(query="q"))
    assert order == ["second", "first", "third"], "third is free but measurably worse: it stays in its own tier"


def test_free_is_cheapest_and_an_unknown_price_goes_last_in_a_tier(
    monkeypatch: pytest.MonkeyPatch, priced: Table
) -> None:
    _benchmark(monkeypatch, "search", 0.05, first=0.50, second=0.49, third=0.48)
    priced.update(second=(0.005, None), third=(0.0, None))
    order = cost_ordered("search", ["first", "second", "third"], SearchRequest(query="q"))
    assert order == ["third", "second", "first"]


def test_unranked_members_keep_their_places_after_the_ranked_ones(
    monkeypatch: pytest.MonkeyPatch, priced: Table
) -> None:
    _benchmark(monkeypatch, "search", 0.05, second=0.5, third=0.5)
    priced.update(first=(0.0, None), second=(0.009, None), third=(0.001, None))
    order = cost_ordered("search", ["first", "second", "third"], SearchRequest(query="q"))
    assert order == ["third", "second", "first"]


def test_a_method_the_benchmark_did_not_rank_keeps_its_order(monkeypatch: pytest.MonkeyPatch, priced: Table) -> None:
    _benchmark(monkeypatch, "search", 0.05, first=0.5, second=0.5)
    priced.update(first=(0.9, None), second=(0.1, None))
    assert cost_ordered("extract", ["first", "second"], SearchRequest(query="q")) == ["first", "second"]


def test_a_capped_call_on_the_default_order_runs_in_cost_order_and_records_it(
    chain: Chain, priced: Table, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = chain.tool.name
    _benchmark(monkeypatch, tool, 0.05, first=0.5, second=0.5, third=0.5)
    monkeypatch.setattr(cost_order, "_default_chain", lambda _chain: True)
    priced.update(first=(0.008, None), second=(0.002, None), third=(0.005, None))
    response = call(chain, CAP)
    assert (response.provider, chain.called[0]) == ("second", "second")
    assert response.decision.merged == [f"{tool}:{name}" for name in ("second", "third", "first")]


def test_with_no_cost_cap_the_static_order_runs(chain: Chain, priced: Table, monkeypatch: pytest.MonkeyPatch) -> None:
    _benchmark(monkeypatch, chain.tool.name, 0.05, first=0.5, second=0.5, third=0.5)
    monkeypatch.setattr(cost_order, "_default_chain", lambda _chain: True)
    priced.update(first=(0.008, None), second=(0.002, None), third=(0.005, None))
    response = call(chain, Budget(time_limit_s=60))
    assert response.provider == "first"
    assert response.decision.merged is None


def test_a_chain_the_caller_set_runs_as_given(chain: Chain, priced: Table, monkeypatch: pytest.MonkeyPatch) -> None:
    _benchmark(monkeypatch, chain.tool.name, 0.05, first=0.5, second=0.5, third=0.5)
    priced.update(first=(0.008, None), second=(0.002, None), third=(0.005, None))
    assert call(chain, CAP).provider == "first"


def test_only_the_default_chain_counts_as_the_default() -> None:
    assert cost_order._default_chain(AutoProvider())
    assert not cost_order._default_chain(AutoProvider(chain=["you-mcp", "exa-mcp"]))
    assert not cost_order._default_chain(object())


def test_a_chain_with_the_default_names_but_its_own_options_counts_as_set() -> None:
    default = AutoProvider()
    names = [name for name, _ in default._chain]
    assert not cost_order._default_chain(AutoProvider(chain=[(name, {"timeout": 9}) for name in names]))
