"""Bright Data provider tests.

The mapping here is derived from Bright Data's documentation, not from a live
call -- no account was available during development. The integration test below
is the check that matters; run it once credentials are available. See
CONTRIBUTING.md on verifying against the live API.
"""

from __future__ import annotations

import os

import pytest

from web_router import WebRouter
from web_router.exceptions import ConfigurationError, UnsupportedFormatError
from web_router.providers.brightdata import BrightDataExtractProvider
from web_router.types import ExtractRequest

URL = "https://example.com"


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> BrightDataExtractProvider:
    monkeypatch.setenv("BRIGHTDATA_API_TOKEN", "test-key")
    monkeypatch.setenv("BRIGHTDATA_ZONE", "test-zone")
    return BrightDataExtractProvider()


def test_a_missing_zone_names_the_variable_to_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """The zone has no default; a request without one is rejected by the API."""
    monkeypatch.setenv("BRIGHTDATA_API_TOKEN", "test-key")
    monkeypatch.delenv("BRIGHTDATA_ZONE", raising=False)

    with pytest.raises(ConfigurationError, match="BRIGHTDATA_ZONE"):
        BrightDataExtractProvider()


def test_the_json_envelope_is_always_requested(provider: BrightDataExtractProvider) -> None:
    """`format="raw"` returns the page as the HTTP body, which is not JSON to decode."""
    payload = provider._build_payload(ExtractRequest(urls=[URL], format="html"), [URL])
    assert payload == {"url": URL, "zone": "test-zone", "format": "json"}


def test_markdown_is_asked_for_through_data_format(provider: BrightDataExtractProvider) -> None:
    """`format` picks the envelope; `data_format` post-processes the page inside it. Markdown is also the default."""
    payload = provider._build_payload(ExtractRequest(urls=[URL], format="markdown"), [URL])
    assert payload == {"url": URL, "zone": "test-zone", "format": "json", "data_format": "markdown"}
    assert provider._build_payload(ExtractRequest(urls=[URL]), [URL]) == payload


def test_plain_text_is_rejected_until_someone_maps_it(provider: BrightDataExtractProvider) -> None:
    with pytest.raises(UnsupportedFormatError, match="text"):
        provider.validate(ExtractRequest(urls=[URL], format="text"))


def test_brightdata_envelope_is_normalized(provider: BrightDataExtractProvider) -> None:
    payload = {
        "status_code": 200,
        "headers": {"content-type": "text/html"},
        "body": "<html>Example</html>",
    }

    response = provider._to_response(ExtractRequest(urls=[URL], format="html"), [URL], payload)

    assert response.provider == "brightdata"
    document = response.documents[0]
    assert document.url == URL, "the envelope describes the fetch and carries no URL of its own"
    assert document.content == "<html>Example</html>"
    assert document.format == "html"
    assert document.status_code == 200
    assert document.extra["headers"] == {"content-type": "text/html"}
    assert response.raw == [payload]


def test_a_fetch_the_unlocker_could_not_make_is_a_failure(provider: BrightDataExtractProvider) -> None:
    """The envelope recorded live for an unresolvable host: 200 outside, 502 and an empty body inside."""
    payload = {
        "status_code": 502,
        "headers": {
            "x-brd-error": "Could not resolve host not-a-real-domain-zzz9.invalid.",
            "x-brd-error-code": "proxy_error",
        },
        "body": "",
    }

    response = provider._to_response(ExtractRequest(urls=[URL]), [URL], payload)

    assert response.documents == []
    assert response.failures[0].url == URL
    assert "Could not resolve host" in response.failures[0].error
    assert response.raw == [payload]


def test_the_error_header_is_read_whatever_its_case(provider: BrightDataExtractProvider) -> None:
    payload = {"status_code": 502, "headers": {"X-Brd-Error": "Could not resolve host"}, "body": ""}

    response = provider._to_response(ExtractRequest(urls=[URL]), [URL], payload)

    assert response.documents == []
    assert response.failures[0].error == "Could not resolve host"


def test_an_error_page_the_target_served_is_still_a_document(provider: BrightDataExtractProvider) -> None:
    """A 404 the site answered was fetched; its status says what it is."""
    payload = {"status_code": 404, "headers": {"content-type": "text/html"}, "body": "Not Found"}

    response = provider._to_response(ExtractRequest(urls=[URL]), [URL], payload)

    assert response.failures == []
    assert response.documents[0].status_code == 404
    assert response.documents[0].content == "Not Found"


@pytest.mark.integration
@pytest.mark.skipif(
    not (os.getenv("BRIGHTDATA_API_TOKEN") and os.getenv("BRIGHTDATA_ZONE")),
    reason="BRIGHTDATA_API_TOKEN / BRIGHTDATA_ZONE not set",
)
def test_live_extract_returns_page_content() -> None:
    with WebRouter("brightdata") as router:
        response = router.extract(URL, format="markdown")

    assert response.provider == "brightdata"
    assert response.documents[0].content
    assert response.documents[0].format == "markdown"
