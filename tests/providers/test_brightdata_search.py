"""Bright Data search provider tests.

``DOCUMENTED`` follows the shape of Bright Data's parsed Google results
(``general``, an ``organic`` list of link, title, description and rank,
``top_ads`` beside it), with synthetic values; it is not a recorded reply.
"""

from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import httpx
import pytest

from web_router.exceptions import ConfigurationError, ProviderError, UnsupportedParameterError
from web_router.providers.brightdata import BrightDataProvider
from web_router.types import SearchRequest

DOCUMENTED: dict[str, Any] = {
    "general": {"query": "python asyncio", "detected_query": "python asyncio"},
    "organic": [
        {
            "global_rank": 1,
            "title": "asyncio \u2014 Asynchronous I/O",
            "link": "https://docs.python.org/3/library/asyncio.html",
            "description": "asyncio is a library to write concurrent code using the async/await syntax.",
            "extensions": [
                {"type": "site_link", "text": "Tasks", "link": "https://docs.python.org/3/library/asyncio-task.html"}
            ],
        },
        {
            "global_rank": 2,
            "title": "Python's asyncio: A Hands-On Walkthrough",
            "link": "https://realpython.com/async-io-python/",
            "description": "In this tutorial, you'll learn how Python asyncio works.",
        },
        {"global_rank": 3, "title": "Example result", "link": "https://example.com/"},
    ],
    "top_ads": [{"global_rank": 4, "title": "An ad", "link": "https://ads.example/order"}],
}


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRIGHTDATA_API_TOKEN", "test-token")
    monkeypatch.setenv("BRIGHTDATA_ZONE", "serp_zone")


def _sent(request: SearchRequest, body: dict[str, Any] = DOCUMENTED) -> httpx.Request:
    sent: list[httpx.Request] = []
    provider = BrightDataProvider(
        transport=httpx.MockTransport(lambda r: sent.append(r) or httpx.Response(200, json=body))
    )
    provider.search(request)
    return sent[0]


@pytest.mark.usefixtures("env")
def test_a_search_is_googles_parsed_page_through_the_zone() -> None:
    sent = _sent(SearchRequest(query="python asyncio", max_results=10))

    assert sent.method == "POST" and sent.url.host == "api.brightdata.com" and sent.url.path == "/request"
    assert sent.headers["authorization"] == "Bearer test-token"
    assert json.loads(sent.content) == {
        "zone": "serp_zone",
        "format": "raw",
        "url": "https://www.google.com/search?q=python+asyncio&num=10&brd_json=1",
    }


@pytest.mark.usefixtures("env")
def test_without_max_results_no_num_is_sent() -> None:
    url = urlsplit(json.loads(_sent(SearchRequest(query="q")).content)["url"])
    assert dict(parse_qsl(url.query)) == {"q": "q", "brd_json": "1"}


@pytest.mark.usefixtures("env")
def test_provider_options_reach_the_body_last() -> None:
    own = "https://www.google.com/search?q=q&gl=de&brd_json=1"
    body = json.loads(_sent(SearchRequest(query="q", provider_options={"url": own})).content)
    assert body["url"] == own


def test_search_needs_the_zone_as_extract_does(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRIGHTDATA_API_TOKEN", "test-token")
    monkeypatch.delenv("BRIGHTDATA_ZONE", raising=False)
    with pytest.raises(ConfigurationError, match="BRIGHTDATA_ZONE"):
        BrightDataProvider()
    assert BrightDataProvider(zone="mine")._zone == "mine"


@pytest.mark.usefixtures("env")
@pytest.mark.parametrize(
    "option",
    [
        {"include_domains": ["a.com"]},
        {"exclude_domains": ["b.com"]},
        {"recency": "week"},
        {"country": "US"},
        {"locale": "en"},
        {"effort": "low"},
        {"search_depth": "lite"},
    ],
)
def test_an_option_bright_data_search_does_not_map_is_refused(option: dict) -> None:
    with pytest.raises(UnsupportedParameterError, match=next(iter(option))):
        BrightDataProvider().validate(SearchRequest(query="q", **option))


@pytest.mark.usefixtures("env")
def test_organic_results_map_and_the_rest_of_the_page_stays_in_raw() -> None:
    response = BrightDataProvider()._to_response(SearchRequest(query="python asyncio"), DOCUMENTED)
    first, second, third = response.results

    assert response.provider == "brightdata" and response.raw == DOCUMENTED
    assert (first.url, first.title) == (
        "https://docs.python.org/3/library/asyncio.html",
        "asyncio \u2014 Asynchronous I/O",
    )
    assert first.description == first.content == DOCUMENTED["organic"][0]["description"]
    assert first.extra == {"global_rank": 1, "extensions": DOCUMENTED["organic"][0]["extensions"]}
    assert first.score is None and first.published_date is None
    assert second.url == "https://realpython.com/async-io-python/"
    assert (third.description, third.content) == ("", "")


@pytest.mark.usefixtures("env")
def test_results_past_max_results_are_cut_since_google_ignores_num() -> None:
    response = BrightDataProvider()._to_response(SearchRequest(query="q", max_results=2), DOCUMENTED)
    assert [result.url for result in response.results] == [
        "https://docs.python.org/3/library/asyncio.html",
        "https://realpython.com/async-io-python/",
    ]


@pytest.mark.usefixtures("env")
def test_a_parsed_page_without_organic_results_is_a_search_that_found_nothing() -> None:
    assert BrightDataProvider()._to_response(SearchRequest(query="q"), {"general": {"query": "q"}}).results == []


@pytest.mark.usefixtures("env")
@pytest.mark.parametrize("body", [{}, {"error": "zone not found"}, {"html": "<html></html>"}])
def test_a_body_that_is_not_a_parsed_page_is_an_error(body: dict) -> None:
    with pytest.raises(ProviderError, match="parsed search page"):
        _sent(SearchRequest(query="q"), body)


@pytest.mark.integration
def test_live_search_returns_results() -> None:
    if not (os.environ.get("BRIGHTDATA_API_TOKEN") and os.environ.get("BRIGHTDATA_ZONE")):
        pytest.skip("BRIGHTDATA_API_TOKEN and BRIGHTDATA_ZONE not set")
    response = BrightDataProvider().search(SearchRequest(query="python asyncio tutorial", max_results=3))
    assert 0 < len(response.results) <= 3
    assert all(result.url.startswith("http") for result in response.results)
