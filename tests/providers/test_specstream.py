"""SpecStream provider tests.

``tests/fixtures/specstream/web_search.json`` is a reply recorded from the live
``GET /v1/web-search`` on 2026-11-12, with the key removed and trimmed to four
of its 18 sources (the row's source positions renumbered to match).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx
import pytest

from web_router.exceptions import ProviderError, UnsupportedParameterError
from web_router.providers.specstream import SpecStreamProvider
from web_router.types import SearchRequest

RECORDED_SEARCH: dict[str, Any] = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "specstream" / "web_search.json").read_text(encoding="utf-8")
)


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> SpecStreamProvider:
    monkeypatch.setenv("SPECSTREAM_API_KEY", "test-key")
    return SpecStreamProvider()


def test_the_query_goes_as_q_and_max_results_as_num(provider: SpecStreamProvider) -> None:
    assert provider._build_payload(SearchRequest(query="ai search", max_results=4)) == {"q": "ai search", "num": 4}
    assert provider._build_payload(SearchRequest(query="ai search")) == {"q": "ai search"}
    assert provider._build_payload(SearchRequest(query="ai search", max_results=500))["num"] == 100


def test_provider_options_reach_the_query_string(provider: SpecStreamProvider) -> None:
    request = SearchRequest(query="hi", provider_options={"passages": "true", "speed": "fast"})
    assert provider._build_payload(request) == {"q": "hi", "passages": "true", "speed": "fast"}


def test_the_key_travels_as_a_bearer_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPECSTREAM_API_KEY", "test-key")
    sent: list[httpx.Request] = []
    provider = SpecStreamProvider(
        transport=httpx.MockTransport(lambda r: sent.append(r) or httpx.Response(200, json=RECORDED_SEARCH))
    )

    response = provider.search(SearchRequest(query="tallest building", max_results=4))

    assert sent[0].method == "GET"
    assert sent[0].url.host == "api.specstream.ai" and sent[0].url.path == "/v1/web-search"
    assert dict(sent[0].url.params) == {"q": "tallest building", "num": "4"}
    assert sent[0].headers["authorization"] == "Bearer test-key"
    assert len(response.results) == 4


@pytest.mark.parametrize(
    "option",
    [
        {"include_domains": ["a.com"]},
        {"exclude_domains": ["b.com"]},
        {"recency": "week"},
        {"country": "US"},
        {"locale": "en"},
        {"search_depth": "deep"},
        {"effort": "low"},
    ],
)
def test_an_unmapped_option_is_refused_rather_than_dropped(provider: SpecStreamProvider, option: dict) -> None:
    with pytest.raises(UnsupportedParameterError, match=next(iter(option))):
        provider.validate(SearchRequest(query="q", **option))


def test_the_recorded_reply_maps_from_data_sources(provider: SpecStreamProvider) -> None:
    response = provider._to_response(SearchRequest(query="tallest building"), RECORDED_SEARCH)
    first, second = response.results[:2]

    assert response.provider == "specstream" and response.raw == RECORDED_SEARCH
    assert first.url == "https://www.burjkhalifa.ae/en/the-tower/facts-figures/"
    assert first.title == "what building is the tallest in the world"
    # An empty snippet stays empty: nothing is made up for it, and no score is invented.
    assert (first.description, first.content, first.score) == ("", "", None)
    assert second.url == "https://www.businessinsider.com/the-tallest-buildings-in-the-world-2015-12"
    assert second.description == second.content and second.description.startswith("Sep 21, 2020 - In September")
    assert second.published_date == "2020-09-21"
    assert second.extra["site"] == "businessinsider.com" and second.extra["cited"] is True
    assert "snippet" not in second.extra and "published" not in second.extra


def test_with_passages_the_excerpt_is_the_content(provider: SpecStreamProvider) -> None:
    hit = {"url": "https://a.com", "title": "A", "snippet": "s", "excerpt": "page text", "published": "2026-11-01"}
    (result,) = provider._to_response(SearchRequest(query="q"), {"data": {"sources": [hit]}}).results
    assert (result.description, result.content, result.published_date) == ("s", "page text", "2026-11-01")
    assert "excerpt" not in result.extra


@pytest.mark.parametrize("body", [{}, {"data": {}}, {"data": {"sources": None}}, {"results": []}])
def test_a_reply_without_a_sources_list_is_an_error_not_an_empty_search(
    monkeypatch: pytest.MonkeyPatch, body: dict
) -> None:
    monkeypatch.setenv("SPECSTREAM_API_KEY", "test-key")
    provider = SpecStreamProvider(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body)))
    with pytest.raises(ProviderError, match="data.sources"):
        provider.search(SearchRequest(query="q"))


def test_an_empty_sources_list_is_a_search_that_found_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPECSTREAM_API_KEY", "test-key")
    body = {"data": {"sources": [], "rows": []}}
    provider = SpecStreamProvider(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body)))
    assert provider.search(SearchRequest(query="q")).results == []


@pytest.mark.integration
def test_live_search_returns_web_results() -> None:
    if not os.environ.get("SPECSTREAM_API_KEY"):
        pytest.skip("SPECSTREAM_API_KEY not set")
    response = SpecStreamProvider().search(SearchRequest(query="python asyncio tutorial", max_results=3))
    assert 0 < len(response.results) <= 3
    assert all(result.url.startswith("http") for result in response.results)
