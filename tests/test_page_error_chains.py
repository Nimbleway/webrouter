"""A translated page-action error must never carry the CDP URL on its exception chain."""

from __future__ import annotations

import asyncio
import sys
import types

import pytest

# Playwright is optional; the page layer only reads its TimeoutError here.
_sync_api = types.ModuleType("playwright.sync_api")


class TimeoutError(Exception):
    pass


_sync_api.TimeoutError = TimeoutError
_playwright = types.ModuleType("playwright")
_playwright.sync_api = _sync_api
sys.modules["playwright"] = _playwright
sys.modules["playwright.sync_api"] = _sync_api

from web_router.exceptions import ElementNotFoundError
from web_router.providers._page import AsyncPage, SyncPage

# Leave no trace: other tests probe for a real Playwright install.
sys.modules.pop("playwright", None)
sys.modules.pop("playwright.sync_api", None)


@pytest.fixture(autouse=True)
def fake_playwright(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "playwright", _playwright)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", _sync_api)


SECRET = "LEAKME_SUPERSECRET"
CDP = f"wss://browser.example/cdp?apiKey={SECRET}"


class Locator:
    def click(self, *, timeout: float) -> None:
        raise TimeoutError(f"Timeout: connect to {CDP}")

    def count(self) -> int:
        return 0


class AsyncLocator:
    async def click(self, *, timeout: float) -> None:
        raise TimeoutError(f"Timeout: connect to {CDP}")

    async def count(self) -> int:
        return 0


class Page:
    def locator(self, selector: str) -> Locator:
        return Locator()

    def is_closed(self) -> bool:
        return False


class AsyncPageFake:
    def locator(self, selector: str) -> AsyncLocator:
        return AsyncLocator()

    def is_closed(self) -> bool:
        return False


class Browser:
    def is_connected(self) -> bool:
        return True


def _texts(error: BaseException):
    while error is not None:
        yield str(error)
        error = error.__cause__ or error.__context__


def test_sync_missing_element_does_not_leak_the_cdp_url() -> None:
    page = SyncPage("steel", "session-42", CDP)
    page._page = Page()  # type: ignore[assignment]
    page._browser = Browser()  # type: ignore[assignment]
    try:
        page.click(ref=None, selector="#missing", timeout=1)
    except ElementNotFoundError as error:
        assert SECRET not in "\n".join(_texts(error))
    else:
        raise AssertionError("expected ElementNotFoundError")


def test_async_missing_element_does_not_leak_the_cdp_url() -> None:
    page = AsyncPage("steel", "session-42", CDP)
    page._page = AsyncPageFake()  # type: ignore[assignment]
    page._browser = Browser()  # type: ignore[assignment]

    async def click() -> None:
        await page.click(ref=None, selector="#missing", timeout=1)

    try:
        asyncio.run(click())
    except ElementNotFoundError as error:
        assert SECRET not in "\n".join(_texts(error))
    else:
        raise AssertionError("expected ElementNotFoundError")
