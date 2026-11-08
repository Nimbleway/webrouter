"""Page actions: what a browser handle does with the page it holds.

Written once, over the session's CDP URL through Playwright, rather than per
vendor. Every backend speaks CDP, so a click behaves the same whichever backend
serves the session. :class:`SyncPage` and :class:`AsyncPage` are the two halves,
one per Playwright API, and each holds a single connection for the life of its
handle: several backends end a session when its CDP client disconnects, so a
connection per action would end the session after the first.

Playwright is imported on the first action, so a caller who only wants the CDP URL never needs it.

Refs come from ``aria_snapshot(mode="ai")`` and resolve through the
``aria-ref=`` selector engine, which knows only the most recent snapshot of
each frame. That is why the handle is the only thing here that takes one:
``read`` and the rest must never call ``aria_snapshot``, or every ref an agent
holds stops resolving.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, NoReturn, TypeVar
from urllib.parse import urlsplit

from web_router.constants import (
    BROWSER_ACTION_TIMEOUT,
    BROWSER_ARIA_REF_SELECTOR,
    BROWSER_CONNECT_TIMEOUT,
    BROWSER_HTML_SCRIPT,
    BROWSER_HTML_SELECTOR,
    BROWSER_MAIN_FRAME_EVENT,
    BROWSER_REF_PATTERN,
    BROWSER_SCREENSHOT_TYPE,
    BROWSER_SECRET_PLACEHOLDER,
    BROWSER_SNAPSHOT_INDENT,
    BROWSER_SNAPSHOT_MODE,
    BROWSER_SNAPSHOT_URL_PREFIX,
    BROWSER_SNAPSHOT_WRAPPER_PATTERN,
    BROWSER_SUBMIT_KEY,
    BROWSER_TEXT_SELECTOR,
    EXTRA_BROWSER,
    MS_PER_SECOND,
    MSG_BROWSER_ACTION_TIMEOUT,
    MSG_BROWSER_ACTIONS_NOT_INSTALLED,
    MSG_BROWSER_BAD_MAX_CHARS,
    MSG_BROWSER_BAD_TIMEOUT,
    MSG_BROWSER_CONNECT_FAILED,
    MSG_BROWSER_HANDLE_RELEASED,
    MSG_BROWSER_HOOK_TOO_LATE,
    MSG_BROWSER_NAVIGATION_FAILED,
    MSG_BROWSER_NOT_FOUND,
    MSG_BROWSER_ONE_TARGET,
    MSG_BROWSER_PAGE_CLOSED,
    MSG_BROWSER_READ_FORMAT,
    MSG_BROWSER_REF_DETACHED,
    MSG_BROWSER_REF_NOT_IN_SNAPSHOT,
    MSG_BROWSER_SYNC_IN_LOOP,
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
from web_router.providers._png import as_png, png_size
from web_router.types import ExtractedDocument
from web_router.types_page import PageFormat, PageLoadState, PageSnapshot, PageState, Screenshot

if TYPE_CHECKING:
    from playwright.async_api import Browser as AsyncBrowser
    from playwright.async_api import Frame as AsyncFrame
    from playwright.async_api import Locator as AsyncLocator
    from playwright.async_api import Page as AsyncPlaywrightPage
    from playwright.async_api import Playwright as AsyncPlaywright
    from playwright.sync_api import Browser, Frame, Locator, Page, Playwright

_T = TypeVar("_T")

_REF = re.compile(BROWSER_REF_PATTERN)
_WRAPPER = re.compile(BROWSER_SNAPSHOT_WRAPPER_PATTERN)
_READ_FORMATS: tuple[PageFormat, ...] = ("text", "html")


def compact_snapshot(tree: str) -> str:
    """Drop unnamed wrapper containers and link-URL lines from a ``mode="ai"`` snapshot.

    A dropped container's children move up a level, so the tree keeps its
    shape. A line that introduced children and has none left loses its
    trailing colon. Refs are left exactly as Playwright printed them.
    """
    lines = tree.splitlines()
    kept: list[tuple[int, str, bool]] = []
    open_wrappers: list[int] = []
    for index, line in enumerate(lines):
        text = line.lstrip()
        indent = len(line) - len(text)
        while open_wrappers and indent <= open_wrappers[-1]:
            open_wrappers.pop()
        if text.startswith(BROWSER_SNAPSHOT_URL_PREFIX):
            continue
        if _WRAPPER.match(line):
            open_wrappers.append(indent)
            continue
        following = lines[index + 1] if index + 1 < len(lines) else ""
        had_children = len(following) - len(following.lstrip()) > indent
        kept.append((max(indent - BROWSER_SNAPSHOT_INDENT * len(open_wrappers), 0), text, had_children))

    out = []
    for index, (indent, text, had_children) in enumerate(kept):
        has_children = index + 1 < len(kept) and kept[index + 1][0] > indent
        if had_children and not has_children and text.endswith(":"):
            text = text[:-1]
        out.append(" " * indent + text)
    return "\n".join(out)


def refs_in(tree: str) -> frozenset[str]:
    """Every ref a snapshot carries."""
    return frozenset(_REF.findall(tree))


def check_target(ref: str | None, selector: str | None) -> None:
    """Raise ``ValueError`` unless exactly one of ``ref`` and ``selector`` is given."""
    if (ref is None) == (selector is None):
        raise ValueError(MSG_BROWSER_ONE_TARGET)


def check_read_format(format: str) -> None:
    """Raise ``ValueError`` for a format ``read`` does not return."""
    if format not in _READ_FORMATS:
        supported = " or ".join(repr(name) for name in _READ_FORMATS)
        raise ValueError(MSG_BROWSER_READ_FORMAT.format(supported=supported, requested=format))


def seconds_for(timeout: float | None) -> float:
    """The budget one action runs under: the caller's, or :data:`BROWSER_ACTION_TIMEOUT`."""
    if timeout is None:
        return BROWSER_ACTION_TIMEOUT
    # Playwright reads 0 as "wait forever", which nobody passing 0 means.
    if timeout <= 0:
        raise ValueError(MSG_BROWSER_BAD_TIMEOUT.format(timeout=timeout))
    return timeout


