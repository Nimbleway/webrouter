"""The ``browser_*`` tools: a session table the server holds, and an id the model holds.

The unit tests drive the real handlers and the real :class:`AsyncBrowserHandle`
over the in-memory ``FakeBrowserProvider``, with the handle's Playwright page
swapped for :class:`FakePage`, so nothing launches and nothing leaves the
process. What they pin: the model only ever sees an id this server minted, no
output of any tool -- success, failure or ``fetch_raw`` -- carries the CDP URL
or the backend's session id, idle and leftover sessions are released, and the
cap holds.

The ``browser``-marked tests at the bottom run the same tools against the real
``local`` provider and the ``file://`` fixture pages, then kill a real server
subprocess and check no browser is left behind. Run them with ``make test-browser``.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import http.server
import importlib.util
import json
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, get_args

import anyio
import pytest

from tests.conftest import FAKE_CDP_URL, FakeBrowserProvider
from tests.mcp.test_navigation import FakePlaywrightPage
from web_router.client import AsyncWebRouter
from web_router.constants import (
    BROWSER_TIMEOUT,
    ENV_BRAVE_API_KEY,
    ENV_MCP_BROWSER_ALLOW_FILE_URLS,
    LOCAL_BROWSER_TEMP_PREFIX,
    MCP_BROWSER_ACTION_TIMEOUT_LIMIT,
    MCP_BROWSER_BLANK_URL,
    MCP_BROWSER_IDLE_TIMEOUT,
    MCP_BROWSER_MAX_SESSIONS,
    MCP_BROWSER_SESSION_TIMEOUT_LIMIT,
    MCP_BROWSER_SNAPSHOT_MAX_CHARS,
    MCP_BROWSER_SNAPSHOT_MAX_CHARS_LIMIT,
    MCP_BROWSER_TOOLS,
    MCP_MIME_PNG,
    MCP_REDACTED,
    MCP_TOOL_BROWSER_CLICK,
    MCP_TOOL_BROWSER_CLOSE,
    MCP_TOOL_BROWSER_GOTO,
    MCP_TOOL_BROWSER_OPEN,
    MCP_TOOL_BROWSER_PRESS,
    MCP_TOOL_BROWSER_READ,
    MCP_TOOL_BROWSER_SCREENSHOT,
    MCP_TOOL_BROWSER_SNAPSHOT,
    MCP_TOOL_BROWSER_TYPE,
    MCP_TOOL_FETCH_RAW,
    MCP_TOOL_LOAD_ENV,
    PROVIDER_LOCAL,
)
from web_router.exceptions import (
    BrowserActionError,
    BrowserActionTimeoutError,
    ElementNotFoundError,
    NavigationError,
    PageClosedError,
    StaleElementError,
)
from web_router.mcp import browsers as browsers_module
from web_router.mcp import server as server_module
from web_router.mcp import tools as tools_module
from web_router.mcp.browsers import BrowserSessions, BrowserToolError
from web_router.mcp.server import call_tool, list_tools, serve
from web_router.mcp.tools import WebRouterTools
from web_router.providers.base import AsyncBrowserHandle
from web_router.registry import register_browser_provider, unregister_browser_provider
from web_router.types import (
    BrowserRequest,
    ExtractedDocument,
    PageSnapshot,
    PageState,
    Screenshot,
    StealthLevel,
)

SECRET = "SEKRIT-cdp-0123456789"
"""The account key the fake provider's CDP URL carries."""

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16

TREE = '- heading "Fake page" [level=1] [ref=e1]\n- textbox "Search" [ref=e2]\n- button "Go" [ref=e3]'


class FakePage:
    """Stands in for the handle's Playwright page: records each action, answers or fails on cue.

    Its answers quote the session's CDP URL and the backend's id wherever a
    careless backend or page might -- in a URL, in page text, in a tree, in an
    error -- which is what the redaction tests lean on.
    """

    def __init__(self, handle: AsyncBrowserHandle) -> None:
        self.cdp_url = handle.cdp_url
        self.vendor_id = handle.session_id
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail: dict[str, BaseException] = {}
        self.truncated = False
        self.closed = False
        self.gate: anyio.Event | None = None
        self.connect_hook: Any = None

    def set_connect_hook(self, hook: Any) -> None:
        self.connect_hook = hook

    async def _act(self, name: str, **kwargs: Any) -> None:
        self.calls.append((name, kwargs))
        if self.gate is not None:
            await self.gate.wait()
        failure = self.fail.get(name)
        if failure is not None:
            raise failure

    def _state(self) -> PageState:
        return PageState(url=f"https://example.test/?next={self.cdp_url}", title=f"Page for {self.vendor_id}")

    async def goto(self, url: str, **kwargs: Any) -> PageState:
        await self._act("goto", url=url, **kwargs)
        return PageState(url=url, title="Fake page", status_code=200)

    async def snapshot(self, **kwargs: Any) -> PageSnapshot:
        await self._act("snapshot", **kwargs)
        return PageSnapshot(
            url="https://example.test/",
            title="Fake page",
            tree=f'{TREE}\n- link "debug {self.cdp_url}" [ref=e4]',
            truncated=self.truncated,
        )

    async def click(self, **kwargs: Any) -> PageState:
        await self._act("click", **kwargs)
        return self._state()

    async def type(self, text: str, **kwargs: Any) -> PageState:
        await self._act("type", text=text, **kwargs)
        return self._state()

    async def press(self, key: str) -> PageState:
        await self._act("press", key=key)
        return self._state()

    async def screenshot(self, **kwargs: Any) -> Screenshot:
        await self._act("screenshot", **kwargs)
        return Screenshot(png=PNG, width=1280, height=720, url=self.cdp_url)

    async def read(self, **kwargs: Any) -> ExtractedDocument:
        await self._act("read", **kwargs)
        return ExtractedDocument(
            url="https://example.test/",
            title="Fake page",
            content=f"Connect with {self.cdp_url} as session {self.vendor_id}. The rest is ordinary text.",
            format=kwargs.get("format", "text"),
        )

    async def close(self) -> None:
        self.closed = True


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def fake_pages(monkeypatch: pytest.MonkeyPatch) -> dict[str, FakePage]:
    """Every handle's page is a :class:`FakePage`, keyed by the backend's session id."""
    pages: dict[str, FakePage] = {}

    def actions(handle: AsyncBrowserHandle) -> FakePage:
        if handle._page is None:
            page = FakePage(handle)
            pages[handle.session_id] = page
            handle._page = page  # type: ignore[assignment]
        return handle._page  # type: ignore[return-value]

    monkeypatch.setattr(AsyncBrowserHandle, "_actions", actions)
    return pages


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def session(fake_browser_provider: Any, fake_pages: dict[str, FakePage], clock: Clock) -> WebRouterTools:
    return WebRouterTools(AsyncWebRouter("fake"), browsers=BrowserSessions(clock=clock))


