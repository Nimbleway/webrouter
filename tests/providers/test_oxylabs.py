"""Oxylabs provider tests.

The endpoint, HTTP Basic auth and response mapping were verified live on
2026-11-03: content, markdown, html, URL order, and an unfetchable URL reported
as a failure, through the integration test below and the extract checks in
tests/test_integration_surface.py.
"""

from __future__ import annotations

import os
from base64 import b64decode

import pytest

from web_router import WebRouter
from web_router.exceptions import ConfigurationError, UnsupportedFormatError
from web_router.providers.oxylabs import OxylabsExtractProvider
from web_router.types import ExtractRequest

URL = "https://example.com"


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> OxylabsExtractProvider:
    monkeypatch.setenv("OXYLABS_USERNAME", "test-user")
    monkeypatch.setenv("OXYLABS_PASSWORD", "test-pass")
    return OxylabsExtractProvider()


def test_credentials_are_sent_as_http_basic(provider: OxylabsExtractProvider) -> None:
    """Oxylabs takes a username and password, not the bearer token every other backend does."""
    header = provider._client_kwargs["headers"]["Authorization"]
    scheme, credentials = header.split(" ", 1)

    assert scheme == "Basic"
    assert b64decode(credentials).decode() == "test-user:test-pass"


def test_a_missing_username_names_the_variable_to_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OXYLABS_USERNAME", raising=False)
    monkeypatch.setenv("OXYLABS_PASSWORD", "test-pass")

    with pytest.raises(ConfigurationError, match="OXYLABS_USERNAME"):
        OxylabsExtractProvider()


def test_the_universal_source_is_what_asks_for_any_website(provider: OxylabsExtractProvider) -> None:
    assert provider._build_payload(ExtractRequest(urls=[URL], format="html"), [URL]) == {
        "source": "universal",
        "url": URL,
    }


def test_markdown_adds_a_flag_rather_than_replacing_the_html(provider: OxylabsExtractProvider) -> None:
    payload = provider._build_payload(ExtractRequest(urls=[URL], format="markdown"), [URL])
    assert payload == {"source": "universal", "url": URL, "markdown": True}
    assert provider._build_payload(ExtractRequest(urls=[URL]), [URL]) == payload  # markdown is the default


def test_plain_text_is_rejected_until_someone_maps_it(provider: OxylabsExtractProvider) -> None:
    with pytest.raises(UnsupportedFormatError, match="text"):
        provider.validate(ExtractRequest(urls=[URL], format="text"))


def test_the_result_is_chosen_by_its_type_not_its_position(provider: OxylabsExtractProvider) -> None:
    """A multi-format request returns one entry per representation, in no fixed order."""
    payload = {
        "results": [
            {"content": "# Example", "type": "markdown", "status_code": 200, "url": URL},
            {"content": "<html>", "type": "raw", "status_code": 200, "url": URL},
        ]
    }

    markdown = provider._to_response(ExtractRequest(urls=[URL], format="markdown"), [URL], payload).documents
    html = provider._to_response(ExtractRequest(urls=[URL], format="html"), [URL], payload).documents

    assert [document.content for document in markdown] == ["# Example"]
    assert [document.content for document in html] == ["<html>"]


def test_oxylabs_payload_is_normalized(provider: OxylabsExtractProvider) -> None:
    payload = {
        "results": [
            {
                "content": "# Example",
                "type": "markdown",
                "status_code": 200,
                "url": URL,
                "job_id": "12345",
                "created_at": "2026-09-16 00:00:01",
            }
        ]
    }

    response = provider._to_response(ExtractRequest(urls=[URL], format="markdown"), [URL], payload)

    assert response.provider == "oxylabs"
    document = response.documents[0]
    assert document.url == URL
    assert document.content == "# Example"
    assert document.format == "markdown"
    assert document.status_code == 200
    assert document.extra["job_id"] == "12345"
    assert response.raw == [payload]


@pytest.mark.integration
@pytest.mark.skipif(
    not (os.getenv("OXYLABS_USERNAME") and os.getenv("OXYLABS_PASSWORD")),
    reason="OXYLABS_USERNAME / OXYLABS_PASSWORD not set",
)
def test_live_extract_returns_page_content() -> None:
    with WebRouter("oxylabs") as router:
        response = router.extract(URL, format="markdown")

    assert response.provider == "oxylabs"
    assert response.documents[0].content
    assert response.documents[0].format == "markdown"