def truncate_tree(tree: str, max_chars: int | None) -> tuple[str, bool]:
    """``tree`` cut at the last line boundary within ``max_chars``, and whether anything was cut."""
    if max_chars is None:
        return tree, False
    if max_chars < 1:
        raise ValueError(MSG_BROWSER_BAD_MAX_CHARS.format(max_chars=max_chars))
    if len(tree) <= max_chars:
        return tree, False
    cut = tree.rfind("\n", 0, max_chars + 1)
    return (tree[:cut] if cut > 0 else ""), True


def _throw(failure: tuple[BrowserActionError, BaseException]) -> NoReturn:
    """Raise a translated error from its scrubbed cause.

    Called after the ``except`` block that built it has ended, never inside
    one: raised there, the original Playwright error would ride along on
    ``__context__``, CDP URL and all.
    """
    error, cause = failure
    raise error from cause


def _ms(seconds: float) -> float:
    return seconds * MS_PER_SECOND


def _sync_api() -> Callable[[], Any]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise ConfigurationError(MSG_BROWSER_ACTIONS_NOT_INSTALLED.format(extra=EXTRA_BROWSER)) from exc
    # Typed here rather than inferred, so the module checks the same with or without the extra installed.
    start: Callable[[], Any] = sync_playwright
    return start


def _async_api() -> Callable[[], Any]:
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise ConfigurationError(MSG_BROWSER_ACTIONS_NOT_INSTALLED.format(extra=EXTRA_BROWSER)) from exc
    start: Callable[[], Any] = async_playwright
    return start


def _running_loop() -> asyncio.AbstractEventLoop | None:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


def _is_timeout(exc: BaseException) -> bool:
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    except ImportError:
        return False
    return isinstance(exc, PlaywrightTimeoutError)


@dataclass
class _Driver:
    """One thread's sync Playwright, shared by every handle used on that thread."""

    playwright: Playwright
    loop: asyncio.AbstractEventLoop | None
    users: int = 0


# Playwright's sync API cannot be started twice on one thread -- the second
# start sees the first one's loop running and refuses -- so the handles on a
# thread share one, each with a connection of its own, and the last to close
# stops it.
_THREAD = threading.local()


def _thread_driver() -> _Driver | None:
    driver: _Driver | None = getattr(_THREAD, "driver", None)
    return driver


