"""Browserbase extract provider tests, over the Fetch API.

The request and response mapping were checked live on 2026-10-29, for
``markdown`` and ``raw``: the payload below is what Fetch answered, which
carries an ``id`` the docs do not list (kept in ``extra``), and ``contentType``
names the format returned (``text/markdown``) rather than the target's own
type, which stays in ``headers``. ``profile`` was checked live the same day
through the whole flow: a session opened with ``persist_profile=True`` set a
cookie, and a fetch naming the same Context sent it back while one without did
not. The two live tests at the end repeat both halves.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any

import httpx
import pytest

from tests.providers._browser_live import SAVE_DELAY
from web_router import WebRouter
from web_router.constants import BROWSERBASE_API_KEY_HEADER, BROWSERBASE_BASE_URL
from web_router.exceptions import ProviderError, UnsupportedFormatError, UnsupportedParameterError
from web_router.providers.browserbase_extract import BrowserbaseExtractProvider
from web_router.providers.firecrawl import FirecrawlExtractProvider
from web_router.types import ExtractRequest

KEY = "test-key"
URL = "https://example.com/account/orders"
COOKIE_ECHO = "https://httpbin.org/cookies"
"""A public page that answers with the cookies it was sent, so a fetch shows whether the profile's rode along."""

FETCHED: dict[str, Any] = {
    "id": "1601dd35-1820-4ef5-8a64-26c80161edae",
    "statusCode": 200,
    "headers": {"Content-Type": "text/html; charset=utf-8", "Server": "cloudflare"},
    "contentType": "text/markdown",
    "encoding": "utf-8",
    "content": "Your orders\n\nOrder 1234",
}


class _Fetch:
    """A recorded Fetch API: answers every call with the payload above and remembers what it was sent."""

    def __init__(self, payload: dict[str, Any] = FETCHED) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.headers: list[httpx.Headers] = []
        self.payload = payload

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path, json.loads(request.content)))
        self.headers.append(request.headers)
        return httpx.Response(200, json=self.payload)


@pytest.fixture
def fetch(monkeypatch: pytest.MonkeyPatch) -> _Fetch:
    monkeypatch.setenv("BROWSERBASE_API_KEY", KEY)
    return _Fetch()


def _provider(api: _Fetch) -> BrowserbaseExtractProvider:
    return BrowserbaseExtractProvider(transport=httpx.MockTransport(api))


def test_one_url_and_one_format_are_sent(fetch: _Fetch) -> None:
    assert _provider(fetch)._build_payload(ExtractRequest(urls=[URL]), [URL]) == {"url": URL, "format": "markdown"}
    assert not _provider(fetch).batches_urls


def test_html_is_fetchs_raw_body(fetch: _Fetch) -> None:
    payload = _provider(fetch)._build_payload(ExtractRequest(urls=[URL], format="html"), [URL])
    assert payload == {"url": URL, "format": "raw"}


def test_plain_text_is_refused_rather_than_served_as_markdown(fetch: _Fetch) -> None:
    with pytest.raises(UnsupportedFormatError, match="text"):
        _provider(fetch).extract(ExtractRequest(urls=[URL], format="text"))
    assert fetch.calls == []


def test_a_profile_is_sent_as_the_context_id(fetch: _Fetch) -> None:
    payload = _provider(fetch)._build_payload(ExtractRequest(urls=[URL], profile="ctx-1"), [URL])
    assert payload == {"url": URL, "format": "markdown", "contextId": "ctx-1"}


def test_no_profile_sends_no_context_field(fetch: _Fetch) -> None:
    """An unset option is never sent, so Fetch's own anonymous behaviour is what applies."""
    assert "contextId" not in _provider(fetch)._build_payload(ExtractRequest(urls=[URL]), [URL])


def test_the_key_is_sent_in_browserbases_own_header(fetch: _Fetch) -> None:
    _provider(fetch).extract(ExtractRequest(urls=[URL]))

    assert fetch.calls == [("POST", "/v1/fetch", {"url": URL, "format": "markdown"})]
    assert fetch.headers[0]["x-bb-api-key"] == KEY
    assert "authorization" not in fetch.headers[0]


def test_the_fetched_page_is_normalized(fetch: _Fetch) -> None:
    response = _provider(fetch).extract(ExtractRequest(urls=[URL], profile="ctx-1"))

    assert response.provider == "browserbase"
    assert response.failures == []
    document = response.documents[0]
    assert document.url == URL, "Fetch does not echo the URL; the one asked for labels the document"
    assert document.content == FETCHED["content"]
    assert document.format == "markdown"
    assert document.status_code == 200
    assert document.extra == {
        "id": FETCHED["id"],
        "headers": FETCHED["headers"],
        "contentType": "text/markdown",
        "encoding": "utf-8",
    }, "everything Fetch said beyond the mapped fields is kept"
    assert response.raw == [FETCHED]


def test_what_the_target_answered_is_reported_not_judged(monkeypatch: pytest.MonkeyPatch) -> None:
    """An expired login comes back as the target's 401: Fetch answered, so it is a document that says so."""
    monkeypatch.setenv("BROWSERBASE_API_KEY", KEY)
    api = _Fetch({**FETCHED, "statusCode": 401, "content": "Sign in"})

    response = _provider(api).extract(ExtractRequest(urls=[URL], profile="ctx-stale"))

    assert response.failures == []
    assert response.documents[0].status_code == 401
    assert response.documents[0].content == "Sign in"


