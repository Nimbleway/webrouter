"""A shared budget's rules: each call's budget, and what is charged and settled.

As in :mod:`tests.budget.test_cost`, on the static order through the public
routers, with prices and p90s the test sets. The clock, nesting and calls on
the router inside the block are in :mod:`tests.budget.test_shared_clock`;
parallel calls in :mod:`tests.budget.test_shared_parallel`.
"""

from __future__ import annotations

import pytest

from tests.budget.conftest import ARGS, Chain, Table, pool_in_force
from web_router import (
    AsyncWebRouter,
    Budget,
    BudgetExceededError,
    BudgetScope,
    DecisionHop,
    WebRouter,
)
from web_router.constants_budget import SKIP_OVER_COST, SKIP_ROUTE_REFUSED
from web_router.exceptions import ConfigurationError
from web_router.routing.attempts import CallRoute, call_route
from web_router.routing.pool import BudgetPool, PoolChain

SHARED = Budget(max_cost_usd=0.10)


def _scoped(chain: Chain, scope: BudgetScope, **options: object) -> object:
    """``chain``'s tool through ``scope``, on the chain's ``auto``."""
    return getattr(scope, chain.tool.name)(ARGS[chain.tool.name], provider=chain.auto(), **options)


async def test_calls_through_an_async_scope_spend_from_it_on_every_tool(chain: Chain, priced: Table) -> None:
    priced.update(first=(0.06, None))
    async with AsyncWebRouter().budget(SHARED) as shared:
        scoped = getattr(shared, chain.tool.name)
        await scoped(ARGS[chain.tool.name], provider=chain.auto())
        with pytest.raises(BudgetExceededError):
            await scoped(ARGS[chain.tool.name], provider=chain.auto())
    assert chain.called == ["first"]
    assert shared.status().spent_usd == 0.06


def test_another_routers_calls_inside_the_block_neither_spend_from_it_nor_are_refused_by_it(
    priced: Table, fake_provider: object
) -> None:
    priced.update(fake=(0.01, None))
    mine, other = WebRouter(), WebRouter()
    with mine.budget(Budget(max_cost_usd=0.001)) as shared:
        response = other.search("q", provider="fake")
        with other.budget(Budget(max_cost_usd=1.0)) as theirs:
            other.search("q", provider="fake")
            assert theirs.status().remaining_usd == 0.99, "a scope on another router does not nest in this one"
        with pytest.raises(BudgetExceededError):
            mine.search("q", provider="fake")
    assert response.decision is None, "the other router's call ran as it would outside any budget"
    assert (shared.status().spent_usd, theirs.status().spent_usd) == (0.0, 0.01)


def test_calls_through_the_scope_spend_from_it_on_every_tool(chain: Chain, priced: Table) -> None:
    priced.update(first=(0.04, None))
    with WebRouter().budget(SHARED) as shared:
        _scoped(chain, shared)
        _scoped(chain, shared)
        with pytest.raises(BudgetExceededError, match=r"est\. \$0\.04 > \$0\.02 left;"):
            _scoped(chain, shared)
    assert chain.called == ["first", "first"]
    assert shared.status().spent_usd == 0.08


def test_a_named_backend_in_a_scope_reserves_and_settles_its_estimate_and_one_that_does_not_fit_never_runs(
    chain: Chain, priced: Table
) -> None:
    priced.update(second=(0.06, None))
    with WebRouter().budget(SHARED) as shared:
        response = _scoped_named(chain, shared, "second")
        with pytest.raises(BudgetExceededError) as raised:
            _scoped_named(chain, shared, "second")
    assert chain.called == ["second"]
    assert response.decision.estimated_cost_usd == 0.06
    left = SKIP_OVER_COST.format(cost="$0.06", left="$0.04")
    assert raised.value.skipped == {f"{chain.tool.name}:second": left}
    assert (shared.status().spent_usd, shared.status().reserved_usd) == (0.06, 0.0)


@pytest.mark.parametrize("tool", ["search"], indirect=True)
def test_a_named_backend_an_outer_route_refuses_is_never_reserved(chain: Chain, priced: Table) -> None:
    priced.update(second=(0.06, None))
    refuse_all = CallRoute(before_attempt=lambda attempt: False)
    shared = WebRouter().budget(SHARED)
    with shared, call_route(refuse_all), pytest.raises(BudgetExceededError) as raised:
        _scoped_named(chain, shared, "second")
    assert raised.value.skipped == {"search:second": SKIP_ROUTE_REFUSED}
    assert (chain.called, shared.status().spent_usd, shared.status().reserved_usd) == ([], 0.0, 0.0)