def _provider(tools: WebRouterTools) -> FakeBrowserProvider:
    provider = tools.router.get_browser_provider("fake")
    assert isinstance(provider, FakeBrowserProvider)
    return provider


def _texts(result: Any) -> list[str]:
    return [block.text for block in result.content if getattr(block, "text", None) is not None]


def _ok(result: Any) -> dict[str, Any]:
    assert not result.is_error, result.content[0].text
    payload: dict[str, Any] = json.loads(result.content[0].text)
    return payload


def _error(result: Any) -> str:
    assert result.is_error, result.content
    text: str = result.content[0].text
    return text


async def _open(tools: WebRouterTools, **arguments: Any) -> str:
    session_id: str = _ok(await call_tool(tools, MCP_TOOL_BROWSER_OPEN, arguments))["session_id"]
    return session_id


def _page(tools: WebRouterTools, pages: dict[str, FakePage], session_id: str) -> FakePage:
    return pages[tools.browsers._sessions[session_id].handle.session_id]


async def test_the_model_gets_a_minted_id_and_never_the_backends(session: WebRouterTools) -> None:
    first = await _open(session)
    second = await _open(session)

    assert first.startswith("browser-") and second.startswith("browser-") and first != second
    assert _provider(session).requests and "session-1" not in first + second
    payload = _ok(await call_tool(session, MCP_TOOL_BROWSER_CLOSE, {"session_id": first}))
    assert payload == {"session_id": first, "state": "released"}
    assert _provider(session).released == ["session-1"]


async def test_no_tool_output_ever_carries_the_cdp_url_or_the_backend_session_id(
    session: WebRouterTools, fake_pages: dict[str, FakePage], monkeypatch: pytest.MonkeyPatch
) -> None:
    outputs: list[str] = []

    async def record(name: str, arguments: dict[str, Any]) -> Any:
        result = await call_tool(session, name, arguments)
        outputs.extend(_texts(result))
        return result

    opened = await record(MCP_TOOL_BROWSER_OPEN, {})
    session_id = json.loads(opened.content[0].text)["session_id"]
    target = {"session_id": session_id}
    await record(MCP_TOOL_BROWSER_GOTO, {**target, "url": "https://example.test/"})
    page = _page(session, fake_pages, session_id)
    await record(MCP_TOOL_BROWSER_SNAPSHOT, target)
    await record(MCP_TOOL_BROWSER_CLICK, {**target, "ref": "e3"})
    await record(MCP_TOOL_BROWSER_TYPE, {**target, "text": "hi", "selector": "input"})
    await record(MCP_TOOL_BROWSER_PRESS, {**target, "key": "Enter"})
    await record(MCP_TOOL_BROWSER_SCREENSHOT, target)
    read = await record(MCP_TOOL_BROWSER_READ, target)
    await record(MCP_TOOL_FETCH_RAW, {"handle": json.loads(read.content[0].text)["handle"]})
    # A failure whose detail quotes both -- as a backend's own error message can.
    page.fail["click"] = BrowserActionError("fake", page.vendor_id, "click", f"refused by {page.cdp_url}")
    await record(MCP_TOOL_BROWSER_CLICK, {**target, "ref": "e3"})
    page.fail["goto"] = NavigationError("fake", page.vendor_id, "goto", f"{page.vendor_id} at {page.cdp_url}")
    await record(MCP_TOOL_BROWSER_GOTO, {**target, "url": "https://example.test/"})
    await record(MCP_TOOL_BROWSER_CLOSE, target)
    # The refusals that list what is open, and one about an id the model made up.
    opened = [await record(MCP_TOOL_BROWSER_OPEN, {}) for _ in range(MCP_BROWSER_MAX_SESSIONS)]
    await record(MCP_TOOL_BROWSER_OPEN, {})
    await record(MCP_TOOL_BROWSER_SNAPSHOT, {"session_id": "browser-made-up"})
    for result in opened:
        await record(MCP_TOOL_BROWSER_CLOSE, {"session_id": json.loads(result.content[0].text)["session_id"]})

    # An open that fails with the backend's own message quoting the connect URL.
    async def failing_open(self: FakeBrowserProvider, request: Any) -> Any:
        raise RuntimeError(f"could not start: {FAKE_CDP_URL.format(session_id='session-9')}")

    monkeypatch.setattr(FakeBrowserProvider, "_aopen", failing_open)
    failed = await record(MCP_TOOL_BROWSER_OPEN, {})

    transcript = "\n".join(outputs)
    assert failed.is_error and "could not start" in failed.content[0].text
    assert "already open" in transcript and "Unknown session_id" in transcript
    assert len(outputs) >= 12
    assert SECRET not in transcript
    assert page.cdp_url not in transcript
    assert page.vendor_id not in transcript and "session-9" not in transcript
    assert "wss://" not in transcript
    assert MCP_REDACTED in transcript


async def test_a_control_url_the_backend_returned_on_open_never_reaches_the_model(
    fake_browser_provider: Any, fake_pages: dict[str, FakePage], clock: Clock
) -> None:
    # Steel's shape: nothing in it reads as a session URL, so only the literal match catches it.
    control_url = "https://api.steel.dev/v1/sessions/control-token-0123456789/player"
    router = AsyncWebRouter("fake", provider_config={"fake": {"control_url": control_url}})
    session = WebRouterTools(router, browsers=BrowserSessions(clock=clock))
    opened = await call_tool(session, MCP_TOOL_BROWSER_OPEN, {})
    target = {"session_id": json.loads(opened.content[0].text)["session_id"]}
    page = _page(session, fake_pages, target["session_id"])
    page.fail["goto"] = NavigationError("fake", page.vendor_id, "goto", f"redirected to {control_url}")

    failed = await call_tool(session, MCP_TOOL_BROWSER_GOTO, {**target, "url": "https://example.test/"})

    transcript = "\n".join(_texts(opened) + _texts(failed))
    assert "control-token" not in transcript
    assert MCP_REDACTED in transcript


