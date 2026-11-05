"""Exa provider tests.

The response mappings here are derived from Exa's documentation, not from a live
call, for search and for contents alike. The integration tests below are the
checks that matter. See CONTRIBUTING.md on verifying against the live API.
"""

from __future__ import annotations

import os

import pytest

from web_router.constants import SNIPPET_LIMIT
from web_router.exceptions import UnsupportedFormatError, UnsupportedParameterError
from web_router.providers.exa import ExaExtractProvider, ExaProvider
from web_router.types import ExtractRequest, SearchRequest

URL = "https://example.com"


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> ExaProvider:
    monkeypatch.setenv("EXA_API_KEY", "test-key")
    return ExaProvider()


def test_text_contents_are_always_requested(provider: ExaProvider) -> None:
    """Without contents Exa returns links only, and `content` would always be empty."""
    body = provider._build_payload(SearchRequest(query="hi"))
    assert body == {"query": "hi", "contents": {"text": True}}


def test_max_results_maps_onto_num_results(provider: ExaProvider) -> None:
    body = provider._build_payload(SearchRequest(query="hi", max_results=5))
    assert body["numResults"] == 5


def test_depth_is_rejected_until_someone_maps_it(provider: ExaProvider) -> None:
    with pytest.raises(UnsupportedParameterError, match="search_depth"):
        provider.validate(SearchRequest(query="hi", search_depth="deep"))


def test_summary_is_preferred_over_the_text_head_for_the_description(provider: ExaProvider) -> None:
    payload = {
        "requestId": "req-1",
        "results": [
            {
                "url": "https://example.com",
                "title": "Example",
                "text": "the whole page",
                "summary": "a summary",
                "score": 0.42,
                "publishedDate": "2026-01-01",
                "author": "someone",
            }
        ],
    }

    response = provider._to_response(SearchRequest(query="hi"), payload)
    result = response.results[0]

    assert response.request_id == "req-1"
    assert result.description == "a summary"
    assert result.content == "the whole page"
    assert result.score == 0.42
    assert result.published_date == "2026-01-01"
    assert result.extra["author"] == "someone"


def test_the_text_head_becomes_the_description_when_there_is_no_summary(provider: ExaProvider) -> None:
    payload = {"results": [{"url": "https://a.com", "text": "y" * 4000}]}
    result = provider._to_response(SearchRequest(query="hi"), payload).results[0]
    assert len(result.description) <= SNIPPET_LIMIT + 1
    assert len(result.content) == 4000


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("EXA_API_KEY"), reason="EXA_API_KEY not set")
def test_live_search_returns_results() -> None:
    from tests.conftest import billing_aware
    from web_router import WebRouter

    with WebRouter("exa") as router, billing_aware("exa"):
        response = router.search("nimbleway", max_results=3)

    assert response.provider == "exa"
    assert response.results
    assert all(result.url for result in response.results)


# -- Extract -----------------------------------------------------------------


@pytest.fixture
def extractor(monkeypatch: pytest.MonkeyPatch) -> ExaExtractProvider:
    monkeypatch.setenv("EXA_API_KEY", "test-key")
    return ExaExtractProvider()


def test_every_url_goes_in_one_call_under_urls(extractor: ExaExtractProvider) -> None:
    """`ids` is the older spelling of the same field; `urls` is the documented one."""
    urls = [URL, "https://example.org"]
    assert extractor.batches_urls
    assert extractor._build_payload(ExtractRequest(urls=urls), urls) == {"urls": urls, "text": True}


@pytest.mark.parametrize("body_format", ["markdown", "html"])
def test_exa_only_returns_text_so_other_formats_are_rejected(extractor: ExaExtractProvider, body_format: str) -> None:
    """Handing back text labelled as markdown is what this prevents."""
    with pytest.raises(UnsupportedFormatError, match=body_format):
        extractor.validate(ExtractRequest(urls=[URL], format=body_format))  # type: ignore[arg-type]


def test_exa_contents_payload_is_normalized(extractor: ExaExtractProvider) -> None:
    payload = {
        "requestId": "req-1",
        "results": [
            {
                "id": URL,
                "url": URL,
                "title": "Example",
                "text": "the whole page",
                "publishedDate": "2026-01-01",
                "author": "someone",
            }
        ],
        "statuses": [{"id": URL, "status": "success", "source": "crawled"}],
    }

    response = extractor._to_response(ExtractRequest(urls=[URL]), [URL], payload)

    assert response.request_id == "req-1"
    document = response.documents[0]
    assert document.content == "the whole page"
    assert document.format == "text"
    assert document.published_date == "2026-01-01"
    assert document.extra["author"] == "someone"
    assert not response.failures


def test_a_url_exa_could_not_crawl_is_reported_as_a_failure(extractor: ExaExtractProvider) -> None:
    payload = {
        "results": [],
        "statuses": [{"id": "https://broken.example", "status": "error", "error": {"tag": "CRAWL_NOT_FOUND"}}],
    }

    broken = ["https://broken.example"]
    response = extractor._to_response(ExtractRequest(urls=broken), broken, payload)

    assert not response.documents
    assert response.failures[0].url == "https://broken.example"
    assert "CRAWL_NOT_FOUND" in response.failures[0].error


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("EXA_API_KEY"), reason="EXA_API_KEY not set")
def test_live_extract_returns_page_content() -> None:
    from tests.conftest import billing_aware
    from web_router import WebRouter

    with WebRouter("exa") as router, billing_aware("exa"):
        response = router.extract(URL)

    assert response.provider == "exa"
    assert response.documents[0].content
