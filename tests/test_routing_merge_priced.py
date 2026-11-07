"""The merge rule prices each option a router sets as the call would run it: the attempt rule, on every backend."""

from __future__ import annotations

from typing import Any

from web_router import Budget, Candidate, RouteDecision, RouteRequest
from web_router.routing.merge import Merged, merge


def _merge(tool: Any, provider: str, params: dict[str, Any], budget: Budget, urls: tuple[str, ...] = ()) -> Merged:
    request = RouteRequest(
        prompt="q",
        candidates=(Candidate(tool=tool, provider=provider),),
        deadline_s=1.0,
        urls=urls,
        budget=budget,
        pinned_tool=tool,
    )
    decision = RouteDecision(params={tool: params})
    return merge(request, decision, {tool: (provider,)}, url_check=lambda url: None)


def test_a_router_asking_more_results_than_a_row_prices_is_refused_under_a_cost_cap() -> None:
    capped = Budget(max_cost_usd=1.0)
    assert _merge("search", "exa", {"max_results": 5}, capped).params == {"max_results": 5}
    refused = _merge("search", "exa", {"max_results": 50}, capped)
    assert refused.params == {}
    assert refused.notes.ignored["params.search.max_results"] == (
        "search:exa with max_results=50 has no known price, so it never fits a cost cap"
    )
    assert _merge("search", "exa", {"max_results": 50}, Budget(time_limit_s=60.0)).params == {"max_results": 50}


def test_a_routers_urls_are_priced_per_page() -> None:
    urls = [f"https://example.com/{n}" for n in range(20)]
    capped = Budget(max_cost_usd=0.05)
    assert _merge("extract", "firecrawl", {"urls": urls[:5]}, capped).params == {"urls": urls[:5]}  # $0.025
    refused = _merge("extract", "firecrawl", {"urls": urls}, capped)  # 20 pages at $0.005
    assert refused.params == {}
    why = refused.notes.ignored["params.extract.urls"]
    assert why == "extract:firecrawl with urls=20 URLs is estimated at $0.1, over the $0.05 cost cap"


def test_an_effort_and_a_depth_are_priced_by_the_same_rule() -> None:
    capped = Budget(max_cost_usd=0.01)
    assert _merge("research", "parallel", {"effort": "low"}, capped).params == {"effort": "low"}  # $0.005
    high = _merge("research", "parallel", {"effort": "high"}, capped)
    assert high.notes.ignored["params.research.effort"].startswith("research:parallel with effort=high is estimated")