def _acquire_driver() -> _Driver:
    driver = _thread_driver()
    if driver is None:
        playwright: Playwright = _sync_api()().start()
        # Started, Playwright's own loop is what this thread now reports as running.
        driver = _Driver(playwright, _running_loop())
        _THREAD.driver = driver
    driver.users += 1
    return driver


def _release_driver(driver: _Driver) -> None:
    driver.users -= 1
    if driver.users > 0:
        return
    if _thread_driver() is driver:
        _THREAD.driver = None
    with contextlib.suppress(Exception):
        driver.playwright.stop()


# Strong references to cleanups a second cancellation stopped us waiting on,
# since the event loop keeps only a weak one to a running task.
_CLEANUPS: set[asyncio.Task[None]] = set()


async def _disconnect(playwright: AsyncPlaywright, browser: AsyncBrowser | None) -> None:
    try:
        if browser is not None:
            with contextlib.suppress(Exception):
                await browser.close()
    finally:
        with contextlib.suppress(Exception):
            await playwright.stop()


async def _disconnect_shielded(playwright: AsyncPlaywright, browser: AsyncBrowser | None) -> None:
    """:func:`_disconnect` as a task of its own, so a cancellation delivered again cannot cut it short.

    Awaiting it can still raise ``CancelledError``; the disconnect then carries on without us.
    """
    task = asyncio.ensure_future(_disconnect(playwright, browser))
    _CLEANUPS.add(task)
    task.add_done_callback(_CLEANUPS.discard)
    await asyncio.shield(task)


class _PageCore:
    """What both halves share: whose session it is, the live refs, and turning a Playwright error into ours."""

    def __init__(self, provider: str, session_id: str, cdp_url: str, *, closed: bool = False) -> None:
        self._provider = provider
        self._session_id = session_id
        self._cdp_url = cdp_url
        self._refs: frozenset[str] = frozenset()
        self._closed = closed

    def _on_navigated(self, frame: Frame | AsyncFrame) -> None:
        # A child frame navigating leaves the main frame's refs alone; the
        # engine itself stops resolving that frame's refs.
        if frame.parent_frame is None:
            self._refs = frozenset()

    def _error(self, kind: type[BrowserActionError], action: str, detail: str) -> BrowserActionError:
        return kind(self._provider, self._session_id, action, detail)

    def _released(self, action: str) -> BrowserActionError:
        return self._error(PageClosedError, action, MSG_BROWSER_HANDLE_RELEASED)

    def _in_snapshot(self, action: str, ref: str) -> None:
        if ref not in self._refs:
            raise self._error(StaleElementError, action, MSG_BROWSER_REF_NOT_IN_SNAPSHOT.format(ref=ref))

    def _detached(self, action: str, ref: str) -> BrowserActionError:
        return self._error(StaleElementError, action, MSG_BROWSER_REF_DETACHED.format(ref=ref))

    def _missing(self, action: str, ref: str | None, selector: str | None, seconds: float) -> BrowserActionError:
        if ref is not None:
            return self._detached(action, ref)
        return self._error(
            ElementNotFoundError, action, MSG_BROWSER_NOT_FOUND.format(selector=selector, timeout=seconds)
        )

    def _failure(
        self,
        action: str,
        exc: BaseException,
        seconds: float,
        *,
        closed: bool,
        url: str | None = None,
    ) -> tuple[BrowserActionError, BaseException]:
        """Our error for a Playwright one, and the cause to chain: that error, scrubbed of the CDP URL."""
        detail, cause = self._scrubbed(exc)
        if closed:
            error = self._error(PageClosedError, action, MSG_BROWSER_PAGE_CLOSED.format(error=detail))
        elif _is_timeout(exc):
            error = self._error(
                BrowserActionTimeoutError, action, MSG_BROWSER_ACTION_TIMEOUT.format(timeout=seconds, error=detail)
            )
        elif url is not None:
            error = self._error(NavigationError, action, MSG_BROWSER_NAVIGATION_FAILED.format(url=url, error=detail))
        else:
            error = self._error(BrowserActionError, action, detail)
        return error, cause

    def _connect_failure(self, action: str, exc: BaseException) -> tuple[BrowserActionError, BaseException]:
        detail, cause = self._scrubbed(exc)
        return self._error(BrowserActionError, action, MSG_BROWSER_CONNECT_FAILED.format(error=detail)), cause

    def _scrubbed(self, exc: BaseException) -> tuple[str, BaseException]:
        """The error's first line, and the error to chain -- a copy, when the original quotes the CDP URL.

        Playwright's connect errors quote the endpoint they tried, and several
        backends put the account key in it, so neither our message nor the
        cause a traceback prints may carry it.
        """
        clean = self._scrub(str(exc))
        first_line = clean.strip().splitlines()[0] if clean.strip() else type(exc).__name__
        if not self._mentions_secret(exc):
            return first_line, exc
        try:
            copy = type(exc)(clean)
        except Exception:
            copy = RuntimeError(clean)
        return first_line, copy

    def _scrub(self, text: str) -> str:
        for secret in (self._cdp_url, urlsplit(self._cdp_url).query):
            if secret:
                text = text.replace(secret, BROWSER_SECRET_PLACEHOLDER)
        return text

    def _mentions_secret(self, exc: BaseException) -> bool:
        """Whether the error, anything chained to it, or any of their arguments quotes the CDP URL."""
        seen: set[int] = set()
        pending: list[BaseException | None] = [exc]
        while pending:
            current = pending.pop()
            if current is None or id(current) in seen:
                continue
            seen.add(id(current))
            texts = [str(current), *(str(arg) for arg in current.args)]
            if any(self._scrub(text) != text for text in texts):
                return True
            pending.extend((current.__cause__, current.__context__))
        return False


