"""WebIQ provider tests.

Search was checked against the live API on 2026-10-01: ``x-apikey``
authenticates, ``maxResults`` bounds the results, and ``contentFormat: "passage"``
returns plain-text passages where the default is the page's HTML markup.

Browse (extract) was checked live on 2026-10-07: ``POST /v3/browse`` with
``contentFormat`` ``markdown``, ``html`` or ``text`` returns a different body for
each, and its ``html`` -- also what it returns unset -- is a simplified markup
(``<TITLE>``, ``<H1>``, ``<P>``, no ``<html>``), not the page source; content is cut at 10,000 characters unless
``maxLength`` is sent (a 142,000-character article came back whole at
500,000); a page outside WebIQ's index answers 404 ``BrowseApiDocNotFound``; and
``liveCrawl: "force"`` answers 202 with a ``retryAfter`` and no content. The
payloads are trimmed recordings of those calls.
"""

from __future__ import annotations

import json
import os

import httpx
import pytest

from web_router.constants import SNIPPET_LIMIT
from web_router.exceptions import ProviderError, UnsupportedFormatError, UnsupportedParameterError
from web_router.providers.webiq import WebIQExtractProvider, WebIQProvider
from web_router.types import ExtractRequest, SearchRequest

RECORDED = {
    "webResults": [
        {
            "title": "Pride & Prejudice (2005 film) - Wikipedia",
            "url": "https://en.wikipedia.org/wiki/Pride_%26_Prejudice_(2005_film)",
            "content": "Pride & Prejudice is a 2005 romantic drama film directed by Joe Wright. " * 60,
            "lastUpdatedAt": "2026-09-09T16:39:00.0000000",
            "crawledAt": "2026-09-30T03:16:00.0000000Z",
            "language": "en",
            "isAdult": False,
            "clickUrl": "https://api.microsoft.ai/ck/a?!&&p=abc",
            "instrumentationSuffix": "DevEx,1000",
        },
        {"title": "Pride & Prejudice (2005) - IMDb", "url": "https://www.imdb.com/title/tt0414387/", "content": ""},
    ],
    "riskLevel": "low",
    "querySignals": {"freshness": False},
    "traceId": "trace-1",
}


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> WebIQProvider:
    monkeypatch.setenv("WEBIQ_API_KEY", "test-key")
    return WebIQProvider()


def test_passages_are_always_asked_for_and_max_results_maps_onto_max_results(provider: WebIQProvider) -> None:
    """Without contentFormat the content is raw HTML, which no other backend hands a model."""
    assert provider._build_payload(SearchRequest(query="hi", max_results=5)) == {
        "query": "hi",
        "maxResults": 5,
        "contentFormat": "passage",
    }


def test_the_key_goes_in_x_apikey_as_it_is(provider: WebIQProvider) -> None:
    assert provider._auth_headers("test-key") == {"x-apikey": "test-key"}


def test_an_unmapped_option_is_refused_rather_than_dropped(provider: WebIQProvider) -> None:
    with pytest.raises(UnsupportedParameterError, match="recency"):
        provider.validate(SearchRequest(query="hi", recency="week"))


def test_passages_become_the_content_and_nothing_is_invented(provider: WebIQProvider) -> None:
    response = provider._to_response(SearchRequest(query="who directed it"), RECORDED)
    first, second = response.results

    assert response.request_id == "trace-1" and response.raw == RECORDED
    assert first.content == RECORDED["webResults"][0]["content"]
    assert len(first.description) <= SNIPPET_LIMIT + 1 and first.description.startswith("Pride & Prejudice")
    # WebIQ states no score and no publication date; lastUpdatedAt is kept, not relabelled.
    assert first.score is None and first.published_date is None
    assert first.extra["lastUpdatedAt"] == "2026-09-09T16:39:00.0000000"
    assert second.content == "" and second.description == ""


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("WEBIQ_API_KEY"), reason="WEBIQ_API_KEY not set")
def test_live_search_returns_passages() -> None:
    from tests.conftest import billing_aware
    from web_router import WebRouter

    with WebRouter("webiq") as router, billing_aware("webiq"):
        response = router.search("Who directed the 2005 film Pride & Prejudice?", max_results=3)

    assert response.provider == "webiq"
    assert 0 < len(response.results) <= 3
    assert all(result.url for result in response.results)
    assert not any(result.content.lstrip().startswith("<") for result in response.results)


PAGE = "https://en.wikipedia.org/wiki/Canberra"

RECORDED_BROWSE = {
    "url": PAGE,
    "title": "Canberra - Wikipedia",
    "content": "# Canberra - Wikipedia\n\n# Canberra\n\nCanberra is the capital city of Australia.",
    "isAdult": False,
    "lastUpdatedAt": "2026-10-05T11:02:00.0000000",
    "crawledAt": "2026-10-06T08:14:00.0000000Z",
    "traceId": "trace-2",
}

