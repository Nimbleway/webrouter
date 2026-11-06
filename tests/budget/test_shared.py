"""A shared budget's rules: each call's budget, what is charged and settled, the clock, nesting and scope.

As in :mod:`tests.budget.test_cost`, on the static order through the public
routers, with prices and p90s the test sets. Parallel calls are in
:mod:`tests.budget.test_shared_parallel`.
"""

from __future__ import annotations

import threading
import time

import pytest

from tests.budget.conftest import ARGS, Chain, Table
from web_router import (
    AsyncBudgetScope,
    AsyncWebRouter,
    Budget,
    BudgetExceededError,
    BudgetScope,
    DecisionHop,
    ResearchTimeoutError,
    WebRouter,
)
from web_router.constants_budget import SKIP_OVER_SHARED, SKIP_ROUTE_REFUSED
from web_router.exceptions import ConfigurationError
from web_router.routing.attempts import CallRoute, call_route
from web_router.routing.pool import BudgetPool, current_pool

SHARED = Budget(max_cost_usd=0.10)


def _scoped(chain: Chain, scope: BudgetScope, **options: object) -> object:
    """``chain``'s tool through ``scope``, on the chain's ``auto``."""
    return getattr(scope, chain.tool.name)(ARGS[chain.tool.name], provider=chain.auto(), **options)


def test_the_scope_has_every_budgeted_method_on_both_routers() -> None:
    methods = ("search", "extract", "answer", "research", "browse")
    assert all(callable(getattr(scope, name)) for scope in (BudgetScope, AsyncBudgetScope) for name in methods)
    assert WebRouter.budget.__doc__ is not None


def test_calls_through_the_scope_spend_from_it_on_every_tool(chain: Chain, priced: Table) -> None:
    priced.update(first=(0.04, None))
    with WebRouter().budget(SHARED) as shared:
        _scoped(chain, shared)
        _scoped(chain, shared)
        with pytest.raises(BudgetExceededError, match=r"est\. \$0\.04 > \$0\.02 left in the shared budget"):
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
    left = SKIP_OVER_SHARED.format(cost="$0.06", left="$0.04")
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
    error = "est. $0.08 > $0.02 left in the shared budget"
    assert response.decision.hops == [DecisionHop(id="search:first", error=error)]
    assert shared.status().spent_usd == 0.085


@pytest.mark.parametrize("tool", ["search"], indirect=True)
def test_calls_on_the_router_inside_the_block_spend_from_it_and_after_it_do_not(chain: Chain, priced: Table) -> None:
    priced.update(first=(0.04, None))
    router = WebRouter()
    with router.budget(SHARED) as shared:
        inside = router.search("q", provider=chain.auto())
    after = router.search("q", provider=chain.auto())
    assert (inside.decision.estimated_cost_usd, after.decision) == (0.04, None)
    assert shared.status().spent_usd == 0.04
    assert current_pool() is None


@pytest.mark.parametrize("tool", ["search"], indirect=True)
def test_a_thread_started_inside_the_block_does_not_see_it_unless_it_calls_through_the_scope(
    chain: Chain, priced: Table
) -> None:
    priced.update(first=(0.04, None))
    router = WebRouter()
    seen: list[object] = []
    with router.budget(SHARED) as shared:
        worker = threading.Thread(target=lambda: seen.append(router.search("q", provider=chain.auto()).decision))
        worker.start()
        worker.join()
    assert (seen, shared.status().spent_usd) == ([None], 0.0)


# -- The clock -----------------------------------------------------------------


@pytest.mark.parametrize("tool", ["search"], indirect=True)
def test_the_clock_runs_from_when_the_scope_is_made(chain: Chain, priced: Table) -> None:
    with WebRouter().budget(Budget(time_limit_s=0.05)) as shared:
        time.sleep(0.06)
        with pytest.raises(BudgetExceededError, match="search:auto skipped: no time left"):
            _scoped(chain, shared)
    assert chain.called == []
    assert shared.status().time_left_s == 0.0
    assert shared.status().elapsed_s >= 0.06


@pytest.mark.parametrize("tool", ["search"], indirect=True)
def test_a_call_gets_the_time_left_in_the_scope_or_its_own_limit_when_shorter(chain: Chain, priced: Table) -> None:
    priced.update(first=(None, 9.0), second=(None, 3.0), third=(None, 1.0))
    with WebRouter().budget(Budget(time_limit_s=5.0)) as shared:
        _scoped(chain, shared)
        _scoped(chain, shared, budget=Budget(time_limit_s=2.0))
    assert chain.called == ["second", "third"]


@pytest.mark.parametrize("tool", ["research"], indirect=True)
def test_a_job_running_when_the_clock_runs_out_hands_back_its_handle_and_its_estimate_counts(
    chain: Chain, priced: Table
) -> None:
    chain.stalls.add("first")
    priced.update(first=(0.04, None))
    shared = WebRouter().budget(Budget(max_cost_usd=0.10, time_limit_s=0.2))
    with shared, pytest.raises(ResearchTimeoutError) as raised:
        _scoped(chain, shared, timeout=600)
    assert raised.value.job_id.startswith("first")
    assert (shared.status().spent_usd, shared.status().reserved_usd) == (0.04, 0.0)


@pytest.mark.parametrize("tool", ["search"], indirect=True)
def test_a_strict_scope_drops_an_unknown_p90_for_every_call_in_it(chain: Chain, priced: Table) -> None:
    priced.update(second=(None, 1.0))
    with WebRouter().budget(Budget(time_limit_s=60, strict=True)) as shared:
        _scoped(chain, shared)
    assert chain.called == ["second"]


# -- Nesting, async, and the pool on its own -----------------------------------


@pytest.mark.parametrize("tool", ["search"], indirect=True)
def test_a_scope_inside_another_spends_from_both(chain: Chain, priced: Table) -> None:
    priced.update(first=(0.06, None))
    router = WebRouter()
    with router.budget(SHARED) as outer, router.budget(Budget(max_cost_usd=1.0)) as inner:
        _scoped(chain, inner)
        with pytest.raises(BudgetExceededError, match=r"\$0\.04 left in the shared budget"):
            _scoped(chain, inner)
    assert (outer.status().spent_usd, inner.status().spent_usd) == (0.06, 0.06)
    assert (outer.status().reserved_usd, inner.status().reserved_usd) == (0.0, 0.0), "the refusal released the inner"


@pytest.mark.parametrize("tool", ["answer"], indirect=True)
async def test_an_async_scope_spends_as_a_sync_one_does(chain: Chain, priced: Table) -> None:
    priced.update(first=(0.07, None), second=(0.02, None))
    async with AsyncWebRouter().budget(SHARED) as shared:
        await shared.answer("q", provider=chain.auto())
        response = await shared.answer("q", provider=chain.auto())
    assert chain.called == ["first", "second"]
    error = "est. $0.07 > $0.03 left in the shared budget"
    assert response.decision.hops == [DecisionHop(id="answer:first", error=error)]
    assert shared.status().spent_usd == 0.09
    assert current_pool() is None


def test_a_pool_with_nothing_set_caps_nothing_and_reports_no_remainder() -> None:
    pool = BudgetPool(Budget())
    assert pool.reserve(5.0) is None
    pool.settle(5.0)
    status = pool.status()
    assert (status.spent_usd, status.remaining_usd, status.time_left_s) == (5.0, None, None)
    assert pool.limit(None) == Budget()
