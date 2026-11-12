"""Keirolabs provider tests.

The payload below was recorded from the live lite endpoint on 2026-11-12
(``python asyncio tutorial``, ``maxResults: 2``), with the account's credit
balance left out. Keirolabs answers ``score: null`` here and sends no date.
"""

from __future__ import annotations

import os

import httpx
import pytest

from web_router.exceptions import UnsupportedParameterError
from web_router.providers.keirolabs import KeirolabsProvider
from web_router.types import SearchRequest

RECORDED = {
    "results": [
        {
            "title": "Python's asyncio: A Hands-On Walkthrough \u2013 Real Python",
            "url": "https://realpython.com/async-io-python/",
            "snippet": "In this tutorial, you'll learn how Python asyncio works, how to define and run coroutines.",
            "domain": "realpython.com",
            "position": 1,
            "score": None,
        },
        {
            "title": "Asyncio in Python - Full Tutorial - YouTube",
            "url": "https://www.youtube.com/watch?v=Qb9s3UiMSTA",
            "snippet": "Python Tutorial: AsyncIO - Complete Guide to Asynchronous Programming with Animations.",
            "domain": "www.youtube.com",
            "position": 2,
            "score": None,
        },
    ],
    "query": "python asyncio tutorial",
    "type": "web",
    "count": 2,
    "creditsCharged": 0.0375,
    "serverLatencyMs": 1255,
}


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> KeirolabsProvider:
    monkeypatch.setenv("KEIROLABS_API_KEY", "test-key")
    return KeirolabsProvider()


def test_max_results_is_sent_as_max_results_camel_case(provider: KeirolabsProvider) -> None:
    assert provider._build_payload(SearchRequest(query="ai search", max_results=4)) == {
        "query": "ai search",
        "maxResults": 4,
    }


def test_a_bare_search_sends_the_query_alone_and_provider_options_reach_the_body(
    provider: KeirolabsProvider,
) -> None:
    assert provider._build_payload(SearchRequest(query="q")) == {"query": "q"}
    request = SearchRequest(query="q", provider_options={"maxResults": 20})
    assert provider._build_payload(request) == {"query": "q", "maxResults": 20}


def test_the_key_travels_as_a_bearer_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KEIROLABS_API_KEY", "test-key")
    sent: list[httpx.Request] = []
    provider = KeirolabsProvider(
        transport=httpx.MockTransport(lambda r: sent.append(r) or httpx.Response(200, json=RECORDED))
    )

    response = provider.search(SearchRequest(query="q"))

    assert sent[0].method == "POST" and sent[0].url.path == "/api/v2/search/lite"
    assert sent[0].url.host == "api.keirolabs.cloud"
    assert sent[0].headers["authorization"] == "Bearer test-key"
    assert len(response.results) == 2


@pytest.mark.parametrize(
    "option",
    [
        {"include_domains": ["a.com"]},
        {"exclude_domains": ["b.com"]},
        {"recency": "week"},
        {"country": "US"},
        {"locale": "en"},
        {"search_depth": "lite"},
        {"effort": "low"},
    ],
)
def test_an_unmapped_option_is_refused_rather_than_dropped(provider: KeirolabsProvider, option: dict) -> None:
    with pytest.raises(UnsupportedParameterError, match=next(iter(option))):
        provider.validate(SearchRequest(query="q", **option))


def test_results_map_and_nothing_is_invented(provider: KeirolabsProvider) -> None:
    response = provider._to_response(SearchRequest(query="q"), RECORDED)
    first, second = response.results

    assert response.provider == "keirolabs" and response.raw == RECORDED
    assert first.url == "https://realpython.com/async-io-python/"
    assert first.title == "Python's asyncio: A Hands-On Walkthrough \u2013 Real Python"
    assert first.description == first.content == RECORDED["results"][0]["snippet"]
    assert first.score is None and first.published_date is None
    assert first.extra == {"domain": "realpython.com", "position": 1}
    assert second.extra["position"] == 2


def test_a_numeric_score_is_kept_and_a_result_without_a_link_is_dropped(provider: KeirolabsProvider) -> None:
    payload = {
        "results": [
            {"title": "T", "url": "https://e.com", "snippet": None, "score": 0.5},
            {"title": "no link", "url": "", "snippet": "s"},
        ]
    }
    (result,) = provider._to_response(SearchRequest(query="q"), payload).results
    assert (result.url, result.score, result.description, result.content) == ("https://e.com", 0.5, "", "")


@pytest.mark.integration
def test_live_search_returns_results() -> None:
    if not os.environ.get("KEIROLABS_API_KEY"):
        pytest.skip("KEIROLABS_API_KEY not set")
    response = KeirolabsProvider().search(SearchRequest(query="python asyncio tutorial", max_results=3))
    assert 0 < len(response.results) <= 3
    assert all(result.url.startswith("http") for result in response.results)