async def test_open_offers_no_way_to_pass_browser_switches(session: WebRouterTools) -> None:
    tool = next(tool for tool in list_tools(session).tools if tool.name == MCP_TOOL_BROWSER_OPEN)
    properties = tool.input_schema["properties"]

    assert {"provider_options", "profile", "persist_profile"}.isdisjoint(properties)
    assert {"headless", "timeout", "provider"} <= set(properties)
    assert "provider_options" in (tool.description or "")
    assert "BILLS" in (tool.description or "")

    message = _error(
        await call_tool(session, MCP_TOOL_BROWSER_OPEN, {"provider_options": {"remote-debugging-address": "0.0.0.0"}})
    )
    assert "does not accept provider_options" in message
    assert _provider(session).requests == []


async def test_stealth_is_offered_as_its_levels_and_reaches_the_router_as_one(session: WebRouterTools) -> None:
    tool = next(tool for tool in list_tools(session).tools if tool.name == MCP_TOOL_BROWSER_OPEN)
    assert tool.input_schema["properties"]["stealth"]["enum"] == list(get_args(StealthLevel))

    await _open(session, stealth="high")

    assert _provider(session).requests[0].stealth == "high"


async def test_open_passes_the_models_options_through(session: WebRouterTools) -> None:
    await _open(session, headless=True, timeout=120, viewport={"width": 800, "height": 600})

    request = _provider(session).requests[0]
    assert (request.headless, request.timeout) == (True, 120)
    assert request.viewport is not None and (request.viewport.width, request.viewport.height) == (800, 600)
    assert request.provider_options == {}


async def test_every_open_sends_a_bounded_lifetime(session: WebRouterTools) -> None:
    await _open(session)
    opened = _ok(
        await call_tool(
            session, MCP_TOOL_BROWSER_OPEN, {"timeout": 10 * BROWSER_TIMEOUT, "idle_timeout": 10 * BROWSER_TIMEOUT}
        )
    )

    unset, excessive = _provider(session).requests
    assert unset.timeout == MCP_BROWSER_SESSION_TIMEOUT_LIMIT == BROWSER_TIMEOUT
    assert (excessive.timeout, excessive.idle_timeout) == (BROWSER_TIMEOUT, BROWSER_TIMEOUT)
    assert "`timeout` 6000 was lowered to 600" in opened["note"]
    assert "`idle_timeout` 6000 was lowered to 600" in opened["note"]


class LifelessBrowserProvider(FakeBrowserProvider):
    """A custom backend that maps no lifetime, as a third-party one may not."""

    name = "lifeless"
    supported_parameters = frozenset({"headless"})


async def test_a_backend_that_maps_no_lifetime_is_not_sent_one(fake_pages: dict[str, FakePage]) -> None:
    register_browser_provider("lifeless", LifelessBrowserProvider)
    try:
        tools = WebRouterTools(AsyncWebRouter("lifeless"))
        opened = await call_tool(tools, MCP_TOOL_BROWSER_OPEN, {})
        asked = await call_tool(tools, MCP_TOOL_BROWSER_OPEN, {"timeout": 60})
        provider = tools.router.get_browser_provider("lifeless")
        assert isinstance(provider, LifelessBrowserProvider)

        assert not opened.is_error, opened.content[0].text
        assert provider.requests[0].timeout is None
        # Asked for explicitly, it is still refused rather than dropped.
        assert asked.is_error and "timeout" in asked.content[0].text
        await tools.aclose()
    finally:
        unregister_browser_provider("lifeless")


async def test_action_timeouts_and_snapshot_size_are_clamped(
    session: WebRouterTools, fake_pages: dict[str, FakePage]
) -> None:
    session_id = await _open(session)
    target = {"session_id": session_id}
    goto_result = _ok(
        await call_tool(session, MCP_TOOL_BROWSER_GOTO, {**target, "url": "https://a.test/", "timeout": 86_400})
    )
    snapshot_result = await call_tool(session, MCP_TOOL_BROWSER_SNAPSHOT, {**target, "max_chars": 10**9, "timeout": 5})
    await call_tool(session, MCP_TOOL_BROWSER_CLICK, {**target, "ref": "e3", "timeout": 1e9})
    exact = _ok(await call_tool(session, MCP_TOOL_BROWSER_PRESS, {**target, "key": "Enter"}))

    goto, snapshot, click, _ = _page(session, fake_pages, session_id).calls
    assert "`timeout` 86400 was lowered to 120" in goto_result["note"]
    assert f"`max_chars` 1e+09 was lowered to {MCP_BROWSER_SNAPSHOT_MAX_CHARS_LIMIT}" in _ok(snapshot_result)["note"]
    assert "lowered" not in exact.get("note", "")
    assert goto[1]["timeout"] == MCP_BROWSER_ACTION_TIMEOUT_LIMIT
    assert (snapshot[1]["max_chars"], snapshot[1]["timeout"]) == (MCP_BROWSER_SNAPSHOT_MAX_CHARS_LIMIT, 5)
    assert click[1]["timeout"] == MCP_BROWSER_ACTION_TIMEOUT_LIMIT


