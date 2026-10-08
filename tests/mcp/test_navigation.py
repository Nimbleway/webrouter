"""The URL policy the ``browser_*`` tools hold a session to, and the route that enforces it.

Pure unit tests: :class:`UrlPolicy` is a function of a URL, and the guard is
driven with stand-ins for Playwright's page, route and socket, so nothing here
launches a browser. The real-browser checks live beside the other browser tests
in ``test_browser_tools.py``.
"""

from __future__ import annotations

import importlib.util
import inspect
import socket
from typing import Any

import pytest

from web_router.constants import (
    ENV_MCP_BROWSER_ALLOW_FILE_URLS,
    MCP_BROWSER_BLANK_URL,
    MCP_BROWSER_ROUTE_ABORT,
    MCP_BROWSER_SETTLE_RESOLVE_LIMIT,
)
from web_router.mcp import navigation
from web_router.mcp.navigation import NavigationGuard, UrlPolicy, files_allowed

CDP = "ws://127.0.0.1:9333/devtools/browser/abc"

POLICY = UrlPolicy.for_session(CDP, allow_files=False)

PUBLIC = "93.184.215.14"


class FakeResolver:
    """``socket.getaddrinfo`` answering from a table, every other name with a public address, and a log."""

    def __init__(self) -> None:
        self.answers: dict[str, list[str]] = {}
        self.asked: list[tuple[str, Any]] = []

    def __call__(self, host: str, port: Any, *args: Any, **kwargs: Any) -> list[tuple[Any, ...]]:
        self.asked.append((host, port))
        answers = self.answers.get(host, [PUBLIC])
        if not answers:
            raise socket.gaierror(socket.EAI_NONAME, "nodename nor servname provided, or not known")
        return [
            (socket.AF_INET6 if ":" in address else socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port or 0))
            for address in answers
        ]


@pytest.fixture(autouse=True)
def resolver(monkeypatch: pytest.MonkeyPatch) -> FakeResolver:
    """No test here asks real DNS: the route's lookup is answered from a table."""
    fake = FakeResolver()
    monkeypatch.setattr(navigation.socket, "getaddrinfo", fake)
    return fake


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/",
        "http://example.com:8080/path?q=1",
        "about:blank",
        "http://127.0.0.1:8000/",
        "http://localhost:9334/",
    ],
)
def test_ordinary_pages_are_allowed(url: str) -> None:
    assert POLICY.refusal(url) is None


@pytest.mark.parametrize(
    "url",
    [
        "chrome://settings",
        "chrome-untrusted://terminal/",
        "devtools://devtools/bundled/inspector.html",
        "chrome-extension://abcdef/page.html",
        "view-source:https://example.com/",
        "javascript:alert(1)",
        "data:text/html,<h1>hi</h1>",
        "blob:https://example.com/uuid",
        "ftp://example.com/file",
        "about:config",
        "ws://example.com/socket",
        "example.com/no-scheme",
    ],
)
def test_every_other_scheme_is_refused(url: str) -> None:
    reason = POLICY.refusal(url)
    assert reason is not None and "do not open" in reason


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://169.254.170.2/v2/credentials",
        "http://169.254.1.1/",
        "http://[fd00:ec2::254]/",
        "http://[fe80::1]/",
        "http://[::ffff:169.254.169.254]/",
        "http://metadata.google.internal/computeMetadata/v1/",
        "http://METADATA.GOOGLE.INTERNAL./",
        "http://100.100.100.200/latest/meta-data/",
        "https://instance-data/",
    ],
)
def test_cloud_metadata_hosts_are_refused(url: str) -> None:
    assert POLICY.refusal(url) == "that is a cloud metadata endpoint"


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:9333/json",
        "http://127.0.0.1:9333/json/version",
        "http://localhost:9333/json/list",
        "http://[::1]:9333/",
        "http://127.0.0.2:9333/",
        "http://0.0.0.0:9333/",
    ],
)
def test_the_sessions_own_debugging_endpoint_is_refused_by_every_loopback_name(url: str) -> None:
    assert POLICY.refusal(url) == "that is this browser session's own debugging endpoint"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://2130706433:9333/json", "debugging endpoint"),
        ("http://0x7f000001:9333/json", "debugging endpoint"),
        ("http://0X7F000001:9333/json", "debugging endpoint"),
        ("http://127.1:9333/json", "debugging endpoint"),
        ("http://0177.0.0.1:9333/json", "debugging endpoint"),
        ("http://0x7f.0.0.1:9333/json", "debugging endpoint"),
        ("http://2852039166/latest/meta-data/", "cloud metadata endpoint"),
        ("http://0xa9fea9fe/latest/meta-data/", "cloud metadata endpoint"),
        ("http://169.254.43518/latest/meta-data/", "cloud metadata endpoint"),
        ("http://0251.0376.0251.0376/", "cloud metadata endpoint"),
    ],
)
def test_other_spellings_of_an_ipv4_address_are_read_as_the_address(url: str, expected: str) -> None:
    reason = POLICY.refusal(url)
    assert reason is not None and expected in reason


