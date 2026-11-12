"""Seltz provider tests.

Mapped from Seltz's OpenAPI 3.1 spec (docs.seltz.ai/api-reference/openapi.json);
no account was available, so the live test skips and the mapping is
documentation-derived. The payload below follows the spec's ``SearchResponse``
and ``Document`` schemas: no title and no score, which is why none are mapped.
"""

from __future__ import annotations

import os

import httpx
import pytest

from web_router.exceptions import UnsupportedParameterError
from web_router.providers.seltz import SeltzProvider
from web_router.types import SearchRequest

DOCUMENTED = {
    "request_id": "req_123",
    "documents": [
        {
            "url": "https://example.com/article",
            "published_date": "2024-03-15T00:00:00Z",
            "content": "A long document body. " * 40,
            "snippets": [{"text": "The passage that matched."}, {"text": "A second passage."}],
            "properties": {"site": "example.com"},
        },
        {"url": "https://example.org/page", "published_date": None, "content": None, "snippets": []},
    ],
}


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> SeltzProvider:
    monkeypatch.setenv("SELTZ_API_KEY", "test-key")
    return SeltzProvider()


def test_every_mapped_option_reaches_its_field(provider: SeltzProvider) -> None:
    request = SearchRequest(
        query="ai search", max_results=4, include_domains=["a.com"], exclude_domains=["b.com"], recency="month"
    )
    assert provider._build_payload(request) == {
        "query": "ai search",
        "max_results": 4,
        "include_domains": ["a.com"],
        "exclude_domains": ["b.com"],
        "from_date": "now-1M",
    }


@pytest.mark.parametrize(("recency", "offset"), [("day", "now-1d"), ("week", "now-7d"), ("year", "now-1y")])
def test_recency_is_an_offset_from_now(provider: SeltzProvider, recency: str, offset: str) -> None:
    assert provider._build_payload(SearchRequest(query="q", recency=recency))["from_date"] == offset


def test_a_bare_search_sends_the_query_alone_and_provider_options_reach_the_body(provider: SeltzProvider) -> None:
    assert provider._build_payload(SearchRequest(query="q")) == {"query": "q"}
    request = SearchRequest(query="q", provider_options={"scope": "news", "tier": "base"})
    assert provider._build_payload(request) == {"query": "q", "scope": "news", "tier": "base"}


def test_the_key_travels_in_its_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SELTZ_API_KEY", "test-key")
    sent: list[httpx.Request] = []
    provider = SeltzProvider(
        transport=httpx.MockTransport(lambda r: sent.append(r) or httpx.Response(200, json=DOCUMENTED))
    )

    response = provider.search(SearchRequest(query="q"))

    assert sent[0].method == "POST" and sent[0].url.path == "/v1/search"
    assert sent[0].headers["x-api-key"] == "test-key"
    assert response.request_id == "req_123"


@pytest.mark.parametrize("option", [{"country": "US"}, {"locale": "en"}, {"search_depth": "lite"}, {"effort": "low"}])
def test_an_unmapped_option_is_refused_rather_than_dropped(provider: SeltzProvider, option: dict) -> None:
    with pytest.raises(UnsupportedParameterError, match=next(iter(option))):
        provider.validate(SearchRequest(query="q", **option))


def test_results_map_and_nothing_is_invented(provider: SeltzProvider) -> None:
    response = provider._to_response(SearchRequest(query="q"), DOCUMENTED)
    first, second = response.results

    assert response.provider == "seltz" and response.raw == DOCUMENTED
    assert first.url == "https://example.com/article" and first.title == "" and first.score is None
    assert first.description == "The passage that matched."
    assert first.content == DOCUMENTED["documents"][0]["content"]
    assert first.published_date == "2024-03-15T00:00:00Z"
    assert first.extra["properties"] == {"site": "example.com"} and len(first.extra["snippets"]) == 2
    assert (second.description, second.content, second.published_date) == ("", "", None)


def test_without_snippets_the_description_is_the_opening_of_the_body(provider: SeltzProvider) -> None:
    body = "word " * 200
    (result,) = provider._to_response(
        SearchRequest(query="q"), {"documents": [{"url": "https://e.com", "content": body}]}
    ).results
    assert result.content == body
    assert body.startswith(result.description.rstrip("…")) and len(result.description) < len(body)


@pytest.mark.integration
def test_live_search_returns_documents() -> None:
    if not os.environ.get("SELTZ_API_KEY"):
        pytest.skip("SELTZ_API_KEY not set")
    response = SeltzProvider().search(SearchRequest(query="python asyncio tutorial", max_results=3))
    assert 0 < len(response.results) <= 3
    assert all(result.url.startswith("http") for result in response.results)