def _scoped_named(chain: Chain, scope: BudgetScope, name: str) -> object:
    return getattr(scope, chain.tool.name)(ARGS[chain.tool.name], provider=name)


@pytest.mark.parametrize("tool", ["research"], indirect=True)
def test_an_unknown_price_research_call_in_a_ten_cent_scope_is_skipped_and_a_known_price_one_runs(
    chain: Chain, priced: Table
) -> None:
    priced.update(second=(0.05, None))  # first's price is unknown
    with WebRouter().budget(SHARED) as shared:
        response = _scoped(chain, shared)
    assert chain.called == ["second"]
    assert response.decision.hops == [DecisionHop(id="research:first", error="price unknown under a cost cap")]
    assert (shared.status().spent_usd, shared.status().reserved_usd) == (0.05, 0.0)


@pytest.mark.parametrize("tool", ["search"], indirect=True)
def test_every_attempt_a_call_makes_is_charged_to_the_scope_failed_ones_included(chain: Chain, priced: Table) -> None:
    chain.raises["first"] = ConfigurationError("first holds no key")
    priced.update(first=(0.02, None), second=(0.03, None))
    with WebRouter().budget(SHARED) as shared:
        _scoped(chain, shared)
    assert chain.called == ["first", "second"]
    assert shared.status().spent_usd == 0.05


@pytest.mark.parametrize("tool", ["search"], indirect=True)
def test_a_call_whose_own_budget_is_tighter_keeps_it(chain: Chain, priced: Table) -> None:
    priced.update(first=(0.05, None), second=(0.005, None))
    with WebRouter().budget(SHARED) as shared:
        response = _scoped(chain, shared, budget=Budget(max_cost_usd=0.01))
    assert chain.called == ["second"]
    assert response.decision.hops == [DecisionHop(id="search:first", error="est. $0.05 > $0.01 left")]


@pytest.mark.parametrize("tool", ["search"], indirect=True)
def test_a_call_whose_own_budget_is_looser_is_held_to_what_is_left_in_the_scope(chain: Chain, priced: Table) -> None:
    priced.update(first=(0.08, None), second=(0.005, None))
    with WebRouter().budget(SHARED) as shared:
        _scoped(chain, shared)
        response = _scoped(chain, shared, budget=Budget(max_cost_usd=1.0))
    assert chain.called == ["first", "second"]
    error = SKIP_OVER_COST.format(cost="$0.08", left="$0.02")
    assert response.decision.hops == [DecisionHop(id="search:first", error=error)]
    assert shared.status().spent_usd == 0.085


# -- Async, and the pool on its own ---------------------------------------------


@pytest.mark.parametrize("tool", ["answer"], indirect=True)
async def test_an_async_scope_spends_as_a_sync_one_does(chain: Chain, priced: Table) -> None:
    priced.update(first=(0.07, None), second=(0.02, None))
    async with AsyncWebRouter().budget(SHARED) as shared:
        await shared.answer("q", provider=chain.auto())
        response = await shared.answer("q", provider=chain.auto())
    assert chain.called == ["first", "second"]
    error = SKIP_OVER_COST.format(cost="$0.07", left="$0.03")
    assert response.decision.hops == [DecisionHop(id="answer:first", error=error)]
    assert shared.status().spent_usd == 0.09
    assert pool_in_force(shared.pool.owner) is None


def test_a_pool_with_nothing_set_caps_nothing_and_reports_no_remainder() -> None:
    pool = BudgetPool(Budget())
    chain = PoolChain((pool,))
    assert chain.reserve(5.0) is None
    chain.settle(5.0)
    status = pool.status()
    assert (status.spent_usd, status.remaining_usd, status.time_left_s) == (5.0, None, None)
    assert chain.limit(None) == Budget()


def test_a_calls_cost_cap_is_what_the_shared_budget_has_left_not_its_whole_cap() -> None:
    pool = BudgetPool(Budget(max_cost_usd=1.00))
    chain = PoolChain((pool,))
    assert chain.reserve(0.95) is None
    chain.settle(0.95)
    assert chain.limit(None).max_cost_usd == pytest.approx(0.05)
    assert chain.limit(Budget(max_cost_usd=0.01)).max_cost_usd == 0.01, "a tighter cap of the call's own still holds"
