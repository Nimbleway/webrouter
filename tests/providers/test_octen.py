"""Octen provider tests.

Mapped from Octen's OpenAPI 3.1 spec (docs.octen.ai/api-reference/search); no
account was available, so the live test skips and the mapping is
documentation-derived. The payload below is the spec's example response.
"""

from __future__ import annotations

import os

import httpx
import pytest

from web_router.exceptions import ProviderError, UnsupportedParameterError
from web_router.providers.octen import OctenProvider
from web_router.types import SearchRequest

DOCUMENTED = {
    "code": 0,
    "msg": "success",
    "request_id": "req_abc123def456",
    "data": {
        "query": "latest WHO guidance on influenza vaccination",
        "results": [
            {
                "title": "Influenza (Seasonal) - World Health Organization (WHO)",
                "url": "https://www.who.int/news-room/fact-sheets/detail/influenza-(seasonal)",
                "highlight": "WHO recommends annual vaccination for high-risk groups ...",
                "authors": "World Health Organization",
                "time_published": "2024-10-15T00:00:00Z",
                "time_last_crawled": "2026-01-20T02:12:34Z",
                "favicon": "https://www.who.int/favicon.ico",
            },
            {
                "title": "A page with its body",
                "url": "https://example.org/page",
                "highlight": "The matching passage.",
                "full_content": "The whole page body.",
            },
        ],
    },
    "meta": {"usage": {"num_search_queries": 1, "full_content_extra_count": 0}, "latency": 237, "warning": None},
}


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> OctenProvider:
    monkeypatch.setenv("OCTEN_API_KEY", "test-key")
    return OctenProvider()


def test_every_mapped_option_reaches_its_field(provider: OctenProvider) -> None:
    request = SearchRequest(
        query="flu",
        max_results=8,
        include_domains=["who.int"],
        exclude_domains=["example.com"],
        locale="en-GB",
        recency="month",
        search_depth="deep",
    )
    assert provider._build_payload(request) == {
        "query": "flu",
        "count": 8,
        "include_domains": ["who.int"],
        "exclude_domains": ["example.com"],
        "language": ["en"],
        "time_range": "month",
        "full_content": {"enable": True, "max_tokens": 100_000},
    }


def test_fast_turns_full_content_on_at_octens_default_and_a_bare_search_sends_the_query(
    provider: OctenProvider,
) -> None:
    assert provider._build_payload(SearchRequest(query="q", search_depth="fast"))["full_content"] == {"enable": True}
    assert provider._build_payload(SearchRequest(query="q")) == {"query": "q"}


def test_max_results_is_held_to_octens_range(provider: OctenProvider) -> None:
    assert provider._build_payload(SearchRequest(query="q", max_results=500))["count"] == 100


def test_the_key_is_a_bearer_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTEN_API_KEY", "test-key")
    sent: list[httpx.Request] = []
    provider = OctenProvider(
        transport=httpx.MockTransport(lambda r: sent.append(r) or httpx.Response(200, json=DOCUMENTED))
    )

    response = provider.search(SearchRequest(query="flu"))

    assert sent[0].method == "POST" and sent[0].url.path == "/search"
    assert sent[0].headers["Authorization"] == "Bearer test-key"
    assert response.request_id == "req_abc123def456"


@pytest.mark.parametrize("option", [{"country": "US"}, {"effort": "low"}, {"search_depth": "lite"}])
def test_an_unmapped_option_is_refused_rather_than_dropped(provider: OctenProvider, option: dict) -> None:
    with pytest.raises(UnsupportedParameterError, match=next(iter(option))):
        provider.validate(SearchRequest(query="q", **option))


def test_a_language_octen_does_not_list_is_refused(provider: OctenProvider) -> None:
    provider.validate(SearchRequest(query="q", locale="ja"))
    with pytest.raises(UnsupportedParameterError, match="filters by the languages it lists"):
        provider.validate(SearchRequest(query="q", locale="he"))


def test_results_map_and_nothing_is_invented(provider: OctenProvider) -> None:
    response = provider._to_response(SearchRequest(query="flu"), DOCUMENTED)
    first, second = response.results

    assert response.provider == "octen" and response.raw == DOCUMENTED
    assert first.title == "Influenza (Seasonal) - World Health Organization (WHO)" and first.score is None
    assert first.description == first.content == "WHO recommends annual vaccination for high-risk groups ..."
    assert first.published_date == "2024-10-15T00:00:00Z"
    assert first.extra["authors"] == "World Health Organization" and "highlight" not in first.extra
    assert (second.description, second.content, second.published_date) == (
        "The matching passage.",
        "The whole page body.",
        None,
    )


def test_a_nonzero_code_raises(provider: OctenProvider) -> None:
    with pytest.raises(ProviderError, match="code 4001: bad"):
        provider._to_response(SearchRequest(query="q"), {"code": 4001, "msg": "bad", "request_id": "r"})


@pytest.mark.integration
def test_live_search_returns_results() -> None:
    if not os.environ.get("OCTEN_API_KEY"):
        pytest.skip("OCTEN_API_KEY not set")
    response = OctenProvider().search(SearchRequest(query="python asyncio tutorial", max_results=3))
    assert 0 < len(response.results) <= 3
    assert all(result.url.startswith("http") for result in response.results)
