"""The candidate builder's pricing and budget prefilter: each candidate's estimates, and what a cap drops."""

from __future__ import annotations

import pytest

from tests.candidates_helpers import clear_keys, ids, one, set_keys
from web_router import Budget, WebRouter
from web_router.prices import estimate, estimate_latency
from web_router.routing.candidates import build_candidates, default_requests
from web_router.types import ExtractRequest, ResearchRequest, SearchRequest


@pytest.fixture
def keys(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """No key in the environment; a test sets the ones it needs."""
    return clear_keys(monkeypatch)


def test_an_answer_candidate_carries_its_price_and_p90(keys: pytest.MonkeyPatch) -> None:
    set_keys(keys, "linkup")
    found = build_candidates(WebRouter(), default_requests("q"))
    answer = one(found, "answer:linkup")
    assert answer.est_cost_usd == estimate("linkup", "answer")
    assert answer.est_latency_s == estimate_latency("linkup", "answer")[1]  # type: ignore[index]
    assert "answer:linkup" not in found.unmeasured  # ranked by route-eval, priced and timed


def test_extract_cost_is_per_page(keys: pytest.MonkeyPatch) -> None:
    urls = ["https://a.test", "https://b.test", "https://c.test"]
    found = build_candidates(WebRouter(), {"extract": ExtractRequest(urls=urls)})
    assert one(found, "extract:tavily").est_cost_usd == pytest.approx(3 * estimate("tavily", "extract"))  # type: ignore[operator]


def test_search_is_priced_at_the_depth_the_caller_set(keys: pytest.MonkeyPatch) -> None:
    set_keys(keys, "nimble")
    found = build_candidates(WebRouter(), {"search": SearchRequest(query="q", search_depth="deep")})
    assert one(found, "search:nimble").est_cost_usd == estimate("nimble", "search", "deep")


@pytest.mark.parametrize("asked", [None, 10])
def test_a_deep_search_within_its_priced_results_keeps_its_estimate_under_a_cost_cap(
    keys: pytest.MonkeyPatch, asked: int | None
) -> None:
    set_keys(keys, "nimble", "you")
    request = {"search": SearchRequest(query="q", search_depth="deep", max_results=asked)}
    found = build_candidates(WebRouter(), request, budget=Budget(max_cost_usd=0.03))
    assert one(found, "search:nimble").est_cost_usd == one(found, "search:you").est_cost_usd == 0.015


def test_a_deep_search_asking_more_results_than_its_price_assumes_is_unpriced(keys: pytest.MonkeyPatch) -> None:
    set_keys(keys, "nimble", "you")
    request = {"search": SearchRequest(query="q", search_depth="deep", max_results=50)}
    found = build_candidates(WebRouter(), request, budget=Budget(max_cost_usd=0.03))
    for candidate_id in ("search:nimble", "search:you"):
        assert candidate_id not in ids(found)  # each result past the tenth is charged: not $0.015
        assert "no known price" in found.dropped[candidate_id]
    uncapped = build_candidates(WebRouter(), request)
    assert one(uncapped, "search:nimble").est_cost_usd is None
    assert "search:nimble" in uncapped.unmeasured


def test_no_budget_drops_nothing(keys: pytest.MonkeyPatch) -> None:
    set_keys(keys, "webiq", "exa")
    found = build_candidates(WebRouter(), {"search": SearchRequest(query="q")})
    assert one(found, "search:webiq").est_cost_usd is None  # unknown price, kept: no cap
    assert found.dropped == {}


def test_a_cost_cap_drops_known_estimates_over_what_is_left(keys: pytest.MonkeyPatch) -> None:
    set_keys(keys, "brave", "exa")
    found = build_candidates(WebRouter(), {"search": SearchRequest(query="q")}, budget=Budget(max_cost_usd=0.01))
    assert "search:brave" in ids(found)  # $0.005
    assert "search:exa" not in ids(found)  # $0.017
    assert "over" in found.dropped["search:exa"]


def test_a_cost_cap_drops_unknown_prices(keys: pytest.MonkeyPatch) -> None:
    set_keys(keys, "webiq", "brave")
    found = build_candidates(WebRouter(), {"search": SearchRequest(query="q")}, budget=Budget(max_cost_usd=10.0))
    assert "search:webiq" not in ids(found)
    assert "no known price" in found.dropped["search:webiq"]
    assert "search:brave" in ids(found)


def test_research_without_effort_is_priced_at_its_default_effort_while_it_fits(keys: pytest.MonkeyPatch) -> None:
    set_keys(keys, "linkup")
    request = {"research": ResearchRequest(query="q")}
    assert estimate("linkup", "research") == 1.5  # its default, high
    roomy = build_candidates(WebRouter(), request, budget=Budget(max_cost_usd=2.0))
    assert one(roomy, "research:linkup").est_cost_usd == 1.5
    # Under $1 the default does not fit, but measured low ($0.25) does: Linkup stays a candidate, at low.
    tight = build_candidates(WebRouter(), request, budget=Budget(max_cost_usd=1.0))
    assert one(tight, "research:linkup").est_cost_usd == estimate("linkup", "research", "low") == 0.25


def test_provider_options_make_the_price_unknown_under_a_cost_cap(keys: pytest.MonkeyPatch) -> None:
    set_keys(keys, "brave")
    request = {"search": SearchRequest(query="q", provider_options={"extra_snippets": True})}
    found = build_candidates(WebRouter(), request, budget=Budget(max_cost_usd=10.0))
    assert "search:brave" not in ids(found)
    assert "no known price" in found.dropped["search:brave"]


def test_provider_options_make_the_price_unknown_with_no_cost_cap_too_and_mark_it_unmeasured(
    keys: pytest.MonkeyPatch,
) -> None:
    set_keys(keys, "brave")
    plain = build_candidates(WebRouter(), {"search": SearchRequest(query="q")})
    assert "search:brave" not in plain.unmeasured
    request = {"search": SearchRequest(query="q", provider_options={"extra_snippets": True})}
    brave = one(found := build_candidates(WebRouter(), request), "search:brave")
    assert (brave.est_cost_usd, brave.est_latency_s) == (None, None), "priced and timed as the ledger reads it"
    assert "search:brave" in found.unmeasured


def test_research_with_a_pinned_effort_is_priced_at_that_effort(keys: pytest.MonkeyPatch) -> None:
    set_keys(keys, "linkup")
    assert estimate("linkup", "research", "low") == 0.25  # the default, high, is $1.5
    budget = Budget(max_cost_usd=1.0)
    pinned = build_candidates(WebRouter(), {"research": ResearchRequest(query="q", effort="low")}, budget=budget)
    assert one(pinned, "research:linkup").est_cost_usd == 0.25
    high = build_candidates(WebRouter(), {"research": ResearchRequest(query="q", effort="high")}, budget=budget)
    assert high.dropped["research:linkup"].startswith("estimated $1.5")


def test_a_time_cap_drops_a_known_p90_that_does_not_fit(keys: pytest.MonkeyPatch) -> None:
    set_keys(keys, "brave", "exa")
    found = build_candidates(WebRouter(), {"search": SearchRequest(query="q")}, budget=Budget(time_limit_s=2.0))
    assert "search:brave" in ids(found)  # p90 under a second
    assert "search:exa" not in ids(found)  # p90 over six seconds
    assert "p90" in found.dropped["search:exa"]


def test_an_unknown_p90_research_candidate_stays_under_a_time_cap(keys: pytest.MonkeyPatch) -> None:
    set_keys(keys, "linkup")
    found = build_candidates(WebRouter(), {"research": ResearchRequest(query="q")}, budget=Budget(time_limit_s=1.0))
    candidate = one(found, "research:linkup")
    assert candidate.est_latency_s is None
    assert "research:linkup" in found.unmeasured


def test_strict_drops_unknown_p90_under_a_time_cap(keys: pytest.MonkeyPatch) -> None:
    set_keys(keys, "linkup", "brave")
    requests = {"search": SearchRequest(query="q"), "research": ResearchRequest(query="q")}
    found = build_candidates(WebRouter(), requests, budget=Budget(time_limit_s=60.0, strict=True))
    assert "research:linkup" not in ids(found)
    assert "strict" in found.dropped["research:linkup"]
    assert "search:brave" in ids(found)  # measured, and it fits
    # Strict speaks only to time: with no time cap it drops nothing.
    assert "research:linkup" in ids(build_candidates(WebRouter(), requests, budget=Budget(strict=True)))


def test_provider_options_make_the_p90_unknown_under_a_time_cap(keys: pytest.MonkeyPatch) -> None:
    set_keys(keys, "brave")
    request = {"search": SearchRequest(query="q", provider_options={"extra_snippets": True})}
    found = build_candidates(WebRouter(), request, budget=Budget(time_limit_s=10.0))
    assert one(found, "search:brave").est_latency_s is None
    strict = build_candidates(WebRouter(), request, budget=Budget(time_limit_s=10.0, strict=True))
    assert "strict" in strict.dropped["search:brave"]
