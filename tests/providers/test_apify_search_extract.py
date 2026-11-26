"""Apify search (Web Search) and extract (Web Fetch), over the Actors' server interface.

``tests/fixtures/apify/web_search.json`` is a reply recorded from the live server interface
``POST https://apify--web-search.apify.actor/`` on 2026-11-23, trimmed to two of its
five results. The Web Fetch reply below is recorded from the live server interface
``POST https://apify--web-fetch.apify.actor/`` the same day, with response headers dropped.
Live data's tests are in ``test_apify.py``.
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest

from web_router.exceptions import UnsupportedParameterError
from web_router.exceptions_typed import ExtractFailedError
from web_router.providers._clock import pinned_today
from web_router.providers.apify import ApifyExtractProvider, ApifySearchProvider
from web_router.types import ExtractRequest, SearchRequest

RECORDED_SEARCH: dict[str, Any] = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "apify" / "web_search.json").read_text(encoding="utf-8")
)
RECORDED_FETCH: dict[str, Any] = {
    "url": "https://example.com/",
    "fetch": {"loadedUrl": "https://example.com/", "httpStatusCode": 200, "contentType": "text/html; charset=utf-8"},
    "metadata": {"canonicalUrl": "https://example.com/", "title": "Example Domain", "languageCode": "en"},
    "markdown": "This domain is for use in documentation examples without needing permission.",
}


@pytest.fixture(autouse=True)
def _token(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """A fake token for every offline test; the live ones read the real ``APIFY_TOKEN``."""
    if not request.node.get_closest_marker("integration"):
        monkeypatch.setenv("APIFY_TOKEN", "test-token")


def test_search_maps_the_shared_options() -> None:
    request = SearchRequest(
        query="q", max_results=5, include_domains=["a.com"], exclude_domains=["b.com"], country="de", locale="de"
    )
    assert ApifySearchProvider()._build_payload(request) == {
        "query": "q",
        "maxResults": 5,
        "allowedDomains": ["a.com"],
        "blockedDomains": ["b.com"],
        "countryCode": "DE",
        "languageCode": "de",
    }


def test_search_sends_recency_as_its_start_date() -> None:
    with pinned_today(date(2026, 11, 9)):
        payload = ApifySearchProvider()._build_payload(SearchRequest(query="q", recency="week"))
    assert payload["dateFrom"] == "2026-11-02"


def test_search_sends_a_bearer_token_to_the_server_interface() -> None:
    sent: list[httpx.Request] = []
    provider = ApifySearchProvider(
        transport=httpx.MockTransport(lambda r: sent.append(r) or httpx.Response(200, json=RECORDED_SEARCH))
    )

    response = provider.search(SearchRequest(query="capital of australia population 2025"))

    assert sent[0].method == "POST" and sent[0].url.host == "apify--web-search.apify.actor"
    assert sent[0].headers["authorization"] == "Bearer test-token"
    first, second = response.results
    assert first.content == first.description and "Canberra" in first.content
    assert first.extra["position"] == 1 and first.published_date is None
    assert second.published_date == "2020-12-11"


@pytest.mark.parametrize("field", ["include_domains", "exclude_domains"])
def test_search_refuses_more_than_ten_domains_before_the_call(field: str) -> None:
    request = SearchRequest(query="q", **{field: [f"d{i}.com" for i in range(11)]})
    assert set(ApifySearchProvider.refused_values(request)) == {field}
    with pytest.raises(UnsupportedParameterError, match=field):
        ApifySearchProvider(transport=httpx.MockTransport(lambda r: pytest.fail("sent"))).search(request)


def test_search_takes_both_domain_lists_at_once() -> None:
    assert not ApifySearchProvider.refused_values(
        SearchRequest(query="q", include_domains=["a.com"], exclude_domains=["b.com"])
    )


def test_search_refuses_depths_beyond_a_snippet() -> None:
    with pytest.raises(UnsupportedParameterError):
        ApifySearchProvider().search(SearchRequest(query="q", search_depth="fast"))


def test_extract_maps_a_page() -> None:
    sent: list[httpx.Request] = []
    provider = ApifyExtractProvider(
        transport=httpx.MockTransport(lambda r: sent.append(r) or httpx.Response(200, json=RECORDED_FETCH))
    )

    response = provider.extract(ExtractRequest(urls=["https://example.com"]))

    assert json.loads(sent[0].content) == {"url": "https://example.com", "formats": ["markdown"]}
    (document,) = response.documents
    assert document.title == "Example Domain" and document.status_code == 200 and document.format == "markdown"


def test_extract_reports_a_404_page_as_a_failed_url() -> None:
    page = {**RECORDED_FETCH, "fetch": {**RECORDED_FETCH["fetch"], "httpStatusCode": 404}}
    provider = ApifyExtractProvider(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=page)))
    with pytest.raises(ExtractFailedError, match="404"):
        provider.extract(ExtractRequest(urls=["https://example.com/x"]))


def test_extract_reports_a_web_fetch_error_reply_as_a_failed_url() -> None:
    reply = {"url": "https://example.com/x.zip", "fetch": {"httpStatusCode": 200}, "error": "UNSUPPORTED_CONTENT_TYPE"}
    provider = ApifyExtractProvider(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=reply)))
    with pytest.raises(ExtractFailedError, match="UNSUPPORTED_CONTENT_TYPE"):
        provider.extract(ExtractRequest(urls=["https://example.com/x.zip"]))


@pytest.mark.integration
def test_live_search_returns_web_results() -> None:
    if not os.environ.get("APIFY_TOKEN"):
        pytest.skip("APIFY_TOKEN not set")
    response = ApifySearchProvider().search(SearchRequest(query="python asyncio tutorial", max_results=3))
    assert 0 < len(response.results) <= 3
    assert all(result.url.startswith("http") for result in response.results)


@pytest.mark.integration
def test_live_extract_returns_page_content() -> None:
    if not os.environ.get("APIFY_TOKEN"):
        pytest.skip("APIFY_TOKEN not set")
    response = ApifyExtractProvider().extract(ExtractRequest(urls=["https://example.com"]))
    (document,) = response.documents
    assert document.status_code == 200 and "documentation examples" in document.content
