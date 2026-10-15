"""Jina Reader provider tests.

The mapping here was verified against the live service, which needs no
credential -- which is also why the integration tests below carry no ``skipif``.
"""

from __future__ import annotations

import pytest

from web_router import WebRouter
from web_router.constants import JINA_RETURN_FORMAT_HEADER
from web_router.exceptions import ProviderError
from web_router.providers.jina import JinaExtractProvider
from web_router.types import ExtractFormat, ExtractRequest

URL = "https://example.com"


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> JinaExtractProvider:
    monkeypatch.delenv("JINA_API_KEY", raising=False)
    return JinaExtractProvider()


def test_a_missing_key_is_not_an_error(provider: JinaExtractProvider) -> None:
    """Reader is open; a key only raises the rate limit."""
    assert provider.api_key is None
    assert "Authorization" not in provider._client_kwargs["headers"]


def test_an_empty_key_variable_is_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty JINA_API_KEY used to send a bare "Bearer ", which httpx rejects, so every call failed."""
    monkeypatch.setenv("JINA_API_KEY", "")
    built = JinaExtractProvider()
    try:
        assert built.api_key is None
        assert "Authorization" not in built._client_kwargs["headers"]
    finally:
        built.close()


def test_a_key_is_sent_as_a_bearer_token_when_there_is_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JINA_API_KEY", "test-key")
    built = JinaExtractProvider()
    try:
        assert built._client_kwargs["headers"]["Authorization"] == "Bearer test-key"
    finally:
        built.close()


def test_the_json_envelope_is_always_requested(provider: JinaExtractProvider) -> None:
    """Without it Reader answers with the page itself, which has no title or status."""
    assert provider._client_kwargs["headers"]["Accept"] == "application/json"


def test_only_the_url_goes_in_the_body(provider: JinaExtractProvider) -> None:
    assert provider._build_payload(ExtractRequest(urls=[URL]), [URL]) == {"url": URL}


@pytest.mark.parametrize("body_format", ["markdown", "html", "text"])
def test_the_format_travels_as_a_header_not_a_body_field(
    provider: JinaExtractProvider, body_format: ExtractFormat
) -> None:
    kwargs = provider._call_kwargs(ExtractRequest(urls=[URL], format=body_format), [URL])
    assert kwargs["headers"][JINA_RETURN_FORMAT_HEADER] == body_format
    assert kwargs["json"] == {"url": URL}


@pytest.mark.parametrize(
    ("body_format", "field"),
    [("markdown", "content"), ("html", "html"), ("text", "text")],
)
def test_the_body_is_read_from_the_key_that_matches_the_format(
    provider: JinaExtractProvider, body_format: ExtractFormat, field: str
) -> None:
    """Verified live: asking for html returns `data.html` and omits `data.content` entirely."""
    payload = {"code": 200, "data": {"url": URL, "title": "Example", field: "the body", "httpStatus": 200}}

    document = provider._to_response(ExtractRequest(urls=[URL], format=body_format), [URL], payload).documents[0]

    assert document.content == "the body"
    assert document.format == body_format


def test_jina_payload_is_normalized(provider: JinaExtractProvider) -> None:
    payload = {
        "code": 200,
        "status": 20000,
        "data": {
            "title": "Example Domain",
            "url": "https://example.com/",
            "content": "# Example Domain",
            "publishedTime": "Tue, 15 Sep 2026 23:38:37 GMT",
            "httpStatus": 200,
            "usage": {"tokens": 33},
        },
    }

    response = provider._to_response(ExtractRequest(urls=[URL]), [URL], payload)

    assert response.provider == "jina"
    document = response.documents[0]
    assert document.url == "https://example.com/"
    assert document.title == "Example Domain"
    assert document.content == "# Example Domain"
    assert document.format == "markdown"
    assert document.status_code == 200
    assert document.published_date == "Tue, 15 Sep 2026 23:38:37 GMT"
    assert document.extra["usage"] == {"tokens": 33}
    assert response.raw == [payload]


@pytest.mark.integration
def test_live_extract_returns_page_content() -> None:
    with WebRouter("jina") as router:
        response = router.extract(URL, format="markdown")

    assert response.provider == "jina"
    assert response.documents[0].content
    assert response.documents[0].title


@pytest.mark.integration
def test_live_html_and_markdown_really_differ() -> None:
    """Two formats that came back identical would mean the header was ignored."""
    with WebRouter("jina") as router:
        markdown = router.extract(URL, format="markdown").documents[0].content
        html = router.extract(URL, format="html").documents[0].content

    assert html.startswith("<")
    assert markdown != html


@pytest.mark.integration
def test_live_a_url_that_cannot_be_fetched_raises_with_the_reason() -> None:
    with pytest.raises(ProviderError, match="422"), WebRouter("jina") as router:
        router.extract("https://not-a-real-domain-zzz9.invalid")
