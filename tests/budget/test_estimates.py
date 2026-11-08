"""What one attempt is estimated to cost and take, on the shipped price list or a test's own."""

from __future__ import annotations

import pytest

from tests.budget.conftest import Table
from web_router import Budget, prices
from web_router.constants_budget import SKIP_PRICE_UNKNOWN
from web_router.routing.attempts import Attempt
from web_router.routing.ledger import Ledger, attempt_cost, attempt_p90
from web_router.types import BrowseRequest, ExtractRequest, ResearchRequest, SearchRequest
from web_router.types_answer import AnswerRequest


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


def test_a_search_is_priced_at_its_depth_and_research_at_its_effort() -> None:
    deep = Attempt(tool="search", provider="nimble", request=SearchRequest(query="q", search_depth="deep"))
    assert attempt_cost(deep) == pytest.approx(0.015)
    low = Attempt(tool="research", provider="parallel", request=ResearchRequest(query="q", effort="low"))
    assert attempt_cost(low) == pytest.approx(0.005)
    unknown = Attempt(tool="research", provider="nimble", request=ResearchRequest(query="q"))
    assert attempt_cost(unknown) is None, "nimble's default effort is priced unknown"


def test_an_extract_is_priced_per_url(priced: Table) -> None:
    priced.update(tavily=(0.008, None))
    two = Attempt(tool="extract", provider="tavily", request=ExtractRequest(urls=["https://a.test", "https://b.test"]))
    assert attempt_cost(two) == pytest.approx(0.016)


def test_the_p90_comes_from_the_measured_latency() -> None:
    measured = Attempt(tool="search", provider="nimble", request=SearchRequest(query="q"))
    assert attempt_p90(measured) == pytest.approx(1.331)
    assert attempt_p90(Attempt(tool="research", provider="parallel", request=ResearchRequest(query="q"))) is None


def test_a_row_whose_estimate_is_only_a_floor_has_an_unknown_price() -> None:
    steps = Attempt(tool="browse", provider="browser-use", request=BrowseRequest(task="t"))
    tokens = Attempt(tool="answer", provider="brave", request=AnswerRequest(query="q"))
    assert [attempt_cost(steps), attempt_cost(tokens)] == [None, None], "per-step and token charges come on top"
    assert prices.estimate("browser-use", "browse") == pytest.approx(0.01), "the list still states the floor"