@pytest.mark.parametrize(
    "url",
    ["http://2130706434:8000/", "http://0x08080808/", "http://1.2.3.4.5/", "http://123abc/", "http://cafe.be/"],
)
def test_numeric_looking_hosts_that_are_not_refused_addresses_stay_allowed(url: str) -> None:
    assert POLICY.refusal(url) is None


def test_a_remote_cdp_address_is_matched_in_any_spelling() -> None:
    policy = UrlPolicy.for_session("ws://10.0.0.5:9222/devtools/browser/x", allow_files=False)

    assert policy.refusal("http://167772165:9222/json") is not None
    assert policy.refusal("http://10.5:9222/json") is not None


def test_a_socket_may_open_but_not_to_the_debugging_endpoint() -> None:
    assert POLICY.refusal("wss://example.com/socket", websocket=True) is None
    assert POLICY.refusal("ws://127.0.0.1:9333/devtools/page/1", websocket=True) is not None


def test_a_remote_cdp_host_is_refused_only_on_its_own_port() -> None:
    policy = UrlPolicy.for_session("wss://connect.vendor.test/?apiKey=k", allow_files=False)

    assert policy.refusal("https://connect.vendor.test/json") is not None
    assert policy.refusal("http://connect.vendor.test:8080/") is None
    assert policy.refusal("http://127.0.0.1:443/") is None


def test_chromes_error_page_is_only_admitted_as_where_the_page_already_is() -> None:
    assert POLICY.refusal("chrome-error://chromewebdata/") is not None
    assert POLICY.refusal("chrome-error://chromewebdata/", current=True) is None


def test_an_unparseable_url_is_refused() -> None:
    assert POLICY.refusal("http://[not-an-address/") == "the URL could not be parsed"


def test_file_urls_follow_the_operators_environment_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_MCP_BROWSER_ALLOW_FILE_URLS, raising=False)
    off = UrlPolicy.for_session(CDP)
    monkeypatch.setenv(ENV_MCP_BROWSER_ALLOW_FILE_URLS, "0")
    zero = UrlPolicy.for_session(CDP)
    monkeypatch.setenv(ENV_MCP_BROWSER_ALLOW_FILE_URLS, "1")
    on = UrlPolicy.for_session(CDP)

    refusal = off.refusal("file:///etc/hosts")
    assert refusal is not None and f"{ENV_MCP_BROWSER_ALLOW_FILE_URLS}=1" in refusal
    assert zero.refusal("file:///etc/hosts") is not None
    assert files_allowed()
    assert on.refusal("file:///etc/hosts") is None
    # The switch is for files only; it opens no other scheme.
    assert on.refusal("chrome://settings") is not None


class FakeRequest:
    def __init__(self, url: str, *, navigation: bool = True, main_frame: bool = True) -> None:
        self.url = url
        self._navigation = navigation
        self.frame = type("Frame", (), {"parent_frame": None if main_frame else object()})()

    def is_navigation_request(self) -> bool:
        return self._navigation


class FakeRoute:
    def __init__(self, request: FakeRequest) -> None:
        self.request = request
        self.outcome: tuple[str, Any] | None = None

    async def continue_(self) -> None:
        self.outcome = ("continue", None)

    async def abort(self, error_code: str) -> None:
        self.outcome = ("abort", error_code)