def test_the_schemas_advertise_the_limits(session: WebRouterTools) -> None:
    schemas = {tool.name: tool.input_schema["properties"] for tool in list_tools(session).tools}

    assert schemas[MCP_TOOL_BROWSER_OPEN]["timeout"]["maximum"] == MCP_BROWSER_SESSION_TIMEOUT_LIMIT
    assert schemas[MCP_TOOL_BROWSER_OPEN]["idle_timeout"]["maximum"] == MCP_BROWSER_SESSION_TIMEOUT_LIMIT
    assert schemas[MCP_TOOL_BROWSER_SNAPSHOT]["max_chars"]["maximum"] == MCP_BROWSER_SNAPSHOT_MAX_CHARS_LIMIT
    for name in (MCP_TOOL_BROWSER_GOTO, MCP_TOOL_BROWSER_SNAPSHOT, MCP_TOOL_BROWSER_CLICK, MCP_TOOL_BROWSER_READ):
        assert schemas[name]["timeout"]["maximum"] == MCP_BROWSER_ACTION_TIMEOUT_LIMIT


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "chrome://settings",
        "javascript:alert(1)",
        "data:text/html,hi",
        "http://169.254.169.254/latest/meta-data/",
        "http://metadata.google.internal/",
    ],
)
async def test_goto_refuses_a_forbidden_url_before_the_page_is_touched(
    session: WebRouterTools, fake_pages: dict[str, FakePage], monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    monkeypatch.delenv(ENV_MCP_BROWSER_ALLOW_FILE_URLS, raising=False)
    session_id = await _open(session)

    message = _error(await call_tool(session, MCP_TOOL_BROWSER_GOTO, {"session_id": session_id, "url": url}))

    assert "browser_goto refused" in message and "Nothing was loaded" in message
    assert all(not page.calls for page in fake_pages.values())
    assert session_id in session.browsers.session_ids()


async def test_an_action_that_lands_somewhere_forbidden_is_refused_and_sent_back(
    session: WebRouterTools, fake_pages: dict[str, FakePage]
) -> None:
    session_id = await _open(session)
    page = _page(session, fake_pages, session_id)
    playwright_page = FakePlaywrightPage()
    await page.connect_hook(playwright_page)
    # The click's link led to a local file; the fake page reports success, as Chrome would.
    playwright_page.url = "file:///Users/someone/.ssh/id_rsa"

    message = _error(await call_tool(session, MCP_TOOL_BROWSER_CLICK, {"session_id": session_id, "ref": "e3"}))
    after = _ok(await call_tool(session, MCP_TOOL_BROWSER_PRESS, {"session_id": session_id, "key": "Enter"}))

    assert "browser_click refused" in message and "sent back to about:blank" in message
    assert playwright_page.visited == [MCP_BROWSER_BLANK_URL]
    assert "id_rsa" not in message and after["session_id"] == session_id


async def test_a_navigation_the_route_aborted_reads_as_a_refusal_not_a_failure(
    session: WebRouterTools, fake_pages: dict[str, FakePage]
) -> None:
    from tests.mcp.test_navigation import FakeRequest, FakeRoute

    session_id = await _open(session)
    page = _page(session, fake_pages, session_id)
    playwright_page = FakePlaywrightPage()
    await page.connect_hook(playwright_page)
    await playwright_page.context.routes["http"](FakeRoute(FakeRequest("http://169.254.169.254/")))
    playwright_page.url = "chrome-error://chromewebdata/"
    page.fail["goto"] = NavigationError("fake", page.vendor_id, "goto", "net::ERR_BLOCKED_BY_CLIENT")

    message = _error(
        await call_tool(session, MCP_TOOL_BROWSER_GOTO, {"session_id": session_id, "url": "https://redirects.test/"})
    )

    assert "browser_goto refused" in message and "cloud metadata endpoint" in message
    assert "ERR_BLOCKED_BY_CLIENT" not in message


async def test_a_cancelled_open_ends_with_the_session_released(
    session: WebRouterTools, monkeypatch: pytest.MonkeyPatch
) -> None:
    gate = anyio.Event()
    started = anyio.Event()
    original = FakeBrowserProvider._aopen

    async def slow_open(self: FakeBrowserProvider, request: Any) -> Any:
        started.set()
        await gate.wait()
        return await original(self, request)

    monkeypatch.setattr(FakeBrowserProvider, "_aopen", slow_open)

    with anyio.fail_after(5):
        async with anyio.create_task_group() as group:
            group.start_soon(call_tool, session, MCP_TOOL_BROWSER_OPEN, {})
            await started.wait()
            # The client gives up on the call while the backend is still starting the session.
            group.cancel_scope.cancel()
            gate.set()

    assert _provider(session).released == ["session-1"]
    assert len(session.browsers) == 0


async def test_an_open_whose_session_cannot_be_read_releases_it(
    session: WebRouterTools, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unparseable(cdp_url: str) -> Any:
        raise ValueError("Invalid IPv6 URL")

    monkeypatch.setattr(browsers_module.UrlPolicy, "for_session", unparseable)

    with pytest.raises(ValueError, match="IPv6"):
        await session.browsers.open(lambda: _provider(session).aopen(BrowserRequest()))

    assert _provider(session).released == ["session-1"]
    assert len(session.browsers) == 0


async def test_an_open_the_backend_never_answers_gives_up_and_frees_its_slot(
    fake_browser_provider: Any, fake_pages: dict[str, FakePage]
) -> None:
    sessions = BrowserSessions(max_sessions=1)

    async def never() -> Any:
        await anyio.sleep_forever()

    with pytest.raises(BrowserToolError, match=r"gave up after 0\.05s"):
        await sessions.open(never, timeout=0.05)
    assert len(sessions) == 0 and sessions._opening == 0


async def test_without_playwright_the_tools_are_hidden_and_open_refuses_before_billing(
    session: WebRouterTools, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(browsers_module, "playwright_installed", lambda: False)
    monkeypatch.setattr(tools_module, "playwright_installed", lambda: False)

    message = _error(await call_tool(session, MCP_TOOL_BROWSER_OPEN, {}))
    names = {tool.name for tool in list_tools(session).tools}

    assert "uv sync --extra mcp --extra browser" in message
    assert _provider(session).requests == []
    assert names.isdisjoint(MCP_BROWSER_TOOLS)
    assert {"search", "extract", "browse"} <= names


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ({}, "exactly one of `ref` or `selector`, not neither"),
        ({"ref": "e1", "selector": "a"}, "not ref and selector"),
        ({"ref": "[ref=e1]"}, "is not a ref"),
        ({"ref": "E 1"}, "is not a ref"),
        ({"selector": "   "}, "`selector` is empty"),
    ],
)
async def test_a_bad_target_is_refused_before_the_page_is_touched(
    session: WebRouterTools, fake_pages: dict[str, FakePage], arguments: dict[str, Any], expected: str
) -> None:
    session_id = await _open(session)

    for tool, extra in ((MCP_TOOL_BROWSER_CLICK, {}), (MCP_TOOL_BROWSER_TYPE, {"text": "x"})):
        message = _error(await call_tool(session, tool, {"session_id": session_id, **extra, **arguments}))
        assert expected in message
    assert all(not page.calls for page in fake_pages.values())


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (StaleElementError, "Call browser_snapshot for a fresh tree"),
        (ElementNotFoundError, "Check the selector"),
        (NavigationError, "The session is still open"),
        (BrowserActionTimeoutError, "pass a larger `timeout`"),
        (BrowserActionError, "browser_click failed: boom"),
    ],
)
async def test_each_action_failure_reads_as_what_the_model_should_do_next(
    session: WebRouterTools, fake_pages: dict[str, FakePage], error: type[BrowserActionError], expected: str
) -> None:
    session_id = await _open(session)
    await call_tool(session, MCP_TOOL_BROWSER_SNAPSHOT, {"session_id": session_id})
    page = _page(session, fake_pages, session_id)
    page.fail["click"] = error("fake", page.vendor_id, "click", "boom")

    message = _error(await call_tool(session, MCP_TOOL_BROWSER_CLICK, {"session_id": session_id, "ref": "e3"}))

    assert expected in message
    assert page.vendor_id not in message
    assert session_id in session.browsers.session_ids()


