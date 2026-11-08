"""A shared budget's clock, scopes inside one another, and calls on the router inside the block.

The rest of a shared budget's rules are in :mod:`tests.budget.test_shared`.
"""

from __future__ import annotations

import threading
import time

import pytest

from tests.budget.conftest import ARGS, Chain, Table, pool_in_force
from web_router import Budget, BudgetExceededError, BudgetScope, ResearchTimeoutError, WebRouter
from web_router.routing.pool import chain_in_force

SHARED = Budget(max_cost_usd=0.10)


def _scoped(chain: Chain, scope: BudgetScope, **options: object) -> object:
    """``chain``'s tool through ``scope``, on the chain's ``auto``."""
    return getattr(scope, chain.tool.name)(ARGS[chain.tool.name], provider=chain.auto(), **options)


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


# -- Nesting ------------------------------------------------------------------


@pytest.mark.parametrize("tool", ["search"], indirect=True)
def test_a_scope_inside_another_spends_from_both(chain: Chain, priced: Table) -> None:
    priced.update(first=(0.06, None))
    router = WebRouter()
    with router.budget(SHARED) as outer, router.budget(Budget(max_cost_usd=1.0)) as inner:
        _scoped(chain, inner)
        with pytest.raises(BudgetExceededError, match=r"\$0\.04 left;"):
            _scoped(chain, inner)
    assert (outer.status().spent_usd, inner.status().spent_usd) == (0.06, 0.06)
    assert (outer.status().reserved_usd, inner.status().reserved_usd) == (0.0, 0.0), "the refusal released the inner"


def test_a_nested_scopes_status_counts_what_its_parent_has_left() -> None:
    router = WebRouter()
    with router.budget(SHARED) as outer, router.budget(Budget(max_cost_usd=1.0)) as inner:
        chain = chain_in_force(router)
        assert chain is not None
        assert chain.reserve(0.06) is None
        assert (inner.status().remaining_usd, outer.status().remaining_usd) == (0.04, 0.04)
    with router.budget(Budget()) as uncapped, router.budget(Budget(max_cost_usd=1.0)) as capped:
        assert (uncapped.status().remaining_usd, capped.status().remaining_usd) == (None, 1.0)


@pytest.mark.parametrize("tool", ["search"], indirect=True)
def test_a_scope_made_before_an_outer_block_is_entered_still_spends_within_the_outer_cap(
    chain: Chain, priced: Table
) -> None:
    priced.update(first=(1.0, None))
    router = WebRouter()
    inner = router.budget(Budget(max_cost_usd=5.0))
    with router.budget(Budget(max_cost_usd=0.01)) as outer:
        with inner, pytest.raises(BudgetExceededError):
            _scoped(chain, inner)
        with pytest.raises(BudgetExceededError):
            _scoped(chain, inner)  # called through, not entered
    assert chain.called == []
    assert (outer.status().spent_usd, inner.status().spent_usd) == (0.0, 0.0)


# -- Calls on the router inside the block ---------------------------------------


@pytest.mark.parametrize("tool", ["search"], indirect=True)
def test_calls_on_the_router_inside_the_block_spend_from_it_and_after_it_do_not(chain: Chain, priced: Table) -> None:
    priced.update(first=(0.04, None))
    router = WebRouter()
    with router.budget(SHARED) as shared:
        inside = router.search("q", provider=chain.auto())
    after = router.search("q", provider=chain.auto())
    assert (inside.decision.estimated_cost_usd, after.decision) == (0.04, None)
    assert shared.status().spent_usd == 0.04
    assert pool_in_force(router) is None


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


def test_a_scope_made_before_an_outer_block_reports_what_the_outer_cap_has_left_even_unentered() -> None:
    router = WebRouter()
    inner = router.budget(Budget(max_cost_usd=5.0))
    with router.budget(SHARED):
        assert inner.status().remaining_usd == 0.1, "as a call through it would be held to"
    assert inner.status().remaining_usd == 5.0