class FakeSocket:
    """Playwright's async ``WebSocketRoute``, down to which of its calls are coroutines."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.outcome: str | None = None

    def connect_to_server(self) -> FakeSocket:
        self.outcome = "connected"
        return self

    async def close(self) -> None:
        self.outcome = "closed"


class FakeContext:
    def __init__(self) -> None:
        self.routes: dict[str, Any] = {}
        self.listeners: dict[str, Any] = {}

    def on(self, event: str, handler: Any) -> None:
        self.listeners[event] = handler

    async def route(self, pattern: str, handler: Any) -> None:
        self.routes["http"] = handler

    async def route_web_socket(self, pattern: str, handler: Any) -> None:
        self.routes["ws"] = handler


class FakeFrame:
    def __init__(self, url: str, parent: Any) -> None:
        self.url = url
        self.parent_frame = parent


class FakePlaywrightPage:
    def __init__(self) -> None:
        self.url = MCP_BROWSER_BLANK_URL
        self.context = FakeContext()
        self.visited: list[str] = []
        self.children: list[str] = []
        self.listeners: dict[str, Any] = {}

    def on(self, event: str, handler: Any) -> None:
        self.listeners[event] = handler

    @property
    def frames(self) -> list[FakeFrame]:
        main = FakeFrame(self.url, None)
        return [main, *(FakeFrame(url, main) for url in self.children)]

    async def goto(self, url: str, **kwargs: Any) -> None:
        self.visited.append(url)
        self.url = url


async def _installed() -> tuple[NavigationGuard, FakePlaywrightPage]:
    guard = NavigationGuard(POLICY)
    page = FakePlaywrightPage()
    await guard.install(page)  # type: ignore[arg-type]
    return guard, page


async def test_a_redirect_to_a_refused_host_is_caught_where_the_page_landed() -> None:
    # Playwright routes only a chain's first request, so the hop to the metadata
    # service is seen by the after-action check -- the page is taken back before
    # anything it loaded reaches the model.
    guard, page = await _installed()
    first = FakeRoute(FakeRequest("http://127.0.0.1:8000/redirect"))

    await page.context.routes["http"](first)
    page.url = "http://169.254.169.254/latest/meta-data/"
    reason = await guard.settle()

    assert first.outcome == ("continue", None)
    assert reason is not None and "cloud metadata endpoint" in reason
    assert page.visited == [MCP_BROWSER_BLANK_URL]


async def test_a_link_to_a_refused_host_is_aborted_by_the_route_and_refused_after() -> None:
    guard, page = await _installed()
    redirected = FakeRoute(FakeRequest("http://169.254.169.254/latest/meta-data/"))
    allowed = FakeRoute(FakeRequest("https://example.com/"))

    await page.context.routes["http"](allowed)
    await page.context.routes["http"](redirected)
    page.url = "chrome-error://chromewebdata/"
    reason = await guard.settle()

    assert allowed.outcome == ("continue", None)
    assert redirected.outcome == ("abort", MCP_BROWSER_ROUTE_ABORT)
    assert reason == "that is a cloud metadata endpoint"
    assert page.visited == [MCP_BROWSER_BLANK_URL]
    assert await guard.settle() is None


async def test_a_refused_subresource_is_aborted_without_refusing_the_page() -> None:
    guard, page = await _installed()
    fetch = FakeRoute(FakeRequest("http://127.0.0.1:9333/json", navigation=False))

    await page.context.routes["http"](fetch)
    page.context.listeners["request"](fetch.request)
    page.url = "https://example.com/"

    assert fetch.outcome == ("abort", MCP_BROWSER_ROUTE_ABORT)
    assert await guard.settle() is None
    assert page.visited == []


async def test_a_frame_sent_to_a_refused_address_refuses_the_page_even_though_the_route_aborted_it() -> None:
    guard, page = await _installed()
    frame = FakeRoute(FakeRequest("http://127.0.0.1:9333/json", main_frame=False))

    await page.context.routes["http"](frame)
    page.url = "https://example.com/"

    assert frame.outcome == ("abort", MCP_BROWSER_ROUTE_ABORT)
    assert await guard.settle() == "that is this browser session's own debugging endpoint"


async def test_a_redirect_hop_is_caught_from_its_request_event() -> None:
    # The route sees only the first request of a chain; the request event sees every hop.
    guard, page = await _installed()
    page.context.listeners["request"](FakeRequest("http://169.254.169.254/latest/meta-data/", main_frame=False))
    page.context.listeners["request"](FakeRequest("data:text/html,ad", main_frame=False))
    page.url = "https://example.com/"

    assert await guard.settle() == "that is a cloud metadata endpoint"


async def test_a_frame_that_reached_a_refused_url_and_was_removed_is_still_caught() -> None:
    guard, page = await _installed()
    page.url = "https://example.com/"
    main = FakeFrame(page.url, None)
    page.listeners["framenavigated"](FakeFrame("about:srcdoc", main))
    page.listeners["framenavigated"](FakeFrame("http://127.0.0.1:9333/json/version", main))
    # The frame is gone by the time the action ends, so a look at page.frames alone finds nothing.
    page.children = []

    reason = await guard.settle()

    assert reason is not None and "debugging endpoint" in reason
    assert page.visited == [MCP_BROWSER_BLANK_URL]


async def test_a_main_frame_that_passed_through_a_file_url_is_caught() -> None:
    guard, page = await _installed()
    page.listeners["framenavigated"](FakeFrame("file:///etc/hosts", None))
    page.url = "https://example.com/"

    reason = await guard.settle()

    assert reason is not None and ENV_MCP_BROWSER_ALLOW_FILE_URLS in reason


async def test_a_page_left_on_a_refused_scheme_is_sent_back_to_blank() -> None:
    guard, page = await _installed()
    page.url = "chrome://settings/"

    reason = await guard.settle()

    assert reason is not None and "chrome" in reason
    assert page.url == MCP_BROWSER_BLANK_URL


async def test_a_frame_redirected_to_the_debugging_endpoint_takes_the_page_back_to_blank() -> None:
    # Playwright does not route a redirect's later hops, so this is the check that sees one.
    guard, page = await _installed()
    page.url = "https://example.com/"
    page.children = ["about:srcdoc", "data:text/html,ad", "http://127.0.0.1:9333/json/version"]

    reason = await guard.settle()

    assert reason is not None and "debugging endpoint" in reason
    assert page.visited == [MCP_BROWSER_BLANK_URL]


async def test_frames_the_page_wrote_itself_are_left_alone() -> None:
    guard, page = await _installed()
    page.url = "https://example.com/"
    page.children = ["about:srcdoc", "about:blank", "data:text/html,ad", "chrome-error://chromewebdata/"]

    assert await guard.settle() is None
    assert page.visited == []


async def test_a_socket_to_the_debugging_endpoint_is_closed() -> None:
    _, page = await _installed()
    refused, allowed = FakeSocket("ws://127.0.0.1:9333/devtools/browser/abc"), FakeSocket("wss://example.com/")

    await page.context.routes["ws"](refused)
    await page.context.routes["ws"](allowed)

    assert (refused.outcome, allowed.outcome) == ("closed", "connected")


async def test_a_guard_never_installed_has_nothing_to_settle() -> None:
    assert await NavigationGuard(POLICY).settle() is None


@pytest.mark.skipif(importlib.util.find_spec("playwright") is None, reason="the browser extra is not installed")
def test_the_socket_fake_awaits_exactly_what_playwright_does() -> None:
    # `connect_to_server` is synchronous even in the async API, so the guard
    # does not await it -- awaiting its return value raises. Should a
    # Playwright release change that, this fails before a real socket does.
    from playwright.async_api import WebSocketRoute

    for name in ("connect_to_server", "close"):
        assert inspect.iscoroutinefunction(getattr(WebSocketRoute, name)) == inspect.iscoroutinefunction(
            getattr(FakeSocket, name)
        ), name


@pytest.mark.parametrize(
    ("answers", "url", "expected"),
    [
        (["169.254.169.254"], "http://rebind.attacker.test/latest/meta-data/", "cloud metadata endpoint"),
        (["fe80::1%en0"], "http://rebind.attacker.test/", "cloud metadata endpoint"),
        (["::ffff:169.254.170.2"], "http://rebind.attacker.test/v2/credentials", "cloud metadata endpoint"),
        (["100.100.100.200"], "http://rebind.attacker.test/", "cloud metadata endpoint"),
        (["127.0.0.1"], "http://rebind.attacker.test:9333/json/version", "debugging endpoint"),
        (["::1"], "http://rebind.attacker.test:9333/json", "debugging endpoint"),
        ([PUBLIC, "127.0.0.1"], "http://rebind.attacker.test:9333/json", "debugging endpoint"),
    ],
)
async def test_a_name_that_resolves_to_a_refused_address_is_aborted(
    resolver: FakeResolver, answers: list[str], url: str, expected: str
) -> None:
    guard, page = await _installed()
    resolver.answers["rebind.attacker.test"] = answers
    route = FakeRoute(FakeRequest(url))

    await page.context.routes["http"](route)

    assert route.outcome == ("abort", MCP_BROWSER_ROUTE_ABORT)
    reason = await guard.settle()
    assert reason is not None and expected in reason


async def test_a_name_that_resolves_to_loopback_on_another_port_is_let_through(resolver: FakeResolver) -> None:
    _, page = await _installed()
    resolver.answers["dev.local.test"] = ["127.0.0.1"]
    route = FakeRoute(FakeRequest("http://dev.local.test:8000/"))

    await page.context.routes["http"](route)

    assert route.outcome == ("continue", None)
    assert resolver.asked == [("dev.local.test", 8000)]


async def test_a_literal_address_or_a_name_that_does_not_resolve_needs_no_answer(resolver: FakeResolver) -> None:
    _, page = await _installed()
    resolver.answers["nowhere.test"] = []
    literal, unknown = FakeRoute(FakeRequest("http://203.0.113.9/")), FakeRoute(FakeRequest("https://nowhere.test/"))

    await page.context.routes["http"](literal)
    await page.context.routes["http"](unknown)

    assert (literal.outcome, unknown.outcome) == (("continue", None), ("continue", None))
    assert resolver.asked == [("nowhere.test", 443)]


async def test_a_socket_to_a_name_that_resolves_to_the_debugging_endpoint_is_closed(resolver: FakeResolver) -> None:
    _, page = await _installed()
    resolver.answers["rebind.attacker.test"] = ["127.0.0.1"]
    socket_route = FakeSocket("ws://rebind.attacker.test:9333/devtools/browser/abc")

    await page.context.routes["ws"](socket_route)

    assert socket_route.outcome == "closed"


async def test_a_redirect_hop_to_an_alias_of_a_refused_address_is_caught_when_the_action_settles(
    resolver: FakeResolver,
) -> None:
    # The route never sees a redirect's later hops, and this hop's URL alone
    # passes: only its DNS answer names the metadata service.
    guard, page = await _installed()
    resolver.answers["md.attacker.test"] = ["169.254.169.254"]
    page.context.listeners["request"](FakeRequest("http://md.attacker.test/latest/meta-data/"))
    page.url = "https://example.com/"

    reason = await guard.settle()

    assert reason is not None and "cloud metadata endpoint" in reason
    assert page.visited == [MCP_BROWSER_BLANK_URL]
    assert ("md.attacker.test", 80) in resolver.asked


async def test_a_frame_sent_to_an_alias_of_the_debugging_endpoint_is_caught_when_the_action_settles(
    resolver: FakeResolver,
) -> None:
    guard, page = await _installed()
    resolver.answers["cdp.attacker.test"] = ["127.0.0.1"]
    main = FakeFrame("https://example.com/", None)
    page.listeners["framenavigated"](FakeFrame("http://cdp.attacker.test:9333/json/version", main))
    # The frame is gone by the time the action ends; only the record still names it.
    page.url = "https://example.com/"

    reason = await guard.settle()

    assert reason is not None and "debugging endpoint" in reason
    assert page.visited == [MCP_BROWSER_BLANK_URL]


async def test_named_hops_that_resolve_publicly_settle_clean(resolver: FakeResolver) -> None:
    guard, page = await _installed()
    page.context.listeners["request"](FakeRequest("https://example.com/"))
    page.context.listeners["request"](FakeRequest("https://example.com/next"))
    page.url = "https://example.com/"

    assert await guard.settle() is None
    # One lookup per named host, and the record does not carry into the next action.
    assert resolver.asked == [("example.com", 443)]
    assert await guard.settle() is None
    assert resolver.asked == [("example.com", 443)]
    assert page.visited == []


async def test_more_named_hosts_than_the_guard_resolves_refuses_rather_than_skipping_the_check(
    resolver: FakeResolver,
) -> None:
    """A page could bury an alias hop behind a crowd of benign hosts, so past the limit the guard fails closed."""
    guard, page = await _installed()
    for index in range(MCP_BROWSER_SETTLE_RESOLVE_LIMIT + 1):
        page.context.listeners["request"](FakeRequest(f"https://host-{index}.example.test/"))
    page.url = "https://example.com/"

    reason = await guard.settle()

    assert reason is not None and "more named hosts" in reason
    assert page.visited == [MCP_BROWSER_BLANK_URL]
    assert resolver.asked == [], "refused outright: a half-checked crowd could hide the hop that matters"

    # The overflow does not carry into the next action.
    page.visited.clear()
    page.context.listeners["request"](FakeRequest("https://example.com/"))
    assert await guard.settle() is None
    assert resolver.asked == [("example.com", 443)]


def test_a_resolved_address_is_judged_like_a_literal_one() -> None:
    assert POLICY.resolved_refusal("https://a.test/", ["10.0.0.1", "2001:db8::1"]) is None
    assert POLICY.resolved_refusal("https://a.test/", ["169.254.1.1"]) == "that is a cloud metadata endpoint"
    assert POLICY.resolved_refusal("http://a.test:9333/", ["127.0.0.5"]) is not None
