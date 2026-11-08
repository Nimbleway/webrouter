"""What one attempt is estimated to cost: the ledger's price rule, read from the shipped price list."""

from __future__ import annotations

import pytest

from web_router import Budget, prices
from web_router.constants_budget import SKIP_PRICE_UNKNOWN
from web_router.routing.attempts import Attempt
from web_router.routing.ledger import Ledger, attempt_cost
from web_router.types import SearchRequest


def _search(provider: str, depth: str | None, max_results: int | None) -> Attempt:
    request = SearchRequest(query="q", search_depth=depth, max_results=max_results)
    return Attempt(tool="search", provider=provider, request=request)


@pytest.mark.parametrize(("provider", "depth"), [("nimble", "deep"), ("you", "deep"), ("exa", None)])
def test_a_search_asking_more_results_than_its_price_assumes_is_unpriced(provider: str, depth: str | None) -> None:
    row = prices.price(provider, "search", depth)
    assert row is not None and row.results_assumed == 10
    within, unset = _search(provider, depth, 10), _search(provider, depth, None)
    assert attempt_cost(within) == attempt_cost(unset) == row.estimate_usd
    assert attempt_cost(_search(provider, depth, 11)) is None, "each result past the tenth is charged"


def test_under_a_cost_cap_a_deep_search_for_fifty_results_is_not_admitted_at_the_ten_result_price() -> None:
    ledger = Ledger(Budget(max_cost_usd=1.0))
    assert not ledger.admit(_search("nimble", "deep", 50))
    assert ledger.skipped == {"search:nimble": SKIP_PRICE_UNKNOWN}
    assert ledger.admit(_search("nimble", "deep", 10))
    assert ledger.spent == pytest.approx(0.015)


def test_a_row_that_charges_no_result_keeps_its_price_for_any_count() -> None:
    assert prices.price("nimble", "search", None).results_assumed is None
    assert attempt_cost(_search("nimble", None, 50)) == prices.estimate("nimble", "search")