async def test_a_lost_page_releases_the_session_and_says_so(
    session: WebRouterTools, fake_pages: dict[str, FakePage]
) -> None:
    session_id = await _open(session)
    await call_tool(session, MCP_TOOL_BROWSER_GOTO, {"session_id": session_id, "url": "https://example.test/"})
    page = _page(session, fake_pages, session_id)
    page.fail["snapshot"] = PageClosedError("fake", page.vendor_id, "snapshot", "the page is gone")

    message = _error(await call_tool(session, MCP_TOOL_BROWSER_SNAPSHOT, {"session_id": session_id}))
    later = _error(await call_tool(session, MCP_TOOL_BROWSER_SNAPSHOT, {"session_id": session_id}))

    assert "call browser_open for a new one" in message
    assert _provider(session).released == [page.vendor_id] and page.closed
    assert "has ended" in later and "connection to it was lost" in later


async def test_snapshot_is_capped_by_default_and_says_when_it_was_cut(
    session: WebRouterTools, fake_pages: dict[str, FakePage]
) -> None:
    session_id = await _open(session)
    await call_tool(session, MCP_TOOL_BROWSER_SNAPSHOT, {"session_id": session_id})
    page = _page(session, fake_pages, session_id)
    page.truncated = True

    result = await call_tool(session, MCP_TOOL_BROWSER_SNAPSHOT, {"session_id": session_id, "max_chars": 500})
    payload = _ok(result)

    assert page.calls[0] == ("snapshot", {"max_chars": MCP_BROWSER_SNAPSHOT_MAX_CHARS, "timeout": None})
    assert page.calls[1][1]["max_chars"] == 500
    assert payload["truncated"] is True and "cut at 500 characters" in payload["note"]
    assert result.content[1].text.startswith('- heading "Fake page" [level=1] [ref=e1]')


async def test_a_screenshot_comes_back_as_image_content(session: WebRouterTools) -> None:
    session_id = await _open(session)

    result = await call_tool(session, MCP_TOOL_BROWSER_SCREENSHOT, {"session_id": session_id, "full_page": True})

    payload = _ok(result)
    image = result.content[1]
    assert (image.type, image.mime_type) == ("image", MCP_MIME_PNG)
    assert base64.b64decode(image.data) == PNG
    assert (payload["width"], payload["height"]) == (1280, 720)


async def test_read_is_shaped_like_extract_and_its_raw_payload_is_scrubbed(session: WebRouterTools) -> None:
    session_id = await _open(session)

    payload = _ok(await call_tool(session, MCP_TOOL_BROWSER_READ, {"session_id": session_id, "format": "html"}))
    raw = _ok(await call_tool(session, MCP_TOOL_FETCH_RAW, {"handle": payload["handle"]}))

    assert payload["documents"][0]["format"] == "html"
    assert payload["handle"].startswith(MCP_TOOL_BROWSER_READ)
    assert "The rest is ordinary text." in raw["raw"]["content"]
    assert SECRET not in json.dumps(raw) and "session-1" not in json.dumps(raw)


async def test_actions_reach_the_handle_with_the_models_arguments(
    session: WebRouterTools, fake_pages: dict[str, FakePage]
) -> None:
    session_id = await _open(session)
    target = {"session_id": session_id}
    await call_tool(session, MCP_TOOL_BROWSER_GOTO, {**target, "url": "https://a.test/", "wait_until": "commit"})
    await call_tool(session, MCP_TOOL_BROWSER_TYPE, {**target, "text": "q", "ref": "e2", "submit": True})
    await call_tool(session, MCP_TOOL_BROWSER_PRESS, {**target, "key": "Escape"})

    assert _page(session, fake_pages, session_id).calls == [
        ("goto", {"url": "https://a.test/", "wait_until": "commit", "timeout": None}),
        ("type", {"text": "q", "ref": "e2", "selector": None, "submit": True, "timeout": None}),
        ("press", {"key": "Escape"}),
    ]


async def test_an_unknown_id_is_refused_and_close_is_idempotent(session: WebRouterTools) -> None:
    unknown = _error(await call_tool(session, MCP_TOOL_BROWSER_GOTO, {"session_id": "session-1", "url": "x"}))
    session_id = await _open(session)
    await call_tool(session, MCP_TOOL_BROWSER_CLOSE, {"session_id": session_id})
    again = _ok(await call_tool(session, MCP_TOOL_BROWSER_CLOSE, {"session_id": session_id}))
    after = _error(await call_tool(session, MCP_TOOL_BROWSER_GOTO, {"session_id": session_id, "url": "x"}))

    assert "Unknown session_id 'session-1'" in unknown
    assert again["state"] == "released" and "closed with browser_close" in again["note"]
    assert "has ended" in after
    assert _provider(session).released == ["session-1"]


async def test_one_session_too_many_is_refused_by_name(session: WebRouterTools) -> None:
    opened = [await _open(session) for _ in range(MCP_BROWSER_MAX_SESSIONS)]

    message = _error(await call_tool(session, MCP_TOOL_BROWSER_OPEN, {}))

    assert f"{MCP_BROWSER_MAX_SESSIONS} browser sessions are already open" in message
    assert all(session_id in message for session_id in opened)
    assert len(_provider(session).requests) == MCP_BROWSER_MAX_SESSIONS
    await call_tool(session, MCP_TOOL_BROWSER_CLOSE, {"session_id": opened[0]})
    await _open(session)


async def test_an_idle_session_is_reaped_and_a_busy_one_is_not(
    session: WebRouterTools, fake_pages: dict[str, FakePage], clock: Clock
) -> None:
    idle = await _open(session)
    busy = await _open(session)
    await call_tool(session, MCP_TOOL_BROWSER_GOTO, {"session_id": busy, "url": "https://a.test/"})
    page = _page(session, fake_pages, busy)
    page.gate = anyio.Event()

    async with anyio.create_task_group() as group:
        group.start_soon(call_tool, session, MCP_TOOL_BROWSER_SNAPSHOT, {"session_id": busy})
        await anyio.wait_all_tasks_blocked()
        clock.now += MCP_BROWSER_IDLE_TIMEOUT
        reaped = await session.browsers.reap()
        page.gate.set()

    message = _error(await call_tool(session, MCP_TOOL_BROWSER_SNAPSHOT, {"session_id": idle}))
    assert reaped == [idle]
    assert _provider(session).released == ["session-1"]
    assert f"released after {MCP_BROWSER_IDLE_TIMEOUT:g}s without a call" in message
    assert session.browsers.session_ids() == [busy]


