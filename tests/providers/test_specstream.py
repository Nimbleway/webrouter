"""SpecStream provider tests.

``tests/fixtures/specstream/web_search.json`` is a reply recorded from the live
``GET /v1/web-search`` on 2026-11-12, with the key removed and trimmed to four
of its 18 sources (the row's source positions renumbered to match).

The extract replies follow the documented ``data.results`` shape
(specstream.ai/agents.md, "Extract API"); none is recorded from the live API yet.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx
import pytest

from web_router.exceptions import ProviderError, UnsupportedParameterError
from web_router.providers.specstream import SpecStreamExtractProvider, SpecStreamProvider
from web_router.types import ExtractRequest, SearchRequest

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
    with pytest.raises(ProviderError, match=r"data\.sources"):
        provider.search(SearchRequest(query="q"))


def test_an_empty_sources_list_is_a_search_that_found_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPECSTREAM_API_KEY", "test-key")
    body = {"data": {"sources": [], "rows": []}}
    provider = SpecStreamProvider(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body)))
    assert provider.search(SearchRequest(query="q")).results == []


EXTRACT_REPLY: dict[str, Any] = {
    "data": {
        "results": [
            {
                "url": "https://a.com",
                "status": "ok",
                "title": "A",
                "text": "# A\n\nBody.",
                "text_truncated": False,
                "published": "2026-11-01",
                "read_from": "live",
                "emails": [],
            },
        ],
        "request_id": "r1",
        "took_ms": 812,
        "price_usd": 0.0008,
    }
}


@pytest.fixture
def extractor(monkeypatch: pytest.MonkeyPatch) -> SpecStreamExtractProvider:
    monkeypatch.setenv("SPECSTREAM_API_KEY", "test-key")
    return SpecStreamExtractProvider()


@pytest.mark.parametrize(("asked", "sent"), [(None, "markdown"), ("markdown", "markdown"), ("text", "text")])
def test_extract_posts_the_urls_and_the_format(monkeypatch: pytest.MonkeyPatch, asked: Any, sent: str) -> None:
    monkeypatch.setenv("SPECSTREAM_API_KEY", "test-key")
    calls: list[httpx.Request] = []
    extractor = SpecStreamExtractProvider(
        transport=httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(200, json=EXTRACT_REPLY))
    )

    (document,) = extractor.extract(ExtractRequest(urls=["https://a.com"], format=asked)).documents

    assert calls[0].method == "POST" and calls[0].url.path == "/v1/extract"
    assert calls[0].headers["authorization"] == "Bearer test-key"
    assert json.loads(calls[0].content) == {"urls": ["https://a.com"], "format": sent}
    assert document.format == sent


def test_provider_options_reach_the_extract_body(extractor: SpecStreamExtractProvider) -> None:
    request = ExtractRequest(urls=["https://a.com"], provider_options={"include": ["links"]})
    assert extractor._build_payload(request, ["https://a.com"])["include"] == ["links"]


def test_html_is_not_offered(extractor: SpecStreamExtractProvider) -> None:
    assert extractor.supported_formats == {"markdown", "text"} and extractor.default_format == "markdown"


def test_the_extract_reply_maps_from_data_results(extractor: SpecStreamExtractProvider) -> None:
    response = extractor._to_response(ExtractRequest(urls=["https://a.com"]), ["https://a.com"], EXTRACT_REPLY)
    (document,) = response.documents

    assert response.provider == "specstream" and response.raw == [EXTRACT_REPLY]
    assert (document.url, document.title, document.content) == ("https://a.com", "A", "# A\n\nBody.")
    assert document.published_date == "2026-11-01"
    assert document.extra == {"status": "ok", "text_truncated": False, "read_from": "live", "emails": []}


@pytest.mark.parametrize("body", [{}, {"results": []}, {"data": {}}, {"data": {"results": None}}])
def test_an_extract_reply_without_a_results_list_raises(extractor: SpecStreamExtractProvider, body: dict) -> None:
    with pytest.raises(ProviderError, match=r"data\.results"):
        extractor._to_response(ExtractRequest(urls=["https://a.com"]), ["https://a.com"], body)


@pytest.mark.parametrize("status", ["blocked", "not_found", "empty", "unsupported", "invalid_url", "failed"])
def test_a_page_whose_status_is_not_ok_is_a_failed_url(extractor: SpecStreamExtractProvider, status: str) -> None:
    urls = ["https://a.com", "https://b.com"]
    blocked = {"url": "https://b.com", "status": status, "text": "Access denied", "status_code": 403}
    body = {"data": {"results": [EXTRACT_REPLY["data"]["results"][0], blocked]}}

    response = extractor._to_response(ExtractRequest(urls=urls), urls, body)

    assert [document.url for document in response.documents] == ["https://a.com"]
    (failure,) = response.failures
    assert failure.url == "https://b.com" and status in failure.error and failure.status_code == 403


def test_a_blocked_page_reaches_the_caller_as_a_failure_not_a_document(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPECSTREAM_API_KEY", "test-key")

    def answer(request: httpx.Request) -> httpx.Response:
        (url,) = json.loads(request.content)["urls"]
        item = (
            {"url": url, "status": "ok", "text": "Body."}
            if url == "https://a.com"
            else {"url": url, "status": "blocked"}
        )
        return httpx.Response(200, json={"data": {"results": [item]}})

    extractor = SpecStreamExtractProvider(transport=httpx.MockTransport(answer))
    response = extractor.extract(ExtractRequest(urls=["https://a.com", "https://b.com"]))

    assert [document.url for document in response.documents] == ["https://a.com"]
    assert [failure.url for failure in response.failures] == ["https://b.com"]


@pytest.mark.integration
def test_live_search_returns_web_results() -> None:
    if not os.environ.get("SPECSTREAM_API_KEY"):
        pytest.skip("SPECSTREAM_API_KEY not set")
    response = SpecStreamProvider().search(SearchRequest(query="python asyncio tutorial", max_results=3))
    assert 0 < len(response.results) <= 3
    assert all(result.url.startswith("http") for result in response.results)
