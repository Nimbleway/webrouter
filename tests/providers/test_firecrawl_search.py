"""Firecrawl search provider tests.

Both payloads were recorded from the live Search API on 2026-11-24
(``python asyncio tutorial``): ``RECORDED`` with ``limit: 3``, ``RECORDED_DEEP``
with ``limit: 2`` and ``scrapeOptions``, keeping its first result. Descriptions
and the page body are cut short, the scraped page's metadata keeps five of its
keys, and the first description is padded back past a snippet's length to stand
for Firecrawl's long ones (live, it ran to 628 characters, the third to 51,831).
The shapes are Firecrawl's own.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import pytest

from web_router.exceptions import ConfigurationError, ProviderError, UnsupportedParameterError
from web_router.providers.firecrawl import FirecrawlProvider
from web_router.types import SearchRequest, SearchResponse

RECORDED: dict[str, Any] = {
    "success": True,
    "data": {
        "web": [
            {
                "url": "https://docs.python.org/3/howto/a-conceptual-overview-of-asyncio.html",
                "title": "A conceptual overview of asyncio",
                "description": "# A conceptual overview of `asyncio` [¶](https://docs.python.org/3/howto/"
                "a-conceptual-overview-of-asyncio.html#a-conceptual-overview-of-asyncio)\n- This in-depth "
                "[YouTube tutorial series](https://www.youtube.com/watch?v=Xbl7XjFYsN4) on\n`asyncio` created by "
                "Python core team member, Łukasz Langa." + " The event loop runs tasks." * 40,
                "position": 1,
            },
            {
                "url": "https://realpython.com/async-io-python/",
                "title": "Python's asyncio: A Hands-On Walkthrough",
                "description": "# Python's asyncio: A Hands-On Walkthrough\nRecommended Course\n\nPython\u2019s "
                "`asyncio` library enables you to write concurrent code using the `async` and `await` keywords.",
                "position": 2,
            },
            {
                "url": "https://www.youtube.com/watch?v=oAkLSJNr5zY",
                "title": "Python Tutorial: AsyncIO - Complete Guide to Asynchronous ...",
                "description": "# Python Tutorial: AsyncIO - Complete Guide to Asynchronous Programming",
                "position": 3,
            },
        ]
    },
    "creditsUsed": 2,
    "id": "01a20dd6-1586-7147-8390-8b9599a07fc4",
}

RECORDED_DEEP: dict[str, Any] = {
    "success": True,
    "data": {
        "web": [
            {
                "url": "https://docs.python.org/3/howto/a-conceptual-overview-of-asyncio.html",
                "title": "A conceptual overview of asyncio",
                "description": "# A conceptual overview of `asyncio`",
                "position": 1,
                "markdown": '### Navigation\n\n- [index](https://docs.python.org/3/genindex.html "General Index")\n'
                '- [modules](https://docs.python.org/3/py-modindex.html "Python Module Index")',
                "metadata": {
                    "title": "A conceptual overview of asyncio — Python 3.15.2 documentation",
                    "sourceURL": "https://docs.python.org/3/howto/a-conceptual-overview-of-asyncio.html",
                    "statusCode": 200,
                    "language": "en",
                    "creditsUsed": 1,
                },
            }
        ]
    },
    "creditsUsed": 4,
    "id": "01a20ddc-fcaa-73aa-9e20-a79b61e9b9b6",
}


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> FirecrawlProvider:
    monkeypatch.setenv("FIRECRAWL_API_KEY", "test-key")
    return FirecrawlProvider()


def _answered_with(body: dict[str, Any], sent: list[httpx.Request] | None = None, **kwargs: Any) -> FirecrawlProvider:
    def reply(request: httpx.Request) -> httpx.Response:
        if sent is not None:
            sent.append(request)
        return httpx.Response(200, json=body)

    return FirecrawlProvider(transport=httpx.MockTransport(reply), **kwargs)


def test_a_search_sends_the_query_and_limit_alone(provider: FirecrawlProvider) -> None:
    assert provider._build_payload(SearchRequest(query="q", max_results=10)) == {"query": "q", "limit": 10}
    assert provider._build_payload(SearchRequest(query="q")) == {"query": "q"}


def test_max_results_past_firecrawls_ceiling_is_sent_as_the_ceiling(provider: FirecrawlProvider) -> None:
    assert provider._build_payload(SearchRequest(query="q", max_results=500))["limit"] == 100


def test_deep_asks_firecrawl_to_scrape_each_result_and_lite_adds_nothing(provider: FirecrawlProvider) -> None:
    deep = provider._build_payload(SearchRequest(query="q", search_depth="deep"))
    assert deep == {"query": "q", "scrapeOptions": {"formats": ["markdown"]}}
    assert provider._build_payload(SearchRequest(query="q", search_depth="lite")) == {"query": "q"}


def test_provider_options_reach_the_body_last(provider: FirecrawlProvider) -> None:
    request = SearchRequest(query="q", max_results=5, provider_options={"limit": 7, "tbs": "qdr:w"})
    assert provider._build_payload(request) == {"query": "q", "limit": 7, "tbs": "qdr:w"}


def test_the_search_posts_to_v2_with_a_bearer_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIRECRAWL_API_KEY", "test-key")
    sent: list[httpx.Request] = []
    response = _answered_with(RECORDED, sent).search(SearchRequest(query="q", max_results=3))

    assert sent[0].method == "POST" and sent[0].url.host == "api.firecrawl.dev"
    assert sent[0].url.path == "/v2/search"
    assert sent[0].headers["authorization"] == "Bearer test-key"
    assert len(response.results) == 3 and response.cost_source is None


@pytest.mark.parametrize(
    "option",
    [
        {"include_domains": ["a.com"]},
        {"exclude_domains": ["b.com"]},
        {"recency": "week"},
        {"country": "US"},
        {"locale": "en"},
        {"effort": "low"},
        {"search_depth": "fast"},
    ],
)
def test_an_option_firecrawl_search_does_not_map_is_refused(provider: FirecrawlProvider, option: dict) -> None:
    with pytest.raises(UnsupportedParameterError, match=next(iter(option))):
        provider.validate(SearchRequest(query="q", **option))


def test_results_map_with_the_description_cut_to_a_snippet(provider: FirecrawlProvider) -> None:
    response = provider._to_response(SearchRequest(query="q"), RECORDED)
    first, second, _ = response.results
    long_description = RECORDED["data"]["web"][0]["description"]

    assert response.provider == "firecrawl" and response.raw == RECORDED
    assert response.request_id == "01a20dd6-1586-7147-8390-8b9599a07fc4"
    assert first.url == "https://docs.python.org/3/howto/a-conceptual-overview-of-asyncio.html"
    assert first.title == "A conceptual overview of asyncio"
    assert len(first.description) < len(long_description)
    assert long_description.startswith(first.description.removesuffix("\u2026"))
    assert first.content == long_description
    assert first.extra == {"position": 1} and first.score is None and first.published_date is None
    assert second.description == second.content == RECORDED["data"]["web"][1]["description"]


def test_lite_cuts_the_content_to_a_snippet_too(provider: FirecrawlProvider) -> None:
    first = provider._to_response(SearchRequest(query="q", search_depth="lite"), RECORDED).results[0]
    assert first.content == first.description


def test_deep_content_is_the_scraped_page_and_its_metadata_stays_in_extra(provider: FirecrawlProvider) -> None:
    (result,) = provider._to_response(SearchRequest(query="q", search_depth="deep"), RECORDED_DEEP).results
    hit = RECORDED_DEEP["data"]["web"][0]

    assert result.content == hit["markdown"]
    assert result.description == hit["description"]
    assert result.extra == {"position": 1, "metadata": hit["metadata"]}


@pytest.mark.parametrize("body", [{}, {"success": True, "data": []}, {"success": True, "data": None}])
def test_a_reply_without_a_data_object_is_an_error_not_an_empty_search(
    monkeypatch: pytest.MonkeyPatch, body: dict
) -> None:
    monkeypatch.setenv("FIRECRAWL_API_KEY", "test-key")
    with pytest.raises(ProviderError, match="data object"):
        _answered_with(body).search(SearchRequest(query="q"))


def test_news_asked_for_through_sources_is_kept_in_raw_beside_no_web_results(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIRECRAWL_API_KEY", "test-key")
    body = {"success": True, "data": {"news": [{"title": "T", "url": "https://example.com/n"}]}}
    request = SearchRequest(query="q", provider_options={"sources": ["news"]})

    response = _answered_with(body).search(request)

    assert response.results == [] and response.raw == body


def test_a_refused_search_raises_with_firecrawls_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIRECRAWL_API_KEY", "test-key")
    with pytest.raises(ProviderError, match="Insufficient credits"):
        _answered_with({"success": False, "error": "Insufficient credits"}).search(SearchRequest(query="q"))


def test_an_empty_web_list_is_a_search_that_found_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIRECRAWL_API_KEY", "test-key")
    response: SearchResponse = _answered_with({"success": True, "data": {"web": []}}).search(SearchRequest(query="q"))
    assert response.results == []


def test_a_missing_key_raises_unless_the_keyless_tier_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    with pytest.raises(ConfigurationError, match="FIRECRAWL_API_KEY"):
        FirecrawlProvider()

    sent: list[httpx.Request] = []
    response = _answered_with(RECORDED, sent, allow_keyless=True).search(SearchRequest(query="q"))

    assert "authorization" not in sent[0].headers
    assert (response.cost_usd, response.cost_source) == (0.0, "keyless")


@pytest.mark.integration
def test_live_search_returns_results() -> None:
    if not os.environ.get("FIRECRAWL_API_KEY"):
        pytest.skip("FIRECRAWL_API_KEY not set")
    response = FirecrawlProvider().search(SearchRequest(query="python asyncio tutorial", max_results=3))
    assert 0 < len(response.results) <= 3
    assert all(result.url.startswith("http") for result in response.results)


@pytest.mark.integration
def test_live_deep_search_returns_each_page() -> None:
    if not os.environ.get("FIRECRAWL_API_KEY"):
        pytest.skip("FIRECRAWL_API_KEY not set")
    request = SearchRequest(query="python asyncio tutorial", max_results=2, search_depth="deep")
    response = FirecrawlProvider().search(request)
    assert response.results and all(len(result.content) > len(result.description) for result in response.results)