async def test_the_reaper_sweeps_on_its_own(fake_browser_provider: Any, fake_pages: dict[str, FakePage]) -> None:
    clock = Clock()
    tools = WebRouterTools(AsyncWebRouter("fake"), browsers=BrowserSessions(clock=clock, reap_interval=0.01))
    await _open(tools)
    clock.now += MCP_BROWSER_IDLE_TIMEOUT

    with anyio.move_on_after(0.5):
        async with anyio.create_task_group() as group:
            group.start_soon(tools.browsers.run_reaper)
            while len(tools.browsers):
                await anyio.sleep(0.01)
            group.cancel_scope.cancel()

    assert _provider(tools).released == ["session-1"]


async def test_shutdown_releases_every_session_and_closes_the_router(session: WebRouterTools) -> None:
    for _ in range(MCP_BROWSER_MAX_SESSIONS):
        await _open(session)
    provider = _provider(session)
    provider.already_over.add("session-2")

    await session.aclose()

    assert sorted(provider.released) == ["session-1", "session-3"]
    assert len(session.browsers) == 0
    assert provider.closed


async def test_a_key_loaded_at_runtime_leaves_every_open_session_where_it_was(
    session: WebRouterTools, isolated_home: Path
) -> None:
    """``load_env`` hands later calls to a new router; a session the old one opened keeps running on it."""
    session_id = await _open(session)
    provider = _provider(session)
    keys = isolated_home / "keys.env"
    keys.write_text(f"{ENV_BRAVE_API_KEY}=not-a-real-key-0123\n", encoding="utf-8")

    _ok(await call_tool(session, MCP_TOOL_LOAD_ENV, {"path": str(keys)}))

    assert provider.released == [] and not provider.closed
    _ok(await call_tool(session, MCP_TOOL_BROWSER_GOTO, {"session_id": session_id, "url": "https://example.com"}))
    _ok(await call_tool(session, MCP_TOOL_BROWSER_CLOSE, {"session_id": session_id}))
    assert provider.released == ["session-1"]
    await session.aclose()
    assert provider.closed, "the old router is closed with the rest at shutdown"


async def test_the_server_releases_every_session_when_the_client_closes_stdin(
    session: WebRouterTools, monkeypatch: pytest.MonkeyPatch
) -> None:
    for _ in range(2):
        await _open(session)

    @asynccontextmanager
    async def closed_stdio() -> AsyncIterator[tuple[Any, Any]]:
        send, receive = anyio.create_memory_object_stream[Any](1)
        out_send, out_receive = anyio.create_memory_object_stream[Any](1)
        await send.aclose()
        async with receive, out_send, out_receive:
            yield receive, out_send

    monkeypatch.setattr(server_module, "stdio_server", closed_stdio)
    with anyio.fail_after(5):
        await serve(session)

    assert sorted(_provider(session).released) == ["session-1", "session-2"]