@pytest.mark.parametrize("content", [{"title": "Orders"}, None], ids=["structured", "missing"])
def test_a_body_with_no_text_is_a_failure_not_a_blank_page(monkeypatch: pytest.MonkeyPatch, content: Any) -> None:
    """Fetch's `json` format, reachable through provider_options, answers a dict; neither it nor nothing is a page."""
    monkeypatch.setenv("BROWSERBASE_API_KEY", KEY)
    payload = {key: value for key, value in FETCHED.items() if key != "content"}
    if content is not None:
        payload["content"] = content

    with pytest.raises(ProviderError, match="no text content"):
        _provider(_Fetch(payload)).extract(ExtractRequest(urls=[URL]))


@pytest.mark.parametrize(
    ("content_type", "encoding"),
    [("application/pdf", "base64"), ("application/json", "utf-8")],
    ids=["binary", "json"],
)
def test_a_raw_body_that_is_not_html_is_not_labelled_html(
    monkeypatch: pytest.MonkeyPatch, content_type: str, encoding: str
) -> None:
    monkeypatch.setenv("BROWSERBASE_API_KEY", KEY)
    api = _Fetch({**FETCHED, "contentType": content_type, "encoding": encoding, "content": "JVBERi0x"})

    with pytest.raises(ProviderError, match=f"Fetch returned {content_type}"):
        _provider(api).extract(ExtractRequest(urls=[URL], format="html"))


def test_an_html_body_with_a_charset_is_served(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BROWSERBASE_API_KEY", KEY)
    api = _Fetch({**FETCHED, "contentType": "text/html; charset=utf-8", "content": "<html></html>"})

    response = _provider(api).extract(ExtractRequest(urls=[URL], format="html"))

    assert response.documents[0].format == "html"
    assert response.documents[0].content == "<html></html>"


def test_every_url_is_its_own_fetch(fetch: _Fetch) -> None:
    response = _provider(fetch).extract(ExtractRequest(urls=[URL, "https://example.org"], profile="ctx-1"))

    assert [call[2]["url"] for call in fetch.calls] == [URL, "https://example.org"]
    assert all(call[2]["contextId"] == "ctx-1" for call in fetch.calls)
    assert response.urls == [URL, "https://example.org"]


def test_a_backend_without_saved_profiles_refuses_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """The option is never dropped: a fetch that quietly ran anonymously would read as the login having failed."""
    monkeypatch.setenv("FIRECRAWL_API_KEY", KEY)
    with pytest.raises(UnsupportedParameterError, match="profile"):
        FirecrawlExtractProvider().extract(ExtractRequest(urls=[URL], profile="ctx-1"))


def test_the_router_passes_a_profile_through(fetch: _Fetch) -> None:
    with WebRouter() as router:
        response = router.extract(URL, provider=_provider(fetch), profile="ctx-1")

    assert fetch.calls[0][2]["contextId"] == "ctx-1"
    assert response.documents[0].content == FETCHED["content"]


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("BROWSERBASE_API_KEY"), reason="BROWSERBASE_API_KEY not set")
def test_live_fetch_returns_markdown_and_html() -> None:
    """The check that confirms the mapping: a public page, in both formats, with the status and type read back."""
    with WebRouter() as router:
        markdown = router.extract("https://example.com", provider="browserbase")
        html = router.extract("https://example.com", provider="browserbase", format="html")

    assert markdown.documents[0].format == "markdown"
    assert "documentation examples" in markdown.documents[0].content
    assert "<html" not in markdown.documents[0].content.lower()
    assert markdown.documents[0].status_code == 200
    assert markdown.documents[0].extra["contentType"] == "text/markdown"
    assert html.documents[0].format == "html"
    assert "<html" in html.documents[0].content.lower()
    assert html.documents[0].extra["contentType"].startswith("text/html")


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("BROWSERBASE_API_KEY"), reason="BROWSERBASE_API_KEY not set")
def test_live_a_cookie_saved_in_a_session_rides_along_on_a_fetch_naming_its_context() -> None:
    """Creates a throwaway context through Browserbase's Contexts API, and deletes it afterwards."""
    from playwright.sync_api import sync_playwright

    name = f"web-router-{uuid.uuid4().hex[:8]}"
    contexts = httpx.Client(
        base_url=BROWSERBASE_BASE_URL, headers={BROWSERBASE_API_KEY_HEADER: os.environ["BROWSERBASE_API_KEY"]}
    )
    context_id = contexts.post("/v1/contexts", json={}).raise_for_status().json()["id"]
    try:
        with WebRouter() as router, sync_playwright() as playwright:
            with router.open_browser("browserbase", profile=context_id, persist_profile=True) as browser:
                cdp = playwright.chromium.connect_over_cdp(browser.cdp_url).contexts[0]
                cdp.add_cookies([{"name": name, "value": "1", "url": COOKIE_ECHO, "expires": time.time() + 3600}])
            time.sleep(SAVE_DELAY)
            # Markdown, not html: the echo answers JSON, which an html request rightly refuses.
            signed_in = router.extract(COOKIE_ECHO, provider="browserbase", profile=context_id)
            anonymous = router.extract(COOKIE_ECHO, provider="browserbase")
    finally:
        contexts.delete(f"/v1/contexts/{context_id}")
        contexts.close()

    assert name in signed_in.documents[0].content
    assert name not in anonymous.documents[0].content
