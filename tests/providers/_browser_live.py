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
from typing import Any

from web_router import WebRouter
from web_router.providers.base import BrowserProvider
from web_router.types import BrowserState

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

    The login is a cookie and a localStorage entry, the two places a site keeps
    one, set over CDP -- what a person typing a password into the control URL
    leaves behind. Unique per call, so a stale profile cannot pass the check.
    ``wait_saved`` waits for the backend to finish saving, where it can say;
    otherwise the check waits :data:`SAVE_DELAY`.
    """
    from playwright.sync_api import sync_playwright

    name = f"web-router-{uuid.uuid4().hex[:8]}"
    with sync_playwright() as playwright:
        with router.open_browser(provider, profile=profile, persist_profile=True, **options) as browser:
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
