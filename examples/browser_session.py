"""A browser session you drive yourself, on this machine, with no key.

`open_browser()` hands back a handle rather than an answer: a live browser,
billed on a cloud backend for as long as it is open, so it belongs in a `with`
block that releases it however the block ends. `local` is the keyless backend --
a Chrome or Chromium on this machine, started for the session and stopped with
it -- which makes it the one to learn the page actions on. The same calls work
unchanged on every cloud vendor; `browser_vendor.py` shows that half.

Runs the same session twice, once through `WebRouter` and once through
`AsyncWebRouter`, whose handle has the same actions, awaited.

Needs the `browser` extra (Playwright) and a Chrome or Chromium: if none is
installed, the error names `uvx playwright install chromium`.

Run it with::

    make setup   # in your webrouter clone: every extra
    uv run python examples/browser_session.py
"""

from __future__ import annotations

import asyncio
import re
import tempfile
from pathlib import Path

from web_router import AsyncWebRouter, StaleElementError, WebRouter

START = "https://example.com"

# A page of our own for the typing half, so the example does not depend on some
# site's search box staying where it is. Submitting it lands back on example.com.
FORM = (
    "data:text/html,<title>A search form</title>"
    '<form action="https://example.com/"><label>Query <input name=q></label><button>Go</button></form>'
)


def ref_of(tree: str, role: str, name: str) -> str:
    """The ref a snapshot gave one element, found by its role and accessible name."""
    match = re.search(rf'- {role} "{re.escape(name)}".*?\[ref=(\w+)\]', tree)
    if match is None:
        raise LookupError(f"no {role} named {name!r} in the snapshot")
    return match.group(1)


def sync_session() -> None:
    with WebRouter("local") as router, router.open_browser() as browser:
        print(f"opened {browser.session_id} on {browser.provider}")

        state = browser.goto(START)
        print(f"\ngoto: {state.title!r}, HTTP {state.status_code}")

        # The accessibility tree, one element per line, each with a ref to act on.
        page = browser.snapshot()
        print(f"\nsnapshot of {page.url}:\n{page.tree}")

        link = ref_of(page.tree, "link", "Learn more")
        state = browser.click(ref=link)
        print(f"\nclicked {link}: now on {state.url}")

        # That navigation made the old refs meaningless, and the handle says so
        # rather than clicking whatever now sits where the link was.
        try:
            browser.click(ref=link)
        except StaleElementError as exc:
            print(f"\nstale ref refused, as it should be:\n  {exc}")

        browser.goto(FORM)
        box = ref_of(browser.snapshot().tree, "textbox", "Query")
        # submit=True presses Enter after typing and waits for what that loads.
        state = browser.type("web-router", ref=box, submit=True)
        print(f"\ntyped into {box} and submitted: now on {state.url}")

        shot = browser.screenshot(full_page=True)
        path = Path(tempfile.gettempdir()) / "web-router-example.png"
        path.write_bytes(shot.png)
        print(f"\nscreenshot: {shot.width}x{shot.height}, saved to {path}")

        # The page as it is now, in the same document shape extract() returns.
        document = browser.read()
        print(f"\nread: {document.format}, {len(document.content)} chars")
        print(f"  {' '.join(document.content.split())[:120]}")

    # Leaving the block released the session: the browser process is gone.
    print(f"\nreleased: {browser.released}")


async def async_session() -> None:
    async with AsyncWebRouter("local") as router, await router.open_browser() as browser:
        await browser.goto(START)
        page = await browser.snapshot()
        state = await browser.click(ref=ref_of(page.tree, "link", "Learn more"))
        document = await browser.read()
        print(f"\nasync: clicked through to {state.url}, read {len(document.content)} chars")
    print(f"async released: {browser.released}")


def main() -> None:
    sync_session()
    asyncio.run(async_session())


if __name__ == "__main__":
    main()
