"""Firecrawl provider tests.

The request and response mapping were checked live on 2026-10-01 against
Firecrawl's keyless tier, for markdown and html; a keyed call differs only by its
Bearer header. The keyless live test below needs no credential.
"""

from __future__ import annotations

import os

import pytest

from web_router import WebRouter
from web_router.exceptions import ConfigurationError, UnsupportedFormatError
from web_router.providers.firecrawl import FirecrawlExtractProvider
from web_router.types import ExtractFormat, ExtractRequest

URL = "https://example.com"


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> FirecrawlExtractProvider:
    monkeypatch.setenv("FIRECRAWL_API_KEY", "test-key")
    return FirecrawlExtractProvider()


def test_one_url_and_one_format_are_sent(provider: FirecrawlExtractProvider) -> None:
    assert provider._build_payload(ExtractRequest(urls=[URL]), [URL]) == {"url": URL, "formats": ["markdown"]}
    assert not provider.batches_urls


@pytest.mark.parametrize("body_format", ["markdown", "html"])
def test_format_maps_onto_firecrawls_formats_list(
    provider: FirecrawlExtractProvider, body_format: ExtractFormat
) -> None:
    payload = provider._build_payload(ExtractRequest(urls=[URL], format=body_format), [URL])
    assert payload["formats"] == [body_format]


def test_plain_text_is_rejected_until_someone_maps_it(provider: FirecrawlExtractProvider) -> None:
    with pytest.raises(UnsupportedFormatError, match="text"):
        provider.validate(ExtractRequest(urls=[URL], format="text"))


def test_firecrawl_payload_is_normalized(provider: FirecrawlExtractProvider) -> None:
    payload = {
        "success": True,
        "data": {
            "markdown": "# Example",
            "links": ["https://iana.org"],
            "metadata": {
                "title": "Example Domain",
                "sourceURL": "https://example.com/",
                "statusCode": 200,
                "language": "en",
            },
        },
    }

    response = provider._to_response(ExtractRequest(urls=[URL]), [URL], payload)

    assert response.provider == "firecrawl"
    document = response.documents[0]
    assert document.url == "https://example.com/"
    assert document.title == "Example Domain"
    assert document.content == "# Example"
    assert document.format == "markdown"
    assert document.status_code == 200
    assert document.extra["links"] == ["https://iana.org"]
    assert document.extra["metadata"]["language"] == "en"
    assert response.raw == [payload]


def test_an_unsuccessful_scrape_is_a_failure_not_an_empty_document(
    provider: FirecrawlExtractProvider,
) -> None:
    """Firecrawl answers 200 and refuses in `success`, which would look like a blank page."""
    payload = {"success": False, "data": {"metadata": {"error": "blocked by robots.txt"}}}

    response = provider._to_response(ExtractRequest(urls=[URL]), [URL], payload)

    assert not response.documents
    assert response.failures[0].url == URL
    assert "blocked by robots.txt" in response.failures[0].error


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("FIRECRAWL_API_KEY"), reason="FIRECRAWL_API_KEY not set")
def test_live_extract_returns_page_content() -> None:
    with WebRouter("firecrawl") as router:
        response = router.extract(URL, format="markdown")

    assert response.provider == "firecrawl"
    assert response.documents[0].content
    assert response.documents[0].format == "markdown"


def test_a_missing_key_raises_unless_keyless_was_asked_for(monkeypatch: pytest.MonkeyPatch) -> None:
    """Named directly, a missing key says so; only allow_keyless drops to the anonymous tier."""
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    with pytest.raises(ConfigurationError):
        FirecrawlExtractProvider()

    keyless = FirecrawlExtractProvider(allow_keyless=True)

    assert "Authorization" not in keyless._client_kwargs["headers"]
    # Keyless sends no credential, so following a redirect cannot hand one away.
    assert keyless._client_kwargs["follow_redirects"] is False


def test_a_key_wins_over_keyless(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIRECRAWL_API_KEY", "test-key")
    provider = FirecrawlExtractProvider(allow_keyless=True)
    assert provider._client_kwargs["headers"]["Authorization"] == "Bearer test-key"


def test_firecrawl_is_the_last_keyless_extract_member() -> None:
    from web_router.constants import AUTO_EXTRACT_KEYLESS

    assert [name for name, _ in AUTO_EXTRACT_KEYLESS] == ["tavily", "jina", "firecrawl"]


@pytest.mark.integration
@pytest.mark.parametrize("body_format", ["markdown", "html"])
def test_live_keyless_extract_returns_page_content(monkeypatch: pytest.MonkeyPatch, body_format: ExtractFormat) -> None:
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    provider = FirecrawlExtractProvider(allow_keyless=True)
    try:
        response = provider.extract(
            ExtractRequest(urls=["https://en.wikipedia.org/wiki/Web_search_engine"], format=body_format)
        )
    finally:
        provider.close()

    assert response.documents and len(response.documents[0].content) > 1000
    assert response.documents[0].format == body_format
