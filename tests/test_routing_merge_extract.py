"""Rule 7 after the option checks: extract whose router-supplied URLs were all dropped searches instead."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.merge_helpers import merge_with
from web_router import Budget, RouteDecision, prices
from web_router.constants_merge import MSG_EXTRACT_NO_URL, NOTE_EXTRACT
from web_router.routing.url_policy import any_web_url

CAP = Budget(max_cost_usd=0.05)


def _urls(n: int) -> RouteDecision:
    urls = [f"https://site.test/{i}" for i in range(n)]
    return RouteDecision(tools=["extract"], params={"extract": {"urls": urls}})


@pytest.fixture(autouse=True)
def cent_a_page(monkeypatch: pytest.MonkeyPatch) -> None:
    row = SimpleNamespace(estimate_usd=0.01, estimate_is_floor=False, mode=None, results_assumed=None)
    monkeypatch.setattr(prices, "price", lambda *_: row)


def test_extract_whose_router_urls_are_dropped_for_cost_searches_instead() -> None:
    merged = merge_with(_urls(50), budget=CAP, url_check=any_web_url)
    assert merged.tool == "search" and merged.params == {}
    assert merged.notes.ignored[NOTE_EXTRACT] == MSG_EXTRACT_NO_URL
    assert "cost cap" in merged.notes.ignored["params.extract.urls"]
    assert merged.notes.merged[0].startswith("search:")


def test_extract_whose_router_urls_fit_the_budget_stays_extract() -> None:
    merged = merge_with(_urls(1), budget=CAP, url_check=any_web_url)
    assert (merged.tool, merged.params) == ("extract", {"urls": ["https://site.test/0"]})


def test_a_pinned_extract_is_never_moved_to_search() -> None:
    merged = merge_with(_urls(50), pinned="extract", budget=CAP, url_check=any_web_url)
    assert merged.tool == "extract" and merged.params == {}
