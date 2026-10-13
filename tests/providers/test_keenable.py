"""Keenable provider tests.

Checked against the live API on 2026-10-13, keyed and keyless: ``X-API-Key``
authenticates ``/v1/search`` and ``/v1/fetch``; their ``/public`` twins answer the
same shapes with no key and refuse a call without ``X-Keenable-Title`` (400
``Missing app identifier``); ``max_results`` bounds the results; a search
result's ``description`` is often empty beside a long ``snippet``; Fetch cuts a
page at 50,000 characters with a truncation notice unless ``max_chars`` is sent
(a 102,092-character article came back whole at 500,000); a URL Keenable had
never seen came back without ``live``, despite its docs; a missing page answers
404; and Fetch's ``published_at`` is a Unix timestamp where its docs say ISO
8601. The payloads are trimmed recordings of those calls.
"""

from __future__ import annotations

import os

import httpx
import pytest

from web_router.constants import SNIPPET_LIMIT
from web_router.exceptions import ConfigurationError, UnsupportedFormatError, UnsupportedParameterError
from web_router.providers.keenable import KeenableExtractProvider, KeenableProvider
from web_router.types import ExtractRequest, SearchRequest

RECORDED = {
    "query": "who created the python programming language",
    "mode": "pro",
    "results": [
        {
            "title": "History of Python",
            "url": "https://en.wikipedia.org/wiki?title=History_of_Python",
            "description": "",
            "snippet": "The programming language Python was conceived in the late 1980s by Guido van Rossum. " * 20,
            "published_at": "2026-09-05T12:55:23Z",
            "acquired_at": "2026-09-07T10:33:41Z",
        },
        {
            "title": "Guido van Rossum | Biography",
            "url": "https://www.ebsco.com/research-starters/biography/guido-van-rossum",
            "description": "A Dutch programmer best known for creating Python.",
            "snippet": "Guido van Rossum is a Dutch computer programmer best known for creating Python.",
            "acquired_at": "2026-08-16T16:44:10Z",
        },
    ],
}


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> KeenableProvider:
    monkeypatch.setenv("KEENABLE_API_KEY", "test-key")
    return KeenableProvider()


def test_max_results_maps_onto_max_results_and_nothing_else_is_sent(provider: KeenableProvider) -> None:
    assert provider._build_payload(SearchRequest(query="hi", max_results=5)) == {"query": "hi", "max_results": 5}


def test_the_key_goes_in_x_api_key_on_the_keyed_path(provider: KeenableProvider) -> None:
    assert provider._auth_headers("test-key") == {"X-API-Key": "test-key"}
    assert provider.search_path == "/v1/search"
    assert "X-Keenable-Title" not in provider._client_kwargs["headers"]


def test_an_unmapped_option_is_refused_rather_than_dropped(provider: KeenableProvider) -> None:
    with pytest.raises(UnsupportedParameterError, match="search_depth"):
        provider.validate(SearchRequest(query="hi", search_depth="deep"))


def test_the_snippet_is_the_content_and_nothing_is_invented(provider: KeenableProvider) -> None:
    response = provider._to_response(SearchRequest(query="who created python"), RECORDED)
    first, second = response.results

    assert response.raw == RECORDED and response.request_id is None
    assert first.content == RECORDED["results"][0]["snippet"]
    # An empty description falls back to the head of the snippet, so it means what it means elsewhere.
    assert len(first.description) <= SNIPPET_LIMIT + 1 and first.description.startswith("The programming language")
    assert first.published_date == "2026-09-05T12:55:23Z" and first.score is None
    # acquired_at is when Keenable indexed the page; it is kept, not relabelled.
    assert first.extra == {"acquired_at": "2026-09-07T10:33:41Z"}
    assert second.description == "A Dutch programmer best known for creating Python."
    assert second.published_date is None


