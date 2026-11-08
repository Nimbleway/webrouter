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

import os
import time
import uuid

import httpx
import pytest

from tests.providers._browser_live import SAVE_DELAY
from tests.providers._browserbase_fetch import (
    FETCHED,
    KEY,
    URL,
    Fetch,
    provider,
    recorded_fetch,
)
from web_router import WebRouter
from web_router.constants import BROWSERBASE_API_KEY_HEADER, BROWSERBASE_BASE_URL
from web_router.types import ExtractRequest

COOKIE_ECHO = "https://httpbin.org/cookies"
"""A public page that answers with the cookies it was sent, so a fetch shows whether the profile's rode along."""


@pytest.fixture
def fetch(monkeypatch: pytest.MonkeyPatch) -> Fetch:
    return recorded_fetch(monkeypatch)


def test_one_url_and_one_format_are_sent(fetch: Fetch) -> None:
    assert provider(fetch)._build_payload(ExtractRequest(urls=[URL]), [URL]) == {"url": URL, "format": "markdown"}
    assert not provider(fetch).batches_urls


def test_html_is_fetchs_raw_body(fetch: Fetch) -> None:
    payload = provider(fetch)._build_payload(ExtractRequest(urls=[URL], format="html"), [URL])
    assert payload == {"url": URL, "format": "raw"}


def test_a_profile_is_sent_as_the_context_id(fetch: Fetch) -> None:
    payload = provider(fetch)._build_payload(ExtractRequest(urls=[URL], profile="ctx-1"), [URL])
    assert payload == {"url": URL, "format": "markdown", "contextId": "ctx-1"}


def test_the_fetched_page_is_normalized(fetch: Fetch) -> None:
    response = provider(fetch).extract(ExtractRequest(urls=[URL], profile="ctx-1"))

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
    assert fetch.headers[0]["x-bb-api-key"] == KEY, "the key goes in Browserbase's own header"
    assert "authorization" not in fetch.headers[0]


def test_what_the_target_answered_is_reported_not_judged(monkeypatch: pytest.MonkeyPatch) -> None:
    """An expired login comes back as the target's 401: Fetch answered, so it is a document that says so."""
    monkeypatch.setenv("BROWSERBASE_API_KEY", KEY)
    api = Fetch({**FETCHED, "statusCode": 401, "content": "Sign in"})

    response = provider(api).extract(ExtractRequest(urls=[URL], profile="ctx-stale"))

    assert response.failures == []
    assert response.documents[0].status_code == 401
    assert response.documents[0].content == "Sign in"


def test_every_url_is_its_own_fetch(fetch: Fetch) -> None:
    response = provider(fetch).extract(ExtractRequest(urls=[URL, "https://example.org"], profile="ctx-1"))

    assert [call[2]["url"] for call in fetch.calls] == [URL, "https://example.org"]
    assert all(call[2]["contextId"] == "ctx-1" for call in fetch.calls)
    assert response.urls == [URL, "https://example.org"]


def test_the_router_passes_a_profile_through(fetch: Fetch) -> None:
    with WebRouter() as router:
        response = router.extract(URL, provider=provider(fetch), profile="ctx-1")

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
