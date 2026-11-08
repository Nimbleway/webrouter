"""Page actions on a browser handle: goto, snapshot, click, type, press, screenshot, read.

The actions are written once, over CDP through Playwright, for every backend.
The unit tests replace Playwright with a fake that records what it was asked,
so they run without a browser; they pin one connection per session, the stale-ref
rule, error translation, the CDP URL staying out of every message, and the
connection closing before the session is released.

The ``browser``-marked tests at the bottom are the acceptance run: every action
against the ``local`` provider and the pages in tests/fixtures/browser, loaded
over ``file://``. They skip when no Chromium-family browser or no Playwright is
installed. Run them with ``make test-browser``.

One check here guards an undocumented dependency: refs resolve through
Playwright's ``aria-ref=`` selector engine, which the driver registers but does
not document. ``test_the_playwright_driver_still_registers_the_aria_ref_engine``
fails offline if a Playwright release drops it, and the live tests fail if it
stops resolving.
"""

from __future__ import annotations

import asyncio
import base64
import gc
import importlib.util
import re
import struct
import sys
import threading
import warnings
import zlib
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests.conftest import FakeBrowserProvider, jpeg
from web_router import AsyncWebRouter, Screenshot, WebRouter
from web_router.constants import (
    BROWSER_ACTION_TIMEOUT,
    BROWSER_CONNECT_TIMEOUT,
    EXTRA_BROWSER,
    MS_PER_SECOND,
    PNG_SIGNATURE,
)
from web_router.exceptions import (
    BrowserActionError,
    BrowserActionTimeoutError,
    ConfigurationError,
    ElementNotFoundError,
    NavigationError,
    PageClosedError,
    StaleElementError,
    WebRouterError,
)
from web_router.providers import _page
from web_router.providers._page import compact_snapshot
from web_router.providers._png import png_size
from web_router.types import BrowserRequest

HAS_PLAYWRIGHT = importlib.util.find_spec("playwright") is not None
needs_playwright = pytest.mark.skipif(not HAS_PLAYWRIGHT, reason="the browser extra (playwright) is not installed")

FIXTURES = Path(__file__).parent / "fixtures" / "browser"

SNAPSHOT = """\
- generic [active] [ref=e1]:
  - heading "Page actions" [level=1] [ref=e2]
  - textbox "Search" [ref=e5]
  - generic [ref=e6]:
    - generic [ref=e7]:
      - button "Buy Apple" [ref=e8]
  - link "Next page" [ref=e12] [cursor=pointer]:
    - /url: next.html
  - generic [ref=e13] [cursor=pointer]: Clickable div
  - iframe [ref=e20]:
    - button "Frame button" [ref=f1e2]"""


def _png(width: int, height: int) -> bytes:
    """A real, minimal PNG header of the given size."""
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    chunk = b"IHDR" + ihdr
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", len(ihdr)) + chunk + struct.pack(">I", zlib.crc32(chunk))


# -- A fake Playwright, sync and async ----------------------------------------