class SyncPage(_PageCore):
    """The page actions of a :class:`~web_router.BrowserHandle`, over Playwright's sync API."""

    def __init__(self, provider: str, session_id: str, cdp_url: str, *, closed: bool = False) -> None:
        super().__init__(provider, session_id, cdp_url, closed=closed)
        self._driver: _Driver | None = None
        self._browser: Browser | None = None
        self._page: Page | None = None

    def goto(self, url: str, *, wait_until: PageLoadState | None, timeout: float | None) -> PageState:
        seconds = seconds_for(timeout)
        page = self._ready("goto")
        try:
            response = page.goto(url, wait_until=wait_until, timeout=_ms(seconds))
        except Exception as exc:
            failure = self._failure("goto", exc, seconds, closed=self._gone(page), url=url)
        else:
            return self._run("goto", seconds, lambda: self._state(page, response.status if response else None))
        _throw(failure)

    def snapshot(self, *, max_chars: int | None, timeout: float | None) -> PageSnapshot:
        truncate_tree("", max_chars)
        seconds = seconds_for(timeout)
        page = self._ready("snapshot")

        def take() -> PageSnapshot:
            full = compact_snapshot(page.aria_snapshot(mode=BROWSER_SNAPSHOT_MODE, timeout=_ms(seconds)))
            tree, truncated = truncate_tree(full, max_chars)
            self._refs = refs_in(tree)
            return PageSnapshot(url=page.url, title=page.title(), tree=tree, truncated=truncated)

        return self._run("snapshot", seconds, take)

    def click(self, *, ref: str | None, selector: str | None, timeout: float | None) -> PageState:
        check_target(ref, selector)
        seconds = seconds_for(timeout)
        page = self._ready("click")

        def act(locator: Locator) -> None:
            locator.click(timeout=_ms(seconds))

        return self._on_element("click", page, ref, selector, seconds, act)

    def type(
        self, text: str, *, ref: str | None, selector: str | None, submit: bool, timeout: float | None
    ) -> PageState:
        check_target(ref, selector)
        seconds = seconds_for(timeout)
        page = self._ready("type")

        def act(locator: Locator) -> None:
            locator.fill(text, timeout=_ms(seconds))
            if submit:
                locator.press(BROWSER_SUBMIT_KEY, timeout=_ms(seconds))

        return self._on_element("type", page, ref, selector, seconds, act)

    def press(self, key: str) -> PageState:
        page = self._ready("press")

        def act() -> PageState:
            page.keyboard.press(key)
            return self._state(page)

        return self._run("press", BROWSER_ACTION_TIMEOUT, act)

    def screenshot(self, *, full_page: bool, timeout: float | None) -> Screenshot:
        seconds = seconds_for(timeout)
        page = self._ready("screenshot")

        def take() -> Screenshot:
            png = as_png(page.screenshot(type=BROWSER_SCREENSHOT_TYPE, full_page=full_page, timeout=_ms(seconds)))
            width, height = png_size(png)
            return Screenshot(png=png, width=width, height=height, url=page.url)

        return self._run("screenshot", seconds, take)

    def read(self, *, format: PageFormat, timeout: float | None) -> ExtractedDocument:
        check_read_format(format)
        seconds = seconds_for(timeout)
        page = self._ready("read")

        def take() -> ExtractedDocument:
            if format == "html":
                content = page.locator(BROWSER_HTML_SELECTOR).evaluate(BROWSER_HTML_SCRIPT, timeout=_ms(seconds))
            else:
                content = page.locator(BROWSER_TEXT_SELECTOR).inner_text(timeout=_ms(seconds))
            return ExtractedDocument(url=page.url, title=page.title(), content=content, format=format)

        return self._run("read", seconds, take)

    def close(self) -> None:
        """Disconnect Playwright from the session. Never raises: the session is about to be released anyway."""
        self._closed = True
        driver, browser = self._driver, self._browser
        self._driver = self._browser = self._page = None
        if driver is None:
            return
        try:
            if browser is not None:
                with contextlib.suppress(Exception):
                    browser.close()
        finally:
            _release_driver(driver)

    def _ready(self, action: str) -> Page:
        if self._closed:
            raise self._released(action)
        # Playwright's sync API runs a loop of its own, which this thread
        # reports as running once it has started; any other loop is the caller's.
        running = _running_loop()
        driver = _thread_driver()
        if running is not None and (driver is None or running is not driver.loop):
            raise self._error(BrowserActionError, action, MSG_BROWSER_SYNC_IN_LOOP.format(action=action))
        if self._page is None:
            self._page = self._connect(action)
        return self._page

    def _connect(self, action: str) -> Page:
        """Connect this handle's own CDP client, on the thread's shared Playwright.

        The page acted on is the first page of the session's first context --
        the vendor's default, carrying its profile. A browser that offers no
        context gets a new one, which is incognito: none of that profile's
        cookies or storage are in it.
        """
        driver = _acquire_driver()
        browser: Browser | None = None
        try:
            browser = driver.playwright.chromium.connect_over_cdp(self._cdp_url, timeout=_ms(BROWSER_CONNECT_TIMEOUT))
            contexts = browser.contexts
            context = contexts[0] if contexts else browser.new_context()
            page = context.pages[0] if context.pages else context.new_page()
        except Exception as exc:
            # Closed here, not left to the driver: another handle on this
            # thread can keep the driver running, and this connection with it.
            if browser is not None:
                with contextlib.suppress(Exception):
                    browser.close()
            _release_driver(driver)
            failure = self._connect_failure(action, exc)
        else:
            if self._closed:
                # Released while connecting: close() found nothing to disconnect,
                # so this connection is ours to end -- as the async half does.
                with contextlib.suppress(Exception):
                    browser.close()
                _release_driver(driver)
                raise self._released(action)
            page.on(BROWSER_MAIN_FRAME_EVENT, self._on_navigated)
            self._driver, self._browser = driver, browser
            return page
        _throw(failure)

    def _run(self, action: str, seconds: float, call: Callable[[], _T]) -> _T:
        try:
            return call()
        except WebRouterError:
            raise
        except Exception as exc:
            failure = self._failure(action, exc, seconds, closed=self._gone(self._page))
        _throw(failure)

    def _on_element(
        self,
        action: str,
        page: Page,
        ref: str | None,
        selector: str | None,
        seconds: float,
        act: Callable[[Locator], None],
    ) -> PageState:
        def run() -> PageState:
            if ref is not None:
                self._in_snapshot(action, ref)
                locator = page.locator(BROWSER_ARIA_REF_SELECTOR.format(ref=ref))
                if locator.count() != 1:
                    raise self._detached(action, ref)
            else:
                locator = page.locator(str(selector))
            try:
                act(locator)
            except Exception as exc:
                if _is_timeout(exc) and self._count(locator) == 0:
                    raise self._missing(action, ref, selector, seconds) from exc
                raise
            return self._state(page)

        return self._run(action, seconds, run)

    def _count(self, locator: Locator) -> int:
        try:
            return int(locator.count())
        except Exception:
            return -1

    def _gone(self, page: Page | None) -> bool:
        try:
            return page is None or page.is_closed() or self._browser is None or not self._browser.is_connected()
        except Exception:
            return True

    def _state(self, page: Page, status_code: int | None = None) -> PageState:
        return PageState(url=page.url, title=page.title(), status_code=status_code)


