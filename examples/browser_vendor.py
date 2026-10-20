"""The same browser session, on a cloud vendor instead of this machine.

`browser_session.py` drives `local`; this runs the same actions through `auto`,
which opens the session on the first vendor you hold a key for -- Steel,
Browserbase, Hyperbrowser, Anchor, then Kernel -- and hands back that vendor's own
handle. Nothing in the driving code changes, because the page actions run over
the session's CDP URL through Playwright rather than through any vendor's API.

It also shows the other way to use a session: hand `cdp_url` to your own
Playwright, browser-use or Stagehand. Connect once and keep that connection for
the whole session -- several vendors end a session when its CDP client
disconnects.

Needs a key. A cloud session bills for as long as it is open, so with none set
this names the variables that would fix it and exits without opening anything;
`auto` would otherwise fall back to `local`, which is not what this example is
about.

Run it with::

    # [all], not [browser]: opening a vendor session takes httpx, which
    # [browser] -- Playwright alone -- does not bring.
    make setup   # in your webrouter clone: every extra
    uv run --env-file .env python examples/browser_vendor.py
"""

from __future__ import annotations

from web_router import WebRouter, configured_browser_providers
from web_router.constants import BROWSER_ENV_BY_PROVIDER

VENDORS = [name for name in configured_browser_providers() if name not in ("auto", "local")]


def through_the_handle(router: WebRouter) -> None:
    """The handle's own actions: identical to the local example, whoever serves it."""
    with router.open_browser() as browser:
        print(f"opened {browser.session_id} on {browser.provider}")
        browser.goto("https://example.com")
        print(browser.snapshot().tree)
        print(f"read {len(browser.read().content)} chars")
    print(f"released: {browser.released}")


def through_your_own_driver(router: WebRouter) -> None:
    """Your own Playwright over the session's CDP URL; the router still owns the release."""
    from playwright.sync_api import sync_playwright

    with router.open_browser() as browser, sync_playwright() as playwright:
        # cdp_url is a credential -- whoever holds it drives the session, and
        # some vendors put the account key in it -- so it is never logged or
        # shown in a repr. Asking for it by name is the one time it appears.
        chrome = playwright.chromium.connect_over_cdp(browser.cdp_url)
        context = chrome.contexts[0] if chrome.contexts else chrome.new_context()
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://example.com")
        print(f"\nyour own Playwright on {browser.provider}: {page.title()!r}")
        chrome.close()


def main() -> None:
    if not VENDORS:
        variables = ", ".join(var for needed in BROWSER_ENV_BY_PROVIDER.values() for var in needed)
        print(f"No cloud browser is configured. Set one of {variables} and run this again.")
        return

    print(f"vendors configured: {', '.join(VENDORS)}")
    with WebRouter() as router:
        through_the_handle(router)
        through_your_own_driver(router)


if __name__ == "__main__":
    main()
