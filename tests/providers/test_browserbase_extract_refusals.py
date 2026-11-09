"""Browserbase extract: what Fetch answers that is not a page in the format asked for, and options it cannot serve.

Each is refused or reported as a failure rather than served under a format the
body does not match, or fetched without the option the caller set.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.providers._browserbase_fetch import (
    FETCHED,
    KEY,
    URL,
    Fetch,
    provider,
    recorded_fetch,
)
from web_router.exceptions import ProviderError, UnsupportedFormatError, UnsupportedParameterError
from web_router.providers.firecrawl import FirecrawlExtractProvider
from web_router.types import ExtractRequest


@pytest.fixture
def fetch(monkeypatch: pytest.MonkeyPatch) -> Fetch:
    return recorded_fetch(monkeypatch)


def test_plain_text_is_refused_rather_than_served_as_markdown(fetch: Fetch) -> None:
    with pytest.raises(UnsupportedFormatError, match="text"):
        provider(fetch).extract(ExtractRequest(urls=[URL], format="text"))
    assert fetch.calls == []


@pytest.mark.parametrize("content", [{"title": "Orders"}, None], ids=["structured", "missing"])
def test_a_body_with_no_text_is_a_failure_not_a_blank_page(monkeypatch: pytest.MonkeyPatch, content: Any) -> None:
    """Fetch's `json` format, reachable through provider_options, answers a dict; neither it nor nothing is a page."""
    monkeypatch.setenv("BROWSERBASE_API_KEY", KEY)
    payload = {key: value for key, value in FETCHED.items() if key != "content"}
    if content is not None:
        payload["content"] = content

    with pytest.raises(ProviderError, match="no text content"):
        provider(Fetch(payload)).extract(ExtractRequest(urls=[URL]))


@pytest.mark.parametrize(
    ("content_type", "encoding"),
    [("application/pdf", "base64"), ("application/json", "utf-8")],
    ids=["binary", "json"],
)
def test_a_raw_body_that_is_not_html_is_not_labelled_html(
    monkeypatch: pytest.MonkeyPatch, content_type: str, encoding: str
) -> None:
    monkeypatch.setenv("BROWSERBASE_API_KEY", KEY)
    api = Fetch({**FETCHED, "contentType": content_type, "encoding": encoding, "content": "JVBERi0x"})

    with pytest.raises(ProviderError, match=f"Fetch returned {content_type}"):
        provider(api).extract(ExtractRequest(urls=[URL], format="html"))


def test_an_html_body_with_a_charset_is_served(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BROWSERBASE_API_KEY", KEY)
    api = Fetch({**FETCHED, "contentType": "text/html; charset=utf-8", "content": "<html></html>"})

    response = provider(api).extract(ExtractRequest(urls=[URL], format="html"))

    assert response.documents[0].format == "html"
    assert response.documents[0].content == "<html></html>"


def test_a_backend_without_saved_profiles_refuses_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """The option is never dropped: a fetch that quietly ran anonymously would read as the login having failed."""
    monkeypatch.setenv("FIRECRAWL_API_KEY", KEY)
    with pytest.raises(UnsupportedParameterError, match="profile"):
        FirecrawlExtractProvider().extract(ExtractRequest(urls=[URL], profile="ctx-1"))