RECORDED_QUEUED = {"url": "https://example.com/?q=new", "retryAfter": "2026-10-07T06:32:43Z", "traceId": "trace-3"}

RECORDED_NOT_INDEXED = {
    "errorCode": "BrowseApiDocNotFound",
    "errorCategory": "UserError",
    "userMessage": "No result is found",
    "technicalDetails": "NotFound",
    "traceId": "trace-4",
}


@pytest.fixture
def extractor(monkeypatch: pytest.MonkeyPatch) -> WebIQExtractProvider:
    monkeypatch.setenv("WEBIQ_API_KEY", "test-key")
    return WebIQExtractProvider()


@pytest.mark.parametrize("body_format", ["markdown", "text"])
def test_one_url_its_format_and_the_whole_page_are_asked_for(extractor: WebIQExtractProvider, body_format: str) -> None:
    request = ExtractRequest(urls=[PAGE], format=body_format)

    assert extractor._build_payload(request, [PAGE]) == {
        "url": PAGE,
        "contentFormat": body_format,
        "maxLength": 500_000,
    }


def test_no_format_asks_for_what_the_document_will_be_labelled(extractor: WebIQExtractProvider) -> None:
    request = ExtractRequest(urls=[PAGE])
    sent = extractor._build_payload(request, [PAGE])["contentFormat"]

    assert extractor._to_response(request, [PAGE], RECORDED_BROWSE).documents[0].format == sent


def test_html_is_refused_rather_than_relabelled(extractor: WebIQExtractProvider) -> None:
    """Browse's html is its own simplified markup, not the page source other backends return."""
    with pytest.raises(UnsupportedFormatError):
        extractor.validate(ExtractRequest(urls=[PAGE], format="html"))


def test_a_browse_payload_is_normalized_and_nothing_is_invented(extractor: WebIQExtractProvider) -> None:
    response = extractor._to_response(ExtractRequest(urls=[PAGE], format="markdown"), [PAGE], RECORDED_BROWSE)
    (document,) = response.documents

    assert (document.url, document.title, document.format) == (PAGE, "Canberra - Wikipedia", "markdown")
    assert document.content == RECORDED_BROWSE["content"]
    assert document.status_code is None, "Browse states no status code"
    assert document.extra["crawledAt"] == "2026-10-06T08:14:00.0000000Z"
    assert response.request_id == "trace-2" and "traceId" not in document.extra
    assert response.raw == [RECORDED_BROWSE]


def test_a_queued_page_is_a_failure_not_an_empty_document(extractor: WebIQExtractProvider) -> None:
    url = RECORDED_QUEUED["url"]
    response = extractor._to_response(ExtractRequest(urls=[url]), [url], RECORDED_QUEUED)

    assert response.documents == [] and response.request_id == "trace-3"
    assert response.failures[0].url == url
    assert "2026-10-07T06:32:43Z" in response.failures[0].error


def test_a_page_outside_the_index_fails_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    missing = "https://example.com/not-indexed"

    def api(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-apikey"] == "test-key"
        if json.loads(request.content)["url"] == missing:
            return httpx.Response(404, json=RECORDED_NOT_INDEXED)
        return httpx.Response(200, json=RECORDED_BROWSE)

    monkeypatch.setenv("WEBIQ_API_KEY", "test-key")
    provider = WebIQExtractProvider(transport=httpx.MockTransport(api))
    response = provider.extract(ExtractRequest(urls=[PAGE, missing], format="markdown"))

    assert [d.url for d in response.documents] == [PAGE]
    assert [f.url for f in response.failures] == [missing]


def test_nothing_in_the_index_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEBIQ_API_KEY", "test-key")
    provider = WebIQExtractProvider(
        transport=httpx.MockTransport(lambda r: httpx.Response(404, json=RECORDED_NOT_INDEXED))
    )

    with pytest.raises(ProviderError):
        provider.extract(ExtractRequest(urls=["https://example.com/not-indexed"]))


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("WEBIQ_API_KEY"), reason="WEBIQ_API_KEY not set")
def test_live_extract_returns_a_whole_page() -> None:
    from tests.conftest import billing_aware
    from web_router import WebRouter

    with WebRouter() as router, billing_aware("webiq"):
        response = router.extract(PAGE, provider="webiq", format="markdown")

    (document,) = response.documents
    assert response.provider == "webiq" and document.format == "markdown"
    assert len(document.content) > 10_000, "past Browse's 10,000-character default cut"
    assert not document.content.lstrip().startswith("<")