async def test_the_server_releases_every_session_when_it_is_cancelled(
    session: WebRouterTools, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _open(session)

    @asynccontextmanager
    async def silent_stdio() -> AsyncIterator[tuple[Any, Any]]:
        send, receive = anyio.create_memory_object_stream[Any](1)
        out_send, out_receive = anyio.create_memory_object_stream[Any](1)
        async with send, receive, out_send, out_receive:
            yield receive, out_send

    monkeypatch.setattr(server_module, "stdio_server", silent_stdio)
    with anyio.move_on_after(0.2):
        await serve(session)

    assert _provider(session).released == ["session-1"]


@pytest.mark.skipif(sys.platform == "win32", reason="no POSIX signals to send")
@pytest.mark.parametrize("signum", ["SIGTERM", "SIGINT", "SIGHUP"])
async def test_a_shutdown_signal_releases_every_session_before_the_process_exits(
    session: WebRouterTools, monkeypatch: pytest.MonkeyPatch, signum: str
) -> None:
    await _open(session)
    exits: list[int] = []
    monkeypatch.setattr(server_module.os, "_exit", exits.append)

    with anyio.fail_after(5):
        async with anyio.create_task_group() as group:
            group.start_soon(server_module._exit_on_signal, session)
            await anyio.wait_all_tasks_blocked()
            os.kill(os.getpid(), getattr(signal, signum))
            while not exits:
                await anyio.sleep(0.01)
            group.cancel_scope.cancel()

    assert exits == [128 + getattr(signal, signum)]
    assert _provider(session).released == ["session-1"] and _provider(session).closed


def test_every_browser_tool_is_served(session: WebRouterTools) -> None:
    names = [tool.name for tool in list_tools(session).tools]
    assert names[-len(MCP_BROWSER_TOOLS) :] == list(MCP_BROWSER_TOOLS)
    assert "browser" in session.capabilities()


# -- Against a real browser ----------------------------------------------------

FIXTURES = Path(__file__).parent.parent / "fixtures" / "browser"
ACTIONS = (FIXTURES / "actions.html").as_uri()


def _chrome() -> str | None:
    from web_router.exceptions import ConfigurationError
    from web_router.providers.local import find_browser

    try:
        return find_browser()
    except ConfigurationError:
        return None


needs_chrome = pytest.mark.skipif(
    importlib.util.find_spec("playwright") is None or _chrome() is None,
    reason="needs Playwright and a Chromium-family browser",
)


def _ref(tree: str, needle: str) -> str:
    for line in tree.splitlines():
        if needle in line and "[ref=" in line:
            return line.split("[ref=", 1)[1].split("]", 1)[0]
    raise AssertionError(f"{needle!r} is not in the snapshot:\n{tree}")


def _browsers_started_by(parent: int) -> set[int]:
    """Pids of the browsers ``local`` launched for process ``parent``, found by their profile directory."""
    listing = subprocess.run(["ps", "-ax", "-o", "pid=,ppid=,command="], capture_output=True, text=True, check=True)
    pids = set()
    for line in listing.stdout.splitlines():
        pid, ppid, command = line.split(None, 2)
        if int(ppid) == parent and LOCAL_BROWSER_TEMP_PREFIX in command and "--remote-debugging-port" in command:
            pids.add(int(pid))
    return pids


def _running(pids: set[int]) -> set[int]:
    listing = subprocess.run(["ps", "-ax", "-o", "pid="], capture_output=True, text=True, check=True).stdout
    return pids & {int(pid) for pid in listing.split()}


@pytest.mark.browser
@needs_chrome
async def test_the_tools_drive_a_real_page_and_never_show_its_cdp_url(monkeypatch: pytest.MonkeyPatch) -> None:
    # The fixture pages are files, which only an operator can let the tools open.
    monkeypatch.setenv(ENV_MCP_BROWSER_ALLOW_FILE_URLS, "1")
    tools = WebRouterTools(AsyncWebRouter(PROVIDER_LOCAL))
    outputs: list[str] = []

    async def run(name: str, **arguments: Any) -> Any:
        result = await call_tool(tools, name, arguments)
        outputs.extend(_texts(result))
        assert not result.is_error, result.content[0].text
        return result

    try:
        opened = await run(MCP_TOOL_BROWSER_OPEN, provider=PROVIDER_LOCAL)
        session_id = json.loads(opened.content[0].text)["session_id"]
        handle = tools.browsers._sessions[session_id].handle
        cdp_url, vendor_id = handle.cdp_url, handle.session_id

        goto = json.loads((await run(MCP_TOOL_BROWSER_GOTO, session_id=session_id, url=ACTIONS)).content[0].text)
        assert goto["title"] == "Page actions"
        tree = (await run(MCP_TOOL_BROWSER_SNAPSHOT, session_id=session_id)).content[1].text
        await run(
            MCP_TOOL_BROWSER_TYPE, session_id=session_id, text="mcp", ref=_ref(tree, 'textbox "Search"'), submit=True
        )
        await run(MCP_TOOL_BROWSER_CLICK, session_id=session_id, ref=_ref(tree, 'button "Count"'))
        await run(MCP_TOOL_BROWSER_PRESS, session_id=session_id, key="Escape")
        read = json.loads((await run(MCP_TOOL_BROWSER_READ, session_id=session_id)).content[0].text)
        assert "pressed:Escape" in read["documents"][0]["content"]
        shot = await run(MCP_TOOL_BROWSER_SCREENSHOT, session_id=session_id)
        assert base64.b64decode(shot.content[1].data).startswith(b"\x89PNG")

        await call_tool(tools, MCP_TOOL_BROWSER_CLICK, {"session_id": session_id, "ref": _ref(tree, "Re-render")})
        stale = await call_tool(tools, MCP_TOOL_BROWSER_CLICK, {"session_id": session_id, "ref": _ref(tree, "Apple")})
        outputs.extend(_texts(stale))
        assert stale.is_error and "Call browser_snapshot" in stale.content[0].text
        # The debugging endpoint of the model's own browser is one goto away; it must not hand over the socket.
        port = cdp_url.split(":")[2].split("/")[0]
        refused = await call_tool(
            tools, MCP_TOOL_BROWSER_GOTO, {"session_id": session_id, "url": f"http://localhost:{port}/json/version"}
        )
        outputs.extend(_texts(refused))
        assert refused.is_error and "debugging endpoint" in refused.content[0].text
        await run(MCP_TOOL_BROWSER_READ, session_id=session_id)

        await run(MCP_TOOL_BROWSER_CLOSE, session_id=session_id)
    finally:
        await tools.aclose()

    transcript = "\n".join(outputs)
    assert cdp_url not in transcript and vendor_id not in transcript and "ws://" not in transcript


@contextlib.contextmanager
def _redirecting_to(target: str) -> Iterator[str]:
    """A throwaway server on loopback whose every page 302s to ``target``, and its URL."""

    class Redirect(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(302)
            self.send_header("Location", target)
            self.end_headers()

        def log_message(self, *args: Any) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/"
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.browser
@needs_chrome
async def test_a_real_session_refuses_its_debugging_endpoint_and_files_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ENV_MCP_BROWSER_ALLOW_FILE_URLS, raising=False)
    secret_file = tmp_path / "notes.html"
    secret_file.write_text("<title>private</title><p>PRIVATE-NOTE-TEXT</p>")
    tools = WebRouterTools(AsyncWebRouter(PROVIDER_LOCAL))
    outputs: list[str] = []

    async def call(name: str, **arguments: Any) -> Any:
        result = await call_tool(tools, name, arguments)
        outputs.extend(_texts(result))
        return result

    try:
        opened = await call(MCP_TOOL_BROWSER_OPEN, provider=PROVIDER_LOCAL)
        session_id = json.loads(opened.content[0].text)["session_id"]
        port = tools.browsers._sessions[session_id].handle.cdp_url.split(":")[2].split("/")[0]

        endpoint = await call(MCP_TOOL_BROWSER_GOTO, session_id=session_id, url=f"http://127.0.0.1:{port}/json")
        local_file = await call(MCP_TOOL_BROWSER_GOTO, session_id=session_id, url=secret_file.as_uri())
        with _redirecting_to(f"http://127.0.0.1:{port}/json/version") as redirector:
            redirected = await call(MCP_TOOL_BROWSER_GOTO, session_id=session_id, url=redirector)
        text = json.loads((await call(MCP_TOOL_BROWSER_READ, session_id=session_id)).content[0].text)

        assert endpoint.is_error and "debugging endpoint" in endpoint.content[0].text
        assert local_file.is_error and ENV_MCP_BROWSER_ALLOW_FILE_URLS in local_file.content[0].text
        assert redirected.is_error and "debugging endpoint" in redirected.content[0].text
        assert text["documents"][0]["url"] == MCP_BROWSER_BLANK_URL
        await call(MCP_TOOL_BROWSER_CLOSE, session_id=session_id)
    finally:
        await tools.aclose()

    transcript = "\n".join(outputs)
    assert "PRIVATE-NOTE-TEXT" not in transcript
    assert "webSocketDebuggerUrl" not in transcript and "ws://" not in transcript


@contextlib.contextmanager
def _serving(root: Path, hop: str) -> Iterator[str]:
    """A throwaway loopback server for the files in ``root``, whose ``/hop`` 302s to ``hop``; its base URL."""

    class Files(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, directory=str(root), **kwargs)

        def do_GET(self) -> None:
            if self.path != "/hop":
                super().do_GET()
                return
            self.send_response(302)
            self.send_header("Location", hop)
            self.end_headers()

        def log_message(self, *args: Any) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Files)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


FLEETING_FRAME = """<!doctype html><title>fleeting frame</title>
<script>
  // Sent to a refused address by a redirect, then removed once it loads: gone before any after-the-fact look.
  const frame = document.createElement("iframe");
  frame.onload = () => frame.remove();
  frame.src = "/hop";
  document.documentElement.appendChild(frame);
</script>
<p>FLEETING-PAGE-TEXT</p>
"""


