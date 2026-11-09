"""Exa search: how each shared option reaches the Search API.

The fields come from exa.ai/docs/reference/search; the live tests at the
bottom are what confirm each one changes what comes back.
"""

from __future__ import annotations

import os
from datetime import date, timedelta

import pytest

from web_router.providers._clock import pinned_today
from web_router.providers.exa import ExaProvider
from web_router.types import SearchRequest


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> ExaProvider:
    monkeypatch.setenv("EXA_API_KEY", "test-key")
    return ExaProvider()


def test_filters_and_country_are_sent_under_exa_names(provider: ExaProvider) -> None:
    body = provider._build_payload(
        SearchRequest(query="hi", include_domains=["a.com"], exclude_domains=["b.com"], country="GB")
    )
    assert body == {
        "query": "hi",
        "contents": {"text": True},
        "includeDomains": ["a.com"],
        "excludeDomains": ["b.com"],
        "userLocation": "GB",
    }


@pytest.mark.parametrize(("recency", "start"), [("day", "2026-11-08"), ("week", "2026-11-02"), ("year", "2025-11-09")])
def test_recency_becomes_a_published_start_counted_back_from_today(
    provider: ExaProvider, recency: str, start: str
) -> None:
    with pinned_today(date(2026, 11, 9)):
        body = provider._build_payload(SearchRequest(query="hi", recency=recency))  # type: ignore[arg-type]
    assert body["startPublishedDate"] == f"{start}T00:00:00.000Z"


@pytest.mark.parametrize(
    ("depth", "contents"),
    [
        ("lite", {"highlights": {"maxCharacters": 500}}),
        ("fast", {"text": {"maxCharacters": 3000}}),
        ("deep", {"text": True}),
    ],
)
def test_each_depth_asks_for_contents_of_its_size(provider: ExaProvider, depth: str, contents: dict) -> None:
    request = SearchRequest(query="hi", search_depth=depth)  # type: ignore[arg-type]
    provider.validate(request)
    assert provider._build_payload(request)["contents"] == contents


@pytest.mark.parametrize(("effort", "search_type"), [("low", "lite"), ("high", "deep-lite"), ("max", "deep")])
def test_effort_picks_the_search_type(provider: ExaProvider, effort: str, search_type: str) -> None:
    assert provider._build_payload(SearchRequest(query="hi", effort=effort))["type"] == search_type  # type: ignore[arg-type]


def test_medium_effort_sends_nothing_since_auto_is_exas_default(provider: ExaProvider) -> None:
    assert "type" not in provider._build_payload(SearchRequest(query="hi", effort="medium"))


def test_lite_type_still_carries_every_filter(provider: ExaProvider) -> None:
    """Exa's lite type takes the domain, location and date filters, so low effort refuses none of them."""
    request = SearchRequest(
        query="hi", effort="low", include_domains=["a.com"], country="US", recency="week", search_depth="lite"
    )
    provider.validate(request)
    body = provider._build_payload(request)
    assert body["type"] == "lite" and {"includeDomains", "userLocation", "startPublishedDate"} <= set(body)


def test_highlights_alone_fill_content_and_description(provider: ExaProvider) -> None:
    payload = {"results": [{"url": "https://a.com", "highlights": ["a passage"]}]}
    result = provider._to_response(SearchRequest(query="hi", search_depth="lite"), payload).results[0]
    assert result.content == "a passage" and result.description == "a passage"


live = pytest.mark.skipif(not os.getenv("EXA_API_KEY"), reason="EXA_API_KEY not set")


@pytest.mark.integration
@live
@pytest.mark.parametrize(("depth", "most"), [("lite", 600), ("fast", 3000)])
def test_live_capped_depths_stay_within_their_size(depth: str, most: int) -> None:
    from tests.conftest import billing_aware
    from web_router import WebRouter

    with WebRouter("exa") as router, billing_aware("exa"):
        response = router.search("what is retrieval augmented generation", max_results=3, search_depth=depth)
    assert response.results
    assert all(0 < len(result.content) <= most for result in response.results)


@pytest.mark.integration
@live
def test_live_deep_returns_more_than_fast() -> None:
    from tests.conftest import billing_aware
    from web_router import WebRouter

    with WebRouter("exa") as router, billing_aware("exa"):
        response = router.search("what is retrieval augmented generation", max_results=3, search_depth="deep")
    assert max(len(result.content) for result in response.results) > 3000


@pytest.mark.integration
@live
def test_live_recency_keeps_to_the_window() -> None:
    from tests.conftest import billing_aware
    from web_router import WebRouter

    with WebRouter("exa") as router, billing_aware("exa"):
        response = router.search("technology news", max_results=5, recency="month")
    dated = [result.published_date for result in response.results if result.published_date]
    assert dated, "no result carried a date to check"
    cutoff = (date.today() - timedelta(days=31)).isoformat()
    assert all(value[:10] >= cutoff for value in dated)
