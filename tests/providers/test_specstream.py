"""SpecStream provider tests."""

from __future__ import annotations

import httpx
import pytest

from web_router.exceptions import ProviderError, UnsupportedParameterError
from web_router.providers.specstream import SpecStreamProvider
from web_router.types import SearchRequest

RECORDED = {
    "results": [
        {
            "title": "SpecStream - Live Web Data Engine",
            "url": "https://specstream.ai",
            "snippet": "SpecStream is the live web data engine.",
            "score": 0.95,
        },
    ],
    "query": "specstream",
}


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> SpecStreamProvider:
    monkeypatch.setenv("SPECSTREAM_API_KEY", "test-key")
    return SpecStreamProvider()


def test_max_results_and_search_depth_are_sent_as_query_parameters(provider: SpecStreamProvider) -> None:
    # SpecStream search is GET, so params instead of JSON
    assert provider._build_payload(SearchRequest(query="ai search", max_results=4, search_depth="lite")) == {
        "query": "ai search",
        "max_results": 4,
        "search_depth": "lite",
    }


def test_the_key_travels_as_a_bearer_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPECSTREAM_API_KEY", "test-key")
    sent: list[httpx.Request] = []
    provider = SpecStreamProvider(
        transport=httpx.MockTransport(lambda r: sent.append(r) or httpx.Response(200, json=RECORDED))
    )

    response = provider.search(SearchRequest(query="q"))

    assert sent[0].method == "GET"
    assert sent[0].url.path == "/v1/web-search"
    assert sent[0].url.host == "api.specstream.ai"
    assert sent[0].headers["authorization"] == "Bearer test-key"
    assert len(response.results) == 1


@pytest.mark.parametrize(
    "option",
    [
        {"include_domains": ["a.com"]},
        {"exclude_domains": ["b.com"]},
        {"recency": "week"},
        {"country": "US"},
        {"locale": "en"},
        {"effort": "low"},
    ],
)
def test_an_unmapped_option_is_refused_rather_than_dropped(provider: SpecStreamProvider, option: dict) -> None:
    with pytest.raises(UnsupportedParameterError, match=next(iter(option))):
        provider.validate(SearchRequest(query="q", **option))


def test_results_map(provider: SpecStreamProvider) -> None:
    response = provider._to_response(SearchRequest(query="q"), RECORDED)
    (first,) = response.results

    assert response.provider == "specstream" and response.raw == RECORDED
    assert first.url == "https://specstream.ai"
    assert first.title == "SpecStream - Live Web Data Engine"
    assert first.description == first.content == "SpecStream is the live web data engine."
    assert first.score == 0.95


@pytest.mark.parametrize("body", [{}, {"error": "quota exceeded"}, {"results": None}])
def test_a_reply_without_a_results_list_is_an_error_not_an_empty_search(
    monkeypatch: pytest.MonkeyPatch, body: dict
) -> None:
    monkeypatch.setenv("SPECSTREAM_API_KEY", "test-key")
    provider = SpecStreamProvider(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body)))
    with pytest.raises(ProviderError, match="results"):
        provider.search(SearchRequest(query="q"))


def test_an_empty_results_list_is_a_search_that_found_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPECSTREAM_API_KEY", "test-key")
    provider = SpecStreamProvider(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"results": []})))
    assert provider.search(SearchRequest(query="q")).results == []