class AsyncPage(_PageCore):
    """The page actions of an :class:`~web_router.AsyncBrowserHandle`, over Playwright's async API."""

    def __init__(self, provider: str, session_id: str, cdp_url: str, *, closed: bool = False) -> None:
        super().__init__(provider, session_id, cdp_url, closed=closed)
        self._playwright: AsyncPlaywright | None = None
        self._browser: AsyncBrowser | None = None
        self._page: AsyncPlaywrightPage | None = None
        self._connect_hook: Callable[[AsyncPlaywrightPage], Awaitable[None]] | None = None
        # Two actions awaited together on a fresh handle would otherwise
        # connect twice, and the loser's connection would leak.
        self._connecting = asyncio.Lock()

    def set_connect_hook(self, hook: Callable[[AsyncPlaywrightPage], Awaitable[None]]) -> None:
        """Run ``hook`` on the Playwright page once it connects, before the first action uses it.

        Raises:
            RuntimeError: if the page has already connected, since the hook would not run.
        """
        if self._page is not None:
            raise RuntimeError(MSG_BROWSER_HOOK_TOO_LATE)
        self._connect_hook = hook

    async def goto(self, url: str, *, wait_until: PageLoadState | None, timeout: float | None) -> PageState:
        seconds = seconds_for(timeout)
        page = await self._ready("goto")
        try:
            response = await page.goto(url, wait_until=wait_until, timeout=_ms(seconds))
        except Exception as exc:
            failure = self._failure("goto", exc, seconds, closed=self._gone(page), url=url)
        else:
            return await self._run("goto", seconds, lambda: self._state(page, response.status if response else None))
        _throw(failure)

    async def snapshot(self, *, max_chars: int | None, timeout: float | None) -> PageSnapshot:
        truncate_tree("", max_chars)
        seconds = seconds_for(timeout)
        page = await self._ready("snapshot")

        async def take() -> PageSnapshot:
            full = compact_snapshot(await page.aria_snapshot(mode=BROWSER_SNAPSHOT_MODE, timeout=_ms(seconds)))
            tree, truncated = truncate_tree(full, max_chars)
            self._refs = refs_in(tree)
            return PageSnapshot(url=page.url, title=await page.title(), tree=tree, truncated=truncated)

        return await self._run("snapshot", seconds, take)

    async def click(self, *, ref: str | None, selector: str | None, timeout: float | None) -> PageState:
        check_target(ref, selector)
        seconds = seconds_for(timeout)
        page = await self._ready("click")

        async def act(locator: AsyncLocator) -> None:
            await locator.click(timeout=_ms(seconds))

        return await self._on_element("click", page, ref, selector, seconds, act)

    async def type(
        self, text: str, *, ref: str | None, selector: str | None, submit: bool, timeout: float | None
    ) -> PageState:
        check_target(ref, selector)
        seconds = seconds_for(timeout)
        page = await self._ready("type")

        async def act(locator: AsyncLocator) -> None:
            await locator.fill(text, timeout=_ms(seconds))
            if submit:
                await locator.press(BROWSER_SUBMIT_KEY, timeout=_ms(seconds))

        return await self._on_element("type", page, ref, selector, seconds, act)

    async def press(self, key: str) -> PageState:
        page = await self._ready("press")

        async def act() -> PageState:
            await page.keyboard.press(key)
            return await self._state(page)

        return await self._run("press", BROWSER_ACTION_TIMEOUT, act)

    async def screenshot(self, *, full_page: bool, timeout: float | None) -> Screenshot:
        seconds = seconds_for(timeout)
        page = await self._ready("screenshot")

        async def take() -> Screenshot:
            image = await page.screenshot(type=BROWSER_SCREENSHOT_TYPE, full_page=full_page, timeout=_ms(seconds))
            # A conversion decodes the whole image, so it runs off the event loop.
            png = await asyncio.to_thread(as_png, image)
            width, height = png_size(png)
            return Screenshot(png=png, width=width, height=height, url=page.url)

        return await self._run("screenshot", seconds, take)

    async def read(self, *, format: PageFormat, timeout: float | None) -> ExtractedDocument:
        check_read_format(format)
        seconds = seconds_for(timeout)
        page = await self._ready("read")

        async def take() -> ExtractedDocument:
            if format == "html":
                content = await page.locator(BROWSER_HTML_SELECTOR).evaluate(BROWSER_HTML_SCRIPT, timeout=_ms(seconds))
            else:
                content = await page.locator(BROWSER_TEXT_SELECTOR).inner_text(timeout=_ms(seconds))
            return ExtractedDocument(url=page.url, title=await page.title(), content=content, format=format)

        return await self._run("read", seconds, take)

    async def close(self) -> None:
        """Disconnect Playwright from the session, as :meth:`SyncPage.close` does."""
        self._closed = True
        playwright, browser = self._playwright, self._browser
        self._playwright = self._browser = self._page = None
        if playwright is not None:
            # Shielded, as _connect's failure path is: the references were
            # already nulled above, so a cancellation cutting the teardown
            # short would leave a Playwright nothing can stop again.
            await _disconnect_shielded(playwright, browser)

    async def _ready(self, action: str) -> AsyncPlaywrightPage:
        if self._closed:
            raise self._released(action)
        async with self._connecting:
            if self._closed:
                raise self._released(action)
            if self._page is None:
                self._page = await self._connect(action)
            return self._page

    async def _connect(self, action: str) -> AsyncPlaywrightPage:
        """Connect this handle's CDP client on a Playwright of its own, picking the page as the sync half does."""
        playwright: AsyncPlaywright = await _async_api()().start()
        browser: AsyncBrowser | None = None
        try:
            browser = await playwright.chromium.connect_over_cdp(self._cdp_url, timeout=_ms(BROWSER_CONNECT_TIMEOUT))
            contexts = browser.contexts
            context = contexts[0] if contexts else await browser.new_context()
            page = context.pages[0] if context.pages else await context.new_page()
            if self._connect_hook is not None:
                await self._connect_hook(page)
        except BaseException as exc:
            # A cancellation too: nothing else holds this Playwright yet, so
            # skipping the cleanup would leave it running for good.
            try:
                await _disconnect_shielded(playwright, browser)
            except asyncio.CancelledError:
                if isinstance(exc, Exception):
                    raise
            if not isinstance(exc, Exception):
                raise
            failure = self._connect_failure(action, exc)
        else:
            if self._closed:
                # Released while connecting: close() found nothing to disconnect,
                # so this connection is ours to end.
                await _disconnect_shielded(playwright, browser)
                raise self._released(action)
            page.on(BROWSER_MAIN_FRAME_EVENT, self._on_navigated)
            self._playwright, self._browser = playwright, browser
            return page
        _throw(failure)

    async def _run(self, action: str, seconds: float, call: Callable[[], Awaitable[_T]]) -> _T:
        try:
            return await call()
        except WebRouterError:
            raise
        except Exception as exc:
            failure = self._failure(action, exc, seconds, closed=self._gone(self._page))
        _throw(failure)

    async def _on_element(
        self,
        action: str,
        page: AsyncPlaywrightPage,
        ref: str | None,
        selector: str | None,
        seconds: float,
        act: Callable[[AsyncLocator], Awaitable[None]],
    ) -> PageState:
        async def run() -> PageState:
            if ref is not None:
                self._in_snapshot(action, ref)
                locator = page.locator(BROWSER_ARIA_REF_SELECTOR.format(ref=ref))
                if await locator.count() != 1:
                    raise self._detached(action, ref)
            else:
                locator = page.locator(str(selector))
            try:
                await act(locator)
            except Exception as exc:
                if _is_timeout(exc) and await self._count(locator) == 0:
                    raise self._missing(action, ref, selector, seconds) from exc
                raise
            return await self._state(page)

        return await self._run(action, seconds, run)

    async def _count(self, locator: AsyncLocator) -> int:
        try:
            return int(await locator.count())
        except Exception:
            return -1

    def _gone(self, page: AsyncPlaywrightPage | None) -> bool:
        try:
            return page is None or page.is_closed() or self._browser is None or not self._browser.is_connected()
        except Exception:
            return True

    async def _state(self, page: AsyncPlaywrightPage, status_code: int | None = None) -> PageState:
        return PageState(url=page.url, title=await page.title(), status_code=status_code)
