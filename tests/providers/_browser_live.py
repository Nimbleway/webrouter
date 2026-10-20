"""What the live browser tests share: waiting for a backend to report a release, and checking a saved login.

A release is a request the backend acts on, not always a state it reports at
once, so a live test asks again for a bounded while rather than once. Only the
backend is asked: a handle answers ``released`` from memory once it has
released, which would prove nothing.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from web_router import WebRouter
from web_router.providers.base import BrowserProvider
from web_router.types import BrowserState

if TYPE_CHECKING:
    from playwright.sync_api import Playwright

RELEASE_DEADLINE = 10.0
RELEASE_INTERVAL = 0.5


def state_after_release(provider: BrowserProvider, session_id: str) -> BrowserState:
    """The state the backend reports once it says ``released``, or the last one it said by the deadline."""
    deadline = time.monotonic() + RELEASE_DEADLINE
    while (state := provider.status(session_id)) != "released" and time.monotonic() < deadline:
        time.sleep(RELEASE_INTERVAL)
    return state


async def astate_after_release(provider: BrowserProvider, session_id: str) -> BrowserState:
    """Async counterpart of :func:`state_after_release`."""
    deadline = time.monotonic() + RELEASE_DEADLINE
    while (state := await provider.astatus(session_id)) != "released" and time.monotonic() < deadline:
        await asyncio.sleep(RELEASE_INTERVAL)
    return state


LOGIN_SITE = "https://example.com"
SIGN_IN_WORDS = ("sign-in", "signin", "login", "log-in")
CONTROL_PAGE_SETTLE_MS = 3000
SAVE_DELAY = 15.0
"""Seconds to wait between releasing a session that saves a profile and opening one on it.

Every backend saves a profile as the session ends, not at the release call: Browserbase and
Hyperbrowser say to wait a few seconds, Steel uploads it before marking it ready.
"""


def login_survives(
    router: WebRouter,
    provider: BrowserProvider,
    profile: str,
    *,
    wait_saved: Callable[[], object] | None = None,
    **options: Any,
) -> bool:
    """Whether a login saved to ``profile`` in one session is there when the next session opens on it.

    The saving session's control URL must open, in a fresh browser holding no
    credentials, on something other than a sign-in page: the page a person
    would log in through. Typing into each vendor's viewer -- a canvas, a
    WebRTC stream, a DevTools screencast -- is not automated; that was checked
    by hand. The login itself is a cookie and a localStorage entry, the two
    places a site keeps one, set over CDP -- what a person logging in through
    the control URL leaves behind. Unique per call, so a stale profile cannot
    pass the check.
    ``wait_saved`` waits for the backend to finish saving, where it can say;
    otherwise the check waits :data:`SAVE_DELAY`.
    """
    from playwright.sync_api import sync_playwright

    name = f"web-router-{uuid.uuid4().hex[:8]}"
    with sync_playwright() as playwright:
        with router.open_browser(provider, profile=profile, persist_profile=True, **options) as browser:
            control_url = browser.control_url()
            assert control_url, "the backend offered no control URL"
            assert _opens_without_signing_in(playwright, control_url), "the control URL asks for a sign-in"
            context = playwright.chromium.connect_over_cdp(browser.cdp_url).contexts[0]
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(LOGIN_SITE)
            context.add_cookies([{"name": name, "value": "1", "url": LOGIN_SITE, "expires": time.time() + 3600}])
            page.evaluate("name => localStorage.setItem(name, '1')", name)
        if wait_saved is None:
            time.sleep(SAVE_DELAY)
        else:
            wait_saved()
        with router.open_browser(provider, profile=profile, **options) as browser:
            context = playwright.chromium.connect_over_cdp(browser.cdp_url).contexts[0]
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(LOGIN_SITE)
            cookie = any(cookie["name"] == name for cookie in context.cookies(LOGIN_SITE))
            stored = page.evaluate("name => localStorage.getItem(name)", name) == "1"
    return cookie and stored


def _opens_without_signing_in(playwright: Playwright, url: str) -> bool:
    """Whether ``url`` loads in a fresh local browser and stays off a sign-in page once its scripts have run."""
    browser = playwright.chromium.launch()
    try:
        page = browser.new_page()
        response = page.goto(url)
        page.wait_for_timeout(CONTROL_PAGE_SETTLE_MS)
        return response is not None and response.ok and not any(word in page.url.lower() for word in SIGN_IN_WORDS)
    finally:
        browser.close()