@pytest.mark.browser
@needs_chrome
async def test_a_frame_that_visits_the_debugging_endpoint_and_vanishes_still_refuses_the_action(
    tmp_path: Path,
) -> None:
    (tmp_path / "fleeting.html").write_text(FLEETING_FRAME)
    tools = WebRouterTools(AsyncWebRouter(PROVIDER_LOCAL))
    outputs: list[str] = []
    try:
        opened = await call_tool(tools, MCP_TOOL_BROWSER_OPEN, {"provider": PROVIDER_LOCAL})
        session_id = json.loads(opened.content[0].text)["session_id"]
        handle = tools.browsers._sessions[session_id].handle
        port = handle.cdp_url.split(":")[2].split("/")[0]

        with _serving(tmp_path, f"http://127.0.0.1:{port}/json/version") as base:
            result = await call_tool(
                tools, MCP_TOOL_BROWSER_GOTO, {"session_id": session_id, "url": f"{base}/fleeting.html"}
            )
        outputs.extend(_texts(result))
        after = json.loads((await call_tool(tools, MCP_TOOL_BROWSER_READ, {"session_id": session_id})).content[0].text)

        assert result.is_error and "browser_goto refused" in result.content[0].text
        assert "debugging endpoint" in result.content[0].text
        assert after["documents"][0]["url"] == MCP_BROWSER_BLANK_URL
    finally:
        await tools.aclose()

    transcript = "\n".join(outputs)
    assert "FLEETING-PAGE-TEXT" not in transcript and "webSocketDebuggerUrl" not in transcript


@contextlib.contextmanager
def _echoing_websocket() -> Iterator[int]:
    """A one-connection WebSocket server on loopback that answers the first text frame with ``echo:<it>``; its port."""
    listener = socket.create_server(("127.0.0.1", 0))

    def serve() -> None:
        with contextlib.suppress(OSError):
            connection, _ = listener.accept()
            with connection:
                request = b""
                while b"\r\n\r\n" not in request:
                    request += connection.recv(4096)
                key = re.search(rb"Sec-WebSocket-Key: *(\S+)", request, re.IGNORECASE)
                assert key is not None
                accept = base64.b64encode(hashlib.sha1(key.group(1) + WEBSOCKET_GUID).digest())
                connection.sendall(
                    b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                    b"Sec-WebSocket-Accept: " + accept + b"\r\n\r\n"
                )
                # A client frame is always masked; a short one has a 2-byte header, then 4 bytes of mask.
                header = connection.recv(2)
                length = header[1] & 0x7F
                mask = connection.recv(4)
                payload = bytes(byte ^ mask[i % 4] for i, byte in enumerate(connection.recv(length)))
                reply = b"echo:" + payload
                connection.sendall(bytes([0x81, len(reply)]) + reply)
                connection.recv(1024)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield listener.getsockname()[1]
    finally:
        listener.close()


WEBSOCKET_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

WEBSOCKET_PAGE = """<!doctype html><title>waiting</title>
<script>
  const socket = new WebSocket("ws://127.0.0.1:{port}/");
  socket.onopen = () => socket.send("hello");
  socket.onmessage = (event) => {{ document.title = event.data; }};
  socket.onerror = () => {{ document.title = "socket-error"; }};
</script>
"""


@pytest.mark.browser
@needs_chrome
async def test_a_websocket_the_policy_allows_really_connects(tmp_path: Path) -> None:
    tools = WebRouterTools(AsyncWebRouter(PROVIDER_LOCAL))
    try:
        opened = await call_tool(tools, MCP_TOOL_BROWSER_OPEN, {"provider": PROVIDER_LOCAL})
        session_id = json.loads(opened.content[0].text)["session_id"]
        with _echoing_websocket() as port:
            (tmp_path / "socket.html").write_text(WEBSOCKET_PAGE.format(port=port))
            with _serving(tmp_path, MCP_BROWSER_BLANK_URL) as base:
                goto = await call_tool(
                    tools, MCP_TOOL_BROWSER_GOTO, {"session_id": session_id, "url": f"{base}/socket.html"}
                )
                assert not goto.is_error, goto.content[0].text
                title = "waiting"
                deadline = time.monotonic() + 10
                while title == "waiting" and time.monotonic() < deadline:
                    await anyio.sleep(0.1)
                    read = await call_tool(tools, MCP_TOOL_BROWSER_READ, {"session_id": session_id})
                    title = json.loads(read.content[0].text)["documents"][0]["title"]
    finally:
        await tools.aclose()

    assert title == "echo:hello"


def _server(env: dict[str, str]) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [sys.executable, "-m", "web_router.mcp"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=env,
    )


def _rpc(process: subprocess.Popen[bytes], message_id: int | None, method: str, params: dict[str, Any]) -> Any:
    assert process.stdin is not None and process.stdout is not None
    message: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": params}
    if message_id is not None:
        message["id"] = message_id
    process.stdin.write((json.dumps(message) + "\n").encode())
    process.stdin.flush()
    if message_id is None:
        return None
    while True:
        reply = json.loads(process.stdout.readline())
        if reply.get("id") == message_id:
            return reply


@pytest.fixture
def server_with_a_browser() -> Iterator[tuple[subprocess.Popen[bytes], set[int]]]:
    """A real server subprocess that has opened one ``local`` browser through ``browser_open``, and its pid."""
    process = _server({**os.environ})
    browsers: set[int] = set()
    try:
        _rpc(
            process,
            1,
            "initialize",
            {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}},
        )
        _rpc(process, None, "notifications/initialized", {})
        reply = _rpc(process, 2, "tools/call", {"name": MCP_TOOL_BROWSER_OPEN, "arguments": {"provider": "local"}})
        assert not reply["result"].get("isError"), reply
        browsers = _browsers_started_by(process.pid)
        assert browsers, "browser_open launched no browser"
        yield process, browsers
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        for pid in _running(browsers):
            os.kill(pid, signal.SIGKILL)


def _still_running_after(pids: set[int], timeout: float = 30.0) -> set[int]:
    deadline = time.monotonic() + timeout
    while _running(pids) and time.monotonic() < deadline:
        time.sleep(0.2)
    return _running(pids)


@pytest.mark.browser
@needs_chrome
def test_closing_stdin_releases_the_real_browser(
    server_with_a_browser: tuple[subprocess.Popen[bytes], set[int]],
) -> None:
    process, browsers = server_with_a_browser
    assert process.stdin is not None
    process.stdin.close()

    assert process.wait(timeout=30) == 0
    assert not _still_running_after(browsers)


@pytest.mark.browser
@needs_chrome
@pytest.mark.skipif(sys.platform == "win32", reason="no SIGTERM to send")
def test_sigterm_releases_the_real_browser(server_with_a_browser: tuple[subprocess.Popen[bytes], set[int]]) -> None:
    process, browsers = server_with_a_browser
    process.send_signal(signal.SIGTERM)

    assert process.wait(timeout=30) == 128 + signal.SIGTERM
    assert not _still_running_after(browsers)