def test_a_missing_key_raises_unless_keyless_was_asked_for(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KEENABLE_API_KEY", raising=False)
    with pytest.raises(ConfigurationError, match="KEENABLE_API_KEY"):
        KeenableProvider()


def test_keyless_calls_the_public_path_and_names_the_app(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KEENABLE_API_KEY", raising=False)
    sent: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json=RECORDED)

    provider = KeenableProvider(allow_keyless=True, transport=httpx.MockTransport(handle))
    response = provider.search(SearchRequest(query="hi", max_results=2))

    assert response.provider == "keenable" and len(response.results) == 2
    assert sent[0].url.path == "/v1/search/public"
    assert sent[0].headers["X-Keenable-Title"] == "web-router"
    assert "X-API-Key" not in sent[0].headers


def test_keyless_with_a_key_set_still_uses_the_key(provider: KeenableProvider) -> None:
    keyed = KeenableProvider(allow_keyless=True)

    assert keyed.search_path == "/v1/search" and keyed.api_key == "test-key"


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("KEENABLE_API_KEY"), reason="KEENABLE_API_KEY not set")
def test_live_search_returns_results() -> None:
    from tests.conftest import billing_aware
    from web_router import WebRouter

    with WebRouter("keenable") as router, billing_aware("keenable"):
        response = router.search("Who created the Python programming language?", max_results=3)

    assert response.provider == "keenable"
    assert 0 < len(response.results) <= 3
    assert all(result.url and result.content for result in response.results)


@pytest.mark.integration
def test_live_keyless_search_returns_results(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KEENABLE_API_KEY", raising=False)
    provider = KeenableProvider(allow_keyless=True)
    try:
        response = provider.search(SearchRequest(query="Who created the Python programming language?", max_results=3))
    finally:
        provider.close()

    assert 0 < len(response.results) <= 3


PAGE = "https://en.wikipedia.org/wiki/Python_(programming_language)"

RECORDED_FETCH = {
    "url": PAGE,
    "title": "Python (programming language)",
    "content": "Python is a high-level, general-purpose programming language.",
    "description": "",
    "author": "Contributors to Wikimedia projects",
    "published_at": 1791654966,
}


@pytest.fixture
def extractor(monkeypatch: pytest.MonkeyPatch) -> KeenableExtractProvider:
    monkeypatch.setenv("KEENABLE_API_KEY", "test-key")
    return KeenableExtractProvider()


def test_one_url_and_the_whole_page_are_asked_for(extractor: KeenableExtractProvider) -> None:
    assert extractor._build_payload(ExtractRequest(urls=[PAGE]), [PAGE]) == {"url": PAGE, "max_chars": 500_000}
    assert extractor.http_method == "GET" and extractor.extract_path == "/v1/fetch"


@pytest.mark.parametrize("body_format", ["html", "text"])
def test_a_format_other_than_markdown_is_refused(extractor: KeenableExtractProvider, body_format: str) -> None:
    with pytest.raises(UnsupportedFormatError):
        extractor.validate(ExtractRequest(urls=[PAGE], format=body_format))


def test_the_page_maps_and_its_timestamp_is_kept_as_sent(extractor: KeenableExtractProvider) -> None:
    response = extractor._to_response(ExtractRequest(urls=[PAGE]), [PAGE], RECORDED_FETCH)
    (document,) = response.documents

    assert document.url == PAGE and document.title == "Python (programming language)"
    assert document.content == RECORDED_FETCH["content"] and document.format == "markdown"
    assert document.published_date is None
    assert document.extra == {
        "description": "",
        "author": "Contributors to Wikimedia projects",
        "published_at": 1791654966,
    }


def test_a_missing_page_is_a_failure_not_an_empty_document(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KEENABLE_API_KEY", raising=False)
    sent: list[httpx.Request] = []
    missing = "https://example.com/missing"

    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        if request.url.params["url"] == missing:
            return httpx.Response(404, json={"error": "Not found", "message": "The page does not exist at this URL."})
        return httpx.Response(200, json=RECORDED_FETCH)

    extractor = KeenableExtractProvider(allow_keyless=True, transport=httpx.MockTransport(handle))
    response = extractor.extract(ExtractRequest(urls=[PAGE, missing]))

    assert [d.url for d in response.documents] == [PAGE]
    assert [f.url for f in response.failures] == [missing] and "404" in response.failures[0].error
    assert all(r.url.path == "/v1/fetch/public" for r in sent)
    assert all(r.headers["X-Keenable-Title"] == "web-router" for r in sent)


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("KEENABLE_API_KEY"), reason="KEENABLE_API_KEY not set")
def test_live_extract_returns_the_whole_page() -> None:
    from tests.conftest import billing_aware
    from web_router import WebRouter

    with WebRouter("keenable") as router, billing_aware("keenable"):
        response = router.extract("https://en.wikipedia.org/wiki/United_States")

    (document,) = response.documents
    assert document.format == "markdown"
    # Past Fetch's own 50,000-character default, so max_chars reached it.
    assert len(document.content) > 60_000