class _Ledger:
    """Everything the fake was asked, in order, and what it should do instead of answering."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.counts: dict[str, int] = {}
        self.fail: dict[str, BaseException] = {}
        self.tree = SNAPSHOT
        self.url = "about:blank"
        self.status: int | None = 200
        self.closed = False
        self.connected = True
        self.connect_error: BaseException | None = None
        self.image: bytes | None = None  # what a screenshot returns instead of a PNG, as some vendors' browsers do

    def act(self, *call: Any) -> None:
        self.calls.append(call)
        failure = self.fail.get(call[0])
        if failure is not None:
            raise failure

    def names(self) -> list[str]:
        return [call[0] for call in self.calls]


class _Locator:
    def __init__(self, ledger: _Ledger, selector: str) -> None:
        self.ledger, self.selector = ledger, selector

    def count(self) -> int:
        return self.ledger.counts.get(self.selector, 1)

    def click(self, timeout: float) -> None:
        self.ledger.act("click", self.selector, timeout)

    def fill(self, text: str, timeout: float) -> None:
        self.ledger.act("fill", self.selector, text, timeout)

    def press(self, key: str, timeout: float) -> None:
        self.ledger.act("locator.press", self.selector, key, timeout)

    def inner_text(self, timeout: float) -> str:
        self.ledger.act("inner_text", self.selector, timeout)
        return "the page text"

    def evaluate(self, expression: str, timeout: float) -> str:
        self.ledger.act("evaluate", self.selector, timeout)
        return "<html></html>"


class _Page:
    def __init__(self, ledger: _Ledger) -> None:
        self.ledger = ledger
        self.listeners: dict[str, Callable[[Any], None]] = {}
        self.main_frame = SimpleNamespace(parent_frame=None)
        self.keyboard = SimpleNamespace(press=lambda key: ledger.act("keyboard.press", key))

    @property
    def url(self) -> str:
        return self.ledger.url

    def title(self) -> str:
        return "Page actions"

    def goto(self, url: str, wait_until: Any, timeout: float) -> Any:
        self.ledger.act("goto", url, wait_until, timeout)
        self.ledger.url = url
        return None if self.ledger.status is None else SimpleNamespace(status=self.ledger.status)

    def aria_snapshot(self, mode: str, timeout: float) -> str:
        self.ledger.act("aria_snapshot", mode, timeout)
        return self.ledger.tree

    def screenshot(self, type: str, full_page: bool, timeout: float) -> bytes:
        self.ledger.act("screenshot", type, full_page, timeout)
        return self.ledger.image or _png(1280, 4000 if full_page else 720)

    def content(self) -> str:
        self.ledger.act("content")
        return "<html></html>"

    def locator(self, selector: str) -> _Locator:
        return _Locator(self.ledger, selector)

    def on(self, event: str, callback: Callable[[Any], None]) -> None:
        self.listeners[event] = callback

    def is_closed(self) -> bool:
        return self.ledger.closed


class _Browser:
    def __init__(self, ledger: _Ledger, page: Any) -> None:
        self.ledger = ledger
        self.contexts = [SimpleNamespace(pages=[page])]

    def close(self) -> None:
        self.ledger.calls.append(("browser.close",))
        self.ledger.connected = False

    def is_connected(self) -> bool:
        return self.ledger.connected


class _Playwright:
    def __init__(self, ledger: _Ledger, page: Any) -> None:
        self.ledger, self.page = ledger, page
        self.chromium = SimpleNamespace(connect_over_cdp=self.connect_over_cdp)

    def connect_over_cdp(self, url: str, timeout: float) -> _Browser:
        self.ledger.calls.append(("connect", url, timeout))
        if self.ledger.connect_error is not None:
            raise self.ledger.connect_error
        return _Browser(self.ledger, self.page)

    def stop(self) -> None:
        self.ledger.calls.append(("playwright.stop",))


def _awaitable(call: Callable[..., Any]) -> Callable[..., Any]:
    async def run(*args: Any, **kwargs: Any) -> Any:
        return call(*args, **kwargs)

    return run


class _AsyncLocator(_Locator):
    count = _awaitable(_Locator.count)
    click = _awaitable(_Locator.click)
    fill = _awaitable(_Locator.fill)
    press = _awaitable(_Locator.press)
    inner_text = _awaitable(_Locator.inner_text)
    evaluate = _awaitable(_Locator.evaluate)


class _AsyncPage(_Page):
    title = _awaitable(_Page.title)
    goto = _awaitable(_Page.goto)
    aria_snapshot = _awaitable(_Page.aria_snapshot)
    screenshot = _awaitable(_Page.screenshot)
    content = _awaitable(_Page.content)

    def __init__(self, ledger: _Ledger) -> None:
        super().__init__(ledger)
        self.keyboard = SimpleNamespace(press=_awaitable(lambda key: ledger.act("keyboard.press", key)))

    def locator(self, selector: str) -> _AsyncLocator:
        return _AsyncLocator(self.ledger, selector)


class _AsyncBrowser(_Browser):
    close = _awaitable(_Browser.close)


class _AsyncPlaywright(_Playwright):
    stop = _awaitable(_Playwright.stop)

    async def connect_over_cdp(self, url: str, timeout: float) -> _AsyncBrowser:  # type: ignore[override]
        self.ledger.calls.append(("connect", url, timeout))
        if self.ledger.connect_error is not None:
            raise self.ledger.connect_error
        return _AsyncBrowser(self.ledger, self.page)


@pytest.fixture
def ledger(monkeypatch: pytest.MonkeyPatch) -> _Ledger:
    """Stand a fake Playwright in for both APIs, and hand back what it records."""
    ledger = _Ledger()
    sync_page, async_page = _Page(ledger), _AsyncPage(ledger)
    ledger.pages = (sync_page, async_page)  # type: ignore[attr-defined]

    def sync_start() -> Any:
        return SimpleNamespace(start=lambda: _Playwright(ledger, sync_page))

    def async_start() -> Any:
        return SimpleNamespace(start=_awaitable(lambda: _AsyncPlaywright(ledger, async_page)))

    monkeypatch.setattr(_page, "_THREAD", threading.local())
    monkeypatch.setattr(_page, "_sync_api", lambda: sync_start)
    monkeypatch.setattr(_page, "_async_api", lambda: async_start)
    return ledger


@pytest.fixture
def provider() -> FakeBrowserProvider:
    return FakeBrowserProvider()


def _ms(seconds: float) -> float:
    return seconds * MS_PER_SECOND


def _timeout_error(message: str = "Locator.click: Timeout 500ms exceeded.") -> BaseException:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    return PlaywrightTimeoutError(message)


def _playwright_error(message: str) -> BaseException:
    from playwright.sync_api import Error

    return Error(message)


# -- Compaction ----------------------------------------------------------------


def test_compaction_drops_unnamed_wrappers_and_link_urls_but_keeps_every_other_line() -> None:
    assert compact_snapshot(SNAPSHOT) == (
        '- heading "Page actions" [level=1] [ref=e2]\n'
        '- textbox "Search" [ref=e5]\n'
        '- button "Buy Apple" [ref=e8]\n'
        '- link "Next page" [ref=e12] [cursor=pointer]\n'
        "- generic [ref=e13] [cursor=pointer]: Clickable div\n"
        "- iframe [ref=e20]:\n"
        '  - button "Frame button" [ref=f1e2]'
    )


def test_a_generic_that_is_clickable_or_has_text_is_kept() -> None:
    tree = "- generic [ref=e3] [cursor=pointer]:\n  - img [ref=e4]\n- generic [ref=e5]: Price: 3"
    assert compact_snapshot(tree) == tree


def test_a_screenshot_a_backend_returns_as_jpeg_comes_back_as_png(
    ledger: _Ledger, provider: FakeBrowserProvider
) -> None:
    ledger.image = jpeg(64, 48)
    with provider.open(BrowserRequest()) as browser:
        shot = browser.screenshot()
    assert shot.png.startswith(PNG_SIGNATURE)
    assert (shot.width, shot.height) == (64, 48)


async def test_the_async_screenshot_converts_a_jpeg_too(ledger: _Ledger, provider: FakeBrowserProvider) -> None:
    _record_release(provider, ledger)
    ledger.image = jpeg(64, 48)
    async with await provider.aopen(BrowserRequest()) as browser:
        shot = await browser.screenshot()
    assert shot.png.startswith(PNG_SIGNATURE)
    assert (shot.width, shot.height) == (64, 48)


# -- One connection per session ------------------------------------------------


def test_every_action_shares_one_connection_and_sends_the_minimum(
    ledger: _Ledger, provider: FakeBrowserProvider
) -> None:
    with provider.open(BrowserRequest()) as browser:
        assert browser.goto("file:///page.html").status_code == 200
        snapshot = browser.snapshot()
        browser.click(ref="e2")
        browser.click(selector="#counter", timeout=2)
        browser.type("hello", ref="e5", submit=True)
        browser.press("Escape")
        shot = browser.screenshot(full_page=True)
        text = browser.read()
        html = browser.read(format="html")

    default = _ms(BROWSER_ACTION_TIMEOUT)
    assert ledger.calls == [
        ("connect", browser.cdp_url, _ms(BROWSER_CONNECT_TIMEOUT)),
        ("goto", "file:///page.html", None, default),
        ("aria_snapshot", "ai", default),
        ("click", "aria-ref=e2", default),
        ("click", "#counter", _ms(2)),
        ("fill", "aria-ref=e5", "hello", default),
        ("locator.press", "aria-ref=e5", "Enter", default),
        ("keyboard.press", "Escape"),
        ("screenshot", "png", True, default),
        ("inner_text", "body", default),
        ("evaluate", ":root", default),
        ("browser.close",),
        ("playwright.stop",),
    ]
    assert snapshot.url == "file:///page.html"
    assert snapshot.tree == compact_snapshot(SNAPSHOT)
    assert (shot.width, shot.height, shot.url) == (1280, 4000, "file:///page.html")
    assert shot.png.startswith(b"\x89PNG")
    assert (text.content, text.format, text.title) == ("the page text", "text", "Page actions")
    assert (html.content, html.format) == ("<html></html>", "html")


def test_goto_reports_no_status_when_the_navigation_produced_no_response(
    ledger: _Ledger, provider: FakeBrowserProvider
) -> None:
    ledger.status = None
    with provider.open(BrowserRequest()) as browser:
        assert browser.goto("file:///page.html#top", wait_until="commit").status_code is None
    assert ledger.calls[1] == ("goto", "file:///page.html#top", "commit", _ms(BROWSER_ACTION_TIMEOUT))


def test_a_handle_that_only_wants_the_cdp_url_never_connects(ledger: _Ledger, provider: FakeBrowserProvider) -> None:
    with provider.open(BrowserRequest()) as browser:
        assert browser.cdp_url
    assert ledger.calls == []


# -- Refs ----------------------------------------------------------------------


def test_a_ref_before_any_snapshot_is_stale_and_touches_nothing(ledger: _Ledger, provider: FakeBrowserProvider) -> None:
    with provider.open(BrowserRequest()) as browser, pytest.raises(StaleElementError, match="e2"):
        browser.click(ref="e2")
    assert "click" not in ledger.names()


def test_a_ref_from_an_older_snapshot_is_stale(ledger: _Ledger, provider: FakeBrowserProvider) -> None:
    with provider.open(BrowserRequest()) as browser:
        browser.snapshot()
        ledger.tree = '- button "Other" [ref=e30]'
        browser.snapshot()
        with pytest.raises(StaleElementError):
            browser.click(ref="e2")
        browser.click(ref="e30")


def test_a_ref_whose_element_was_removed_is_stale_not_retargeted(
    ledger: _Ledger, provider: FakeBrowserProvider
) -> None:
    with provider.open(BrowserRequest()) as browser:
        browser.snapshot()
        ledger.counts["aria-ref=e8"] = 0
        with pytest.raises(StaleElementError, match="re-rendered"):
            browser.type("x", ref="e8")
    assert "fill" not in ledger.names()


def test_a_main_frame_navigation_clears_refs_and_a_child_frame_one_does_not(
    ledger: _Ledger, provider: FakeBrowserProvider
) -> None:
    with provider.open(BrowserRequest()) as browser:
        browser.snapshot()
        navigated = ledger.pages[0].listeners["framenavigated"]  # type: ignore[attr-defined]
        navigated(SimpleNamespace(parent_frame=object()))
        browser.click(ref="e2")
        navigated(SimpleNamespace(parent_frame=None))
        with pytest.raises(StaleElementError):
            browser.click(ref="e2")


def test_a_ref_that_times_out_after_its_element_went_away_is_stale(
    ledger: _Ledger, provider: FakeBrowserProvider
) -> None:
    pytest.importorskip("playwright")

    class Vanishing(_Locator):
        calls = 0

        def count(self) -> int:
            Vanishing.calls += 1
            return 1 if Vanishing.calls == 1 else 0

    ledger.fail["click"] = _timeout_error()
    with provider.open(BrowserRequest()) as browser:
        browser.snapshot()
        ledger.pages[0].locator = lambda selector: Vanishing(ledger, selector)  # type: ignore[attr-defined]
        with pytest.raises(StaleElementError) as caught:
            browser.click(ref="e2")
    assert caught.value.__cause__ is ledger.fail["click"]


@pytest.mark.parametrize("target", [{}, {"ref": "e2", "selector": "#a"}], ids=["neither", "both"])
def test_exactly_one_of_ref_and_selector_is_required(
    ledger: _Ledger, provider: FakeBrowserProvider, target: dict[str, str]
) -> None:
    with provider.open(BrowserRequest()) as browser, pytest.raises(ValueError, match="exactly one"):
        browser.click(**target)
    assert ledger.calls == []


# -- Errors --------------------------------------------------------------------


def test_a_selector_that_matched_nothing_is_element_not_found(ledger: _Ledger, provider: FakeBrowserProvider) -> None:
    pytest.importorskip("playwright")
    ledger.fail["click"] = _timeout_error()
    ledger.counts["#nope"] = 0
    with provider.open(BrowserRequest()) as browser, pytest.raises(ElementNotFoundError) as caught:
        browser.click(selector="#nope", timeout=0.5)
    assert caught.value.action == "click"
    assert caught.value.session_id == browser.session_id
    assert caught.value.__cause__ is ledger.fail["click"]


def test_a_timeout_on_an_element_that_is_there_is_a_timeout(ledger: _Ledger, provider: FakeBrowserProvider) -> None:
    pytest.importorskip("playwright")
    ledger.fail["click"] = _timeout_error()
    with provider.open(BrowserRequest()) as browser, pytest.raises(BrowserActionTimeoutError, match=r"0\.5s"):
        browser.click(selector="#covered", timeout=0.5)


def test_a_failed_goto_is_a_navigation_error_with_playwrights_on_cause(
    ledger: _Ledger, provider: FakeBrowserProvider
) -> None:
    pytest.importorskip("playwright")
    ledger.fail["goto"] = _playwright_error("Page.goto: net::ERR_NAME_NOT_RESOLVED at https://nowhere.test/")
    with provider.open(BrowserRequest()) as browser, pytest.raises(NavigationError, match="ERR_NAME_NOT_RESOLVED"):
        browser.goto("https://nowhere.test/")


def test_an_action_on_a_closed_page_is_page_closed(ledger: _Ledger, provider: FakeBrowserProvider) -> None:
    pytest.importorskip("playwright")
    with provider.open(BrowserRequest()) as browser:
        browser.goto("file:///page.html")
        ledger.closed = True
        ledger.fail["screenshot"] = _playwright_error("Target page, context or browser has been closed")
        with pytest.raises(PageClosedError):
            browser.screenshot()


def test_an_action_after_release_is_page_closed_and_does_not_reconnect(
    ledger: _Ledger, provider: FakeBrowserProvider
) -> None:
    with provider.open(BrowserRequest()) as browser:
        browser.goto("file:///page.html")
    with pytest.raises(PageClosedError, match="released"):
        browser.goto("file:///page.html")
    assert ledger.names().count("connect") == 1


def _chain(error: BaseException) -> Iterator[BaseException]:
    """The error and everything reachable from it through ``__cause__`` and ``__context__``."""
    seen: set[int] = set()
    pending: list[BaseException | None] = [error]
    while pending:
        current = pending.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        pending.extend((current.__cause__, current.__context__))


def _texts(error: BaseException) -> Iterator[str]:
    for linked in _chain(error):
        yield str(linked)
        yield from (str(arg) for arg in linked.args)


@pytest.mark.parametrize("failing", ["connect", "goto", "screenshot"])
def test_the_cdp_url_never_reaches_an_error_or_anything_chained_to_it(
    ledger: _Ledger, provider: FakeBrowserProvider, failing: str
) -> None:
    pytest.importorskip("playwright")
    with provider.open(BrowserRequest()) as browser:
        secret = browser.cdp_url
        error = _playwright_error(f"{failing}: connect ECONNREFUSED {secret}")
        if failing == "connect":
            ledger.connect_error = error
        else:
            browser.goto("file:///page.html")
            ledger.fail[failing] = error
        with pytest.raises(BrowserActionError) as caught:
            if failing == "screenshot":
                browser.screenshot()
            else:
                browser.goto("file:///page.html")
    key = secret.split("apiKey=")[1].split("&")[0]
    texts = list(_texts(caught.value))
    assert len(list(_chain(caught.value))) >= 2
    assert not [text for text in texts if key in text or secret in text]
    assert "ECONNREFUSED" in str(caught.value)
    if failing == "connect":
        assert "playwright.stop" in ledger.names()


@pytest.mark.parametrize("timeout", [0, -1])
def test_a_timeout_must_be_positive_because_playwright_reads_zero_as_forever(
    ledger: _Ledger, provider: FakeBrowserProvider, timeout: float
) -> None:
    with provider.open(BrowserRequest()) as browser, pytest.raises(ValueError, match="positive"):
        browser.snapshot(timeout=timeout)


def test_a_snapshot_over_max_chars_is_cut_at_a_line_and_offers_no_ref_past_the_cut(
    ledger: _Ledger, provider: FakeBrowserProvider
) -> None:
    full = compact_snapshot(SNAPSHOT)
    cap = full.index("- button")
    with provider.open(BrowserRequest()) as browser:
        snapshot = browser.snapshot(max_chars=cap)
        assert snapshot.truncated
        assert snapshot.tree == full[: cap - 1]
        assert len(snapshot.tree) <= cap
        browser.click(ref="e5")
        with pytest.raises(StaleElementError):
            browser.click(ref="e8")
        whole = browser.snapshot(max_chars=len(full))
        assert (whole.tree, whole.truncated) == (full, False)
        with pytest.raises(ValueError, match="max_chars"):
            browser.snapshot(max_chars=0)


def test_a_screenshot_serializes_to_json_as_base64_and_back() -> None:
    shot = Screenshot(png=_png(2, 3), width=2, height=3, url="file:///page.html")
    payload = shot.model_dump_json()
    assert base64.b64encode(shot.png).decode() in payload
    assert Screenshot.model_validate_json(payload) == shot


def test_two_sync_handles_on_one_thread_share_one_playwright_with_a_connection_each(
    ledger: _Ledger, provider: FakeBrowserProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    starts: list[int] = []
    sync_start = _page._sync_api()

    def counted() -> Any:
        starts.append(1)
        return sync_start()

    monkeypatch.setattr(_page, "_sync_api", lambda: counted)
    first, second = provider.open(BrowserRequest()), provider.open(BrowserRequest())
    first.goto("file:///one.html")
    second.goto("file:///two.html")
    first.release()
    assert "playwright.stop" not in ledger.names()
    second.snapshot()
    second.release()
    assert starts == [1]
    assert ledger.names().count("connect") == 2
    assert ledger.names()[-1] == "playwright.stop"


def test_read_refuses_markdown_rather_than_returning_something_else(
    ledger: _Ledger, provider: FakeBrowserProvider
) -> None:
    with provider.open(BrowserRequest()) as browser, pytest.raises(ValueError, match="'text' or 'html'"):
        browser.read(format="markdown")  # type: ignore[arg-type]


def test_every_action_error_is_a_web_router_error() -> None:
    for kind in (StaleElementError, ElementNotFoundError, NavigationError, BrowserActionTimeoutError, PageClosedError):
        assert issubclass(kind, BrowserActionError)
    assert issubclass(BrowserActionError, WebRouterError)


def test_without_playwright_the_actions_name_the_extra_to_install(
    monkeypatch: pytest.MonkeyPatch, provider: FakeBrowserProvider
) -> None:
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    with (
        provider.open(BrowserRequest()) as browser,
        pytest.raises(ConfigurationError, match=f"--extra {EXTRA_BROWSER}"),
    ):
        browser.goto("file:///page.html")


async def test_without_playwright_the_async_actions_name_the_extra_too(
    monkeypatch: pytest.MonkeyPatch, provider: FakeBrowserProvider
) -> None:
    monkeypatch.setitem(sys.modules, "playwright.async_api", None)
    async with await provider.aopen(BrowserRequest()) as browser:
        with pytest.raises(ConfigurationError, match=EXTRA_BROWSER):
            await browser.goto("file:///page.html")


# -- The sync handle and event loops -------------------------------------------


async def test_the_sync_handle_refuses_to_run_inside_an_event_loop(
    ledger: _Ledger, provider: FakeBrowserProvider
) -> None:
    with provider.open(BrowserRequest()) as browser, pytest.raises(BrowserActionError, match="AsyncWebRouter"):
        browser.goto("file:///page.html")
    assert ledger.calls == []


# -- Release -------------------------------------------------------------------


def _record_release(provider: FakeBrowserProvider, ledger: _Ledger) -> None:
    release = provider._release

    def recorded(session_id: str) -> None:
        ledger.calls.append(("release", session_id))
        release(session_id)

    provider._release = recorded  # type: ignore[method-assign]


def test_release_closes_the_connection_before_the_session(ledger: _Ledger, provider: FakeBrowserProvider) -> None:
    _record_release(provider, ledger)
    with provider.open(BrowserRequest()) as browser:
        browser.goto("file:///page.html")
    assert ledger.names()[-3:] == ["browser.close", "playwright.stop", "release"]


def test_a_block_that_raises_still_closes_the_connection_then_releases(
    ledger: _Ledger, provider: FakeBrowserProvider
) -> None:
    _record_release(provider, ledger)
    with pytest.raises(KeyError), provider.open(BrowserRequest()) as browser:
        browser.goto("file:///page.html")
        raise KeyError("boom")
    assert ledger.names()[-3:] == ["browser.close", "playwright.stop", "release"]
    assert browser.released


def test_a_connection_that_will_not_close_does_not_stop_the_release(
    ledger: _Ledger, provider: FakeBrowserProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(self: _Browser) -> None:
        raise RuntimeError("already gone")

    monkeypatch.setattr(_Browser, "close", refuse)
    with provider.open(BrowserRequest()) as browser:
        browser.goto("file:///page.html")
    assert provider.released == [browser.session_id]
    assert "playwright.stop" in ledger.names()


def test_a_router_closing_releases_through_the_same_path(ledger: _Ledger) -> None:
    router = WebRouter()
    browser = router.open_browser(FakeBrowserProvider())
    browser.goto("file:///page.html")
    router.close()
    assert browser.released
    assert "browser.close" in ledger.names()


# -- The async handle ----------------------------------------------------------


async def test_the_async_handle_runs_every_action_over_one_connection(
    ledger: _Ledger, provider: FakeBrowserProvider
) -> None:
    _record_release(provider, ledger)
    async with await provider.aopen(BrowserRequest()) as browser:
        await asyncio.gather(browser.goto("file:///page.html"), browser.snapshot())
        await browser.click(ref="e2")
        await browser.type("hello", selector="#q", submit=True)
        await browser.press("Escape")
        shot = await browser.screenshot()
        document = await browser.read()
        ledger.counts["aria-ref=e8"] = 0
        with pytest.raises(StaleElementError):
            await browser.click(ref="e8")
    assert ledger.names().count("connect") == 1
    assert (shot.width, shot.height) == (1280, 720)
    assert document.content == "the page text"
    assert ledger.names()[-3:] == ["browser.close", "playwright.stop", "release"]
    with pytest.raises(PageClosedError):
        await browser.snapshot()


def test_a_setup_that_fails_after_connecting_closes_that_connection_and_keeps_the_error(
    ledger: _Ledger, provider: FakeBrowserProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = provider.open(BrowserRequest())
    other.goto("file:///other.html")
    connected = _Browser.__init__

    def without_contexts(self: _Browser, ledger: _Ledger, page: Any) -> None:
        connected(self, ledger, page)
        self.contexts = []

    def refuse(self: _Browser) -> None:
        raise RuntimeError("Browser.new_context: Target closed")

    monkeypatch.setattr(_Browser, "__init__", without_contexts)
    monkeypatch.setattr(_Browser, "new_context", refuse, raising=False)

    with provider.open(BrowserRequest()) as browser, pytest.raises(BrowserActionError, match="Target closed") as caught:
        browser.goto("file:///page.html")

    assert isinstance(caught.value.__cause__, RuntimeError)
    assert ledger.names()[-2:] == ["connect", "browser.close"]
    # The other handle's driver is untouched, and so is its connection.
    assert "playwright.stop" not in ledger.names()
    other.snapshot()
    other.release()


async def _cancelled_mid_connect(ledger: _Ledger, browser: Any, stall: asyncio.Event) -> asyncio.Task[Any]:
    task = asyncio.ensure_future(browser.goto("file:///page.html"))
    await asyncio.wait_for(stall.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    return task


async def test_a_cancellation_while_connecting_stops_playwright_and_is_raised_unchanged(
    ledger: _Ledger, provider: FakeBrowserProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    stall = asyncio.Event()

    async def hang(self: _AsyncPlaywright, url: str, timeout: float) -> Any:
        stall.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(_AsyncPlaywright, "connect_over_cdp", hang)
    browser = await provider.aopen(BrowserRequest())

    task = await _cancelled_mid_connect(ledger, browser, stall)

    assert task.cancelled()
    assert ledger.names() == ["playwright.stop"]
    await browser.release()


async def test_a_cancellation_after_connecting_closes_the_browser_then_stops_playwright(
    ledger: _Ledger, provider: FakeBrowserProvider
) -> None:
    stall = asyncio.Event()

    async def hook(page: Any) -> None:
        stall.set()
        await asyncio.Event().wait()

    browser = await provider.aopen(BrowserRequest())
    browser.on_connect(hook)

    task = await _cancelled_mid_connect(ledger, browser, stall)

    assert task.cancelled()
    assert ledger.names()[-2:] == ["browser.close", "playwright.stop"]
    await browser.release()
    assert ledger.names().count("playwright.stop") == 1, "nothing was left for the release to stop"


async def test_a_release_during_the_first_connect_closes_the_connection_it_made(
    ledger: _Ledger, provider: FakeBrowserProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    stall, resume = asyncio.Event(), asyncio.Event()
    connect = _AsyncPlaywright.connect_over_cdp

    async def slow(self: _AsyncPlaywright, url: str, timeout: float) -> Any:
        stall.set()
        await resume.wait()
        return await connect(self, url, timeout)

    monkeypatch.setattr(_AsyncPlaywright, "connect_over_cdp", slow)
    browser = await provider.aopen(BrowserRequest())
    task = asyncio.ensure_future(browser.goto("file:///page.html"))
    await asyncio.wait_for(stall.wait(), timeout=1)
    await browser.release()
    resume.set()

    with pytest.raises(PageClosedError, match="released"):
        await task
    assert ledger.names()[-2:] == ["browser.close", "playwright.stop"]


# -- The undocumented dependency -----------------------------------------------


@needs_playwright
def test_the_playwright_driver_still_registers_the_aria_ref_engine() -> None:
    import playwright

    bundles = (Path(playwright.__file__).parent / "driver" / "package" / "lib").glob("*.js")
    registered = re.compile(r"""["']aria-ref["']\s*,\s*this\._createAriaRefEngine\(""")
    assert any(registered.search(bundle.read_text(errors="replace")) for bundle in bundles), (
        "Playwright no longer registers the aria-ref= selector engine that snapshot refs resolve through. "
        "Pin the browser extra back to a release that does, or move refs to our own tagging (see _page.py)."
    )


# -- Against a real browser ----------------------------------------------------


def _chrome() -> str | None:
    from web_router.providers.local import find_browser

    try:
        return find_browser()
    except ConfigurationError:
        return None


needs_chrome = pytest.mark.skipif(
    not HAS_PLAYWRIGHT or _chrome() is None, reason="needs Playwright and a Chromium-family browser"
)

ACTIONS = (FIXTURES / "actions.html").as_uri()
NEXT = (FIXTURES / "next.html").as_uri()


def _ref(tree: str, needle: str) -> str:
    for line in tree.splitlines():
        if needle in line:
            match = re.search(r"\[ref=([a-z0-9]+)\]", line)
            if match:
                return match.group(1)
    raise AssertionError(f"{needle!r} is not in the snapshot:\n{tree}")


@pytest.fixture
def local_browser() -> Iterator[Any]:
    with WebRouter("local") as router, router.open_browser() as browser:
        yield browser


@pytest.mark.browser
@needs_chrome
def test_every_action_works_against_a_real_page(local_browser: Any) -> None:
    browser = local_browser
    state = browser.goto(ACTIONS)
    assert (state.url, state.title) == (ACTIONS, "Page actions")

    tree = browser.snapshot().tree
    assert '- heading "Page actions" [level=1]' in tree
    assert "/url:" not in tree
    assert not re.search(r"^\s*- generic \[ref=[a-z0-9]+\]:?$", tree, re.MULTILINE)

    browser.type("hello", ref=_ref(tree, 'textbox "Search"'), submit=True)
    assert "submitted:hello:false" in browser.read().content

    browser.click(ref=_ref(tree, 'checkbox "I agree"'))
    browser.click(selector="button[type=submit]")
    assert "submitted:hello:true" in browser.read().content

    browser.click(ref=_ref(tree, 'button "Count"'))
    assert "clicked:1" in browser.read().content

    browser.press("Escape")
    assert "pressed:Escape" in browser.read().content

    browser.click(ref=_ref(tree, "Frame button"))
    assert "Frame clicked" in browser.snapshot().tree

    viewport = browser.screenshot()
    full = browser.screenshot(full_page=True)
    assert viewport.png.startswith(b"\x89PNG") and full.png.startswith(b"\x89PNG")
    assert full.height > viewport.height > 0
    assert png_size(full.png) == (full.width, full.height)

    html = browser.read(format="html")
    assert html.format == "html" and '<ul id="list">' in html.content


@pytest.mark.browser
@needs_chrome
def test_the_aria_ref_engine_resolves_a_snapshot_ref_live(local_browser: Any) -> None:
    local_browser.goto(ACTIONS)
    ref = _ref(local_browser.snapshot().tree, 'button "Count"')
    page = local_browser._page._page
    assert page.locator(f"aria-ref={ref}").count() == 1


@pytest.mark.browser
@needs_chrome
def test_a_ref_used_after_a_re_render_a_new_snapshot_or_a_navigation_is_stale(local_browser: Any) -> None:
    browser = local_browser
    browser.goto(ACTIONS)
    tree = browser.snapshot().tree
    apple = _ref(tree, "Buy Apple")
    browser.click(ref=_ref(tree, "Re-render list"))
    with pytest.raises(StaleElementError, match="re-rendered"):
        browser.click(ref=apple)

    fresh = browser.snapshot().tree
    browser.click(ref=_ref(fresh, "Buy Apple"))
    assert "bought:Apple:gen2" in browser.read().content

    count = _ref(fresh, 'button "Count"')
    browser.goto(NEXT)
    with pytest.raises(StaleElementError, match="latest snapshot"):
        browser.click(ref=count)


@pytest.mark.browser
@needs_chrome
def test_real_failures_are_told_apart(local_browser: Any) -> None:
    browser = local_browser
    browser.goto(ACTIONS)
    with pytest.raises(ElementNotFoundError):
        browser.click(selector="#nope", timeout=0.5)
    with pytest.raises(NavigationError, match="ERR_FILE_NOT_FOUND"):
        browser.goto((FIXTURES / "missing.html").as_uri())


@pytest.mark.browser
@needs_chrome
async def test_the_sync_handle_refuses_inside_a_running_loop_against_a_real_browser() -> None:
    with WebRouter("local") as router, router.open_browser() as browser:
        with pytest.raises(BrowserActionError, match="running event loop"):
            browser.goto(ACTIONS)
        assert browser._page._browser is None


@pytest.mark.browser
@needs_chrome
def test_release_closes_the_real_connection_before_ending_the_session() -> None:
    seen: dict[str, bool] = {}
    with WebRouter("local") as router:
        browser = router.open_browser()
        provider = router.get_browser_provider("local")
        release = provider._release

        def recorded(session_id: str) -> None:
            seen["connection_open"] = browser._page._browser is not None
            release(session_id)

        provider._release = recorded  # type: ignore[method-assign]
        with browser:
            browser.goto(ACTIONS)
            connection = browser._page._browser
        assert seen == {"connection_open": False}
        assert not connection.is_connected()


@pytest.mark.browser
@needs_chrome
async def test_the_async_handle_drives_a_real_page() -> None:
    async with AsyncWebRouter("local") as router, await router.open_browser() as browser:
        assert (await browser.goto(ACTIONS)).title == "Page actions"
        tree = (await browser.snapshot()).tree
        await browser.type("async", ref=_ref(tree, 'textbox "Search"'), submit=True)
        assert "submitted:async" in (await browser.read()).content
        await browser.click(ref=_ref(tree, 'button "Count"'))
        await browser.press("Escape")
        assert "pressed:Escape" in (await browser.read()).content
        assert (await browser.screenshot()).png.startswith(b"\x89PNG")
        await browser.click(ref=_ref(tree, "Re-render list"))
        with pytest.raises(StaleElementError):
            await browser.click(ref=_ref(tree, "Buy Apple"))
        connection = browser._page._browser
    assert not connection.is_connected()


@pytest.mark.browser
@needs_chrome
def test_two_real_handles_used_alternately_on_one_thread() -> None:
    with WebRouter("local") as router, router.open_browser() as first, router.open_browser() as second:
        first.goto(ACTIONS)
        second.goto(NEXT)
        first_tree = first.snapshot().tree
        second_tree = second.snapshot().tree
        first.click(ref=_ref(first_tree, 'button "Count"'))
        second.click(ref=_ref(second_tree, "Only button"))
        assert "clicked:1" in first.read().content
        assert second.read().title == "Next page"
        first.release()
        assert second.goto(ACTIONS).title == "Page actions"


@pytest.mark.browser
@needs_chrome
def test_a_handle_dropped_unreleased_does_not_break_the_next_one() -> None:
    from web_router.providers.local import LocalBrowserProvider

    provider = LocalBrowserProvider()
    try:
        dropped = provider.open(BrowserRequest())
        dropped.goto(ACTIONS)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ResourceWarning)
            del dropped
            gc.collect()
        with provider.open(BrowserRequest()) as fresh:
            assert fresh.goto(NEXT).title == "Next page"
            assert "Only button" in fresh.snapshot().tree
    finally:
        provider.close()
        # The dropped handle never gave its share of this thread's Playwright
        # back; stop it so later tests start from a clean thread.
        driver = _page._thread_driver()
        if driver is not None:
            driver.playwright.stop()
            _page._THREAD.driver = None
