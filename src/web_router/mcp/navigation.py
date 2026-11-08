"""Where a model's browser may go: http(s) pages, and not the places a page should never reach.

A model driving a browser takes instructions from the pages it reads, so the
browser tools cannot trust the URL a model asks for any more than the links a
page offers. :class:`UrlPolicy` decides; :class:`NavigationGuard` enforces it on
a live session:

- before ``browser_goto`` touches the page, on the URL it was given;
- on the first request of every chain the context makes, through a
  ``route()``, which aborts a link, a form or a script's request to a refused
  address -- the host's DNS answer included, resolved just before the request
  is let through. Playwright does not route a redirect's later hops;
- while an action runs, on every navigation request -- redirect hops included
  -- and every committed frame navigation, recorded as they happen, so a frame
  that reaches a refused address and is removed before the action ends is
  still caught;
- after every action, on where the page and each attached frame ended up --
  and, since the route resolves only a chain's first hop, on the DNS answers of
  every name the action's recorded navigations touched, so a redirect hop or a
  frame sent to an alias of a refused address is caught before the result is
  handed back.

Any of them sends the page back to ``about:blank`` and refuses the action, so
what it loaded is never handed to the model. CDP ``Network.setBlockedURLs``
would block the debugging port inside the browser as well, but over
``connect_over_cdp`` it did not stop a redirect hop to it, so it is not used.

The DNS check narrows DNS rebinding but cannot close it: the browser does its
own lookup after the route lets a request through, and a name with a short TTL
can answer that one differently. ``docs/mcp.md`` says what is left.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from web_router._hosts import host_address, is_loopback, is_metadata
from web_router.constants import (
    BROWSER_ACTION_TIMEOUT,
    ENV_MCP_BROWSER_ALLOW_FILE_URLS,
    MCP_BROWSER_ALLOWED_SCHEMES,
    MCP_BROWSER_ALLOWED_URLS,
    MCP_BROWSER_BLANK_URL,
    MCP_BROWSER_DEFAULT_PORTS,
    MCP_BROWSER_ERROR_PAGE_SCHEMES,
    MCP_BROWSER_FILE_SCHEME,
    MCP_BROWSER_ROUTE_ABORT,
    MCP_BROWSER_ROUTE_PATTERN,
    MCP_BROWSER_SETTLE_RESOLVE_LIMIT,
    MCP_BROWSER_WEBSOCKET_SCHEMES,
    MCP_MSG_BROWSER_REFUSED_CDP,
    MCP_MSG_BROWSER_REFUSED_FILE,
    MCP_MSG_BROWSER_REFUSED_METADATA,
    MCP_MSG_BROWSER_REFUSED_REDIRECT,
    MCP_MSG_BROWSER_REFUSED_SCHEME,
    MCP_MSG_BROWSER_REFUSED_UNPARSEABLE,
    MCP_MSG_BROWSER_REFUSED_UNVERIFIED,
    MCP_TRUE_VALUES,
    MS_PER_SECOND,
)

if TYPE_CHECKING:
    from playwright.async_api import Frame, Page, Request, Route, WebSocketRoute


def files_allowed() -> bool:
    """Whether the operator has opted in to ``file:`` URLs through the environment."""
    return os.environ.get(ENV_MCP_BROWSER_ALLOW_FILE_URLS, "").strip().lower() in MCP_TRUE_VALUES


@dataclass(frozen=True)
class UrlPolicy:
    """The rules for one session: its own debugging endpoint is off limits, and so is everything else listed."""

    cdp_host: str | None = None
    cdp_port: int | None = None
    allow_files: bool = False

    @classmethod
    def for_session(cls, cdp_url: str, *, allow_files: bool | None = None) -> UrlPolicy:
        """The policy for a session whose CDP endpoint is ``cdp_url``; ``file:`` per the environment unless given."""
        host, port = _host_port(cdp_url)
        return cls(host, port, files_allowed() if allow_files is None else allow_files)

    def refusal(self, url: str, *, websocket: bool = False, current: bool = False, frame: bool = False) -> str | None:
        """Why ``url`` may not be opened, or ``None`` if it may.

        ``websocket`` admits ``ws:`` / ``wss:``, for a socket the page opens;
        ``current`` admits Chrome's own error page, which is where a refused or
        failed navigation leaves the tab and holds nothing from the address.
        ``frame`` is for a page's subframe: only its network address and
        ``file:`` are judged, since an ``about:srcdoc`` or ``data:`` frame holds
        nothing but what the page itself wrote.
        """
        if url in MCP_BROWSER_ALLOWED_URLS:
            return None
        try:
            parts = urlsplit(url)
            scheme = parts.scheme.lower()
            host = (parts.hostname or "").rstrip(".").lower()
            port = parts.port or MCP_BROWSER_DEFAULT_PORTS.get(scheme)
        except ValueError:
            return MCP_MSG_BROWSER_REFUSED_UNPARSEABLE
        if scheme == MCP_BROWSER_FILE_SCHEME:
            return (
                None
                if self.allow_files
                else MCP_MSG_BROWSER_REFUSED_FILE.format(env_var=ENV_MCP_BROWSER_ALLOW_FILE_URLS)
            )
        if (current and scheme in MCP_BROWSER_ERROR_PAGE_SCHEMES) or (
            frame and scheme not in MCP_BROWSER_ALLOWED_SCHEMES
        ):
            return None
        allowed = MCP_BROWSER_ALLOWED_SCHEMES | (MCP_BROWSER_WEBSOCKET_SCHEMES if websocket else frozenset())
        if scheme not in allowed:
            return MCP_MSG_BROWSER_REFUSED_SCHEME.format(scheme=scheme or "scheme-less")
        return self._host_refusal(host, port)

    def resolved_refusal(self, url: str, addresses: Iterable[str]) -> str | None:
        """Why ``url`` may not be opened now that its host has resolved to ``addresses``, or ``None``.

        For the answer a DNS name gave, which :meth:`refusal` never sees: a
        name of anyone's choosing can point at the metadata service or at the
        debugging port on loopback.
        """
        _, port = _host_port(url)
        for address in addresses:
            reason = self._host_refusal(address.split("%", 1)[0].lower(), port)
            if reason is not None:
                return reason
        return None

    def _host_refusal(self, host: str, port: int | None) -> str | None:
        if is_metadata(host):
            return MCP_MSG_BROWSER_REFUSED_METADATA
        if self.cdp_port is not None and port == self.cdp_port and _same_host(host, self.cdp_host):
            return MCP_MSG_BROWSER_REFUSED_CDP
        return None


class NavigationGuard:
    """Holds one session to its :class:`UrlPolicy`, once :meth:`install` has run on its connected page."""

    def __init__(self, policy: UrlPolicy) -> None:
        self.policy = policy
        self._page: Page | None = None
        self._blocked: str | None = None
        self._named: dict[tuple[str, int | None], str] = {}
        self._overflowed = False

    async def install(self, page: Page) -> None:
        """Route every request of the page's context through the policy. The handle's connect hook.

        Also watches every navigation request -- a redirect hop included, which
        the route never sees -- and every frame navigation, so a frame that
        reaches a refused address and is gone again by the time :meth:`settle`
        looks is still caught.
        """
        self._page = page
        page.context.on("request", self._on_request)
        page.on("framenavigated", self._on_frame_navigated)
        await page.context.route(MCP_BROWSER_ROUTE_PATTERN, self._route)
        await page.context.route_web_socket(MCP_BROWSER_ROUTE_PATTERN, self._route_web_socket)

    async def settle(self) -> str | None:
        """After an action: why the page may not stay where it is, having sent it to ``about:blank``, or ``None``."""
        blocked, self._blocked = self._blocked, None
        named, self._named = self._named, {}
        overflowed, self._overflowed = self._overflowed, False
        if self._page is None:
            return None
        current = self._where_refused(self._page)
        if blocked is None and current is None:
            # The route resolved only each chain's first hop; a redirect hop or
            # a frame navigation to an alias of a refused address is caught here
            # or not at all, so nothing the action loaded is handed back first.
            if overflowed:
                # Fail closed: resolving only the first names recorded would
                # let a page bury an alias hop behind a crowd of benign hosts.
                current = MCP_MSG_BROWSER_REFUSED_UNVERIFIED
            else:
                for reason in await asyncio.gather(*(self._resolved_refusal(url) for url in named.values())):
                    if reason is not None:
                        current = reason
                        break
        if blocked is None and current is None:
            return None
        with contextlib.suppress(Exception):
            await self._page.goto(MCP_BROWSER_BLANK_URL, timeout=BROWSER_ACTION_TIMEOUT * MS_PER_SECOND)
        return blocked or MCP_MSG_BROWSER_REFUSED_REDIRECT.format(reason=current)

    def _flag(self, reason: str | None) -> None:
        if reason is not None and self._blocked is None:
            self._blocked = reason

    def _on_request(self, request: Request) -> None:
        if request.is_navigation_request():
            self._flag(self.policy.refusal(request.url, frame=_is_subframe(request)))
            self._record(request.url)

    def _on_frame_navigated(self, frame: Frame) -> None:
        self._flag(self.policy.refusal(frame.url, current=True, frame=frame.parent_frame is not None))
        self._record(frame.url)

    def _record(self, url: str) -> None:
        host, port = _host_port(url)
        if host is None or host_address(host) is not None:
            # A literal address was already judged as it stands; only a name has an answer to check.
            return
        if (host, port) in self._named:
            return
        if len(self._named) >= MCP_BROWSER_SETTLE_RESOLVE_LIMIT:
            # A name the guard will not resolve is a name it cannot vouch for,
            # so the action is refused when it settles rather than half-checked.
            self._overflowed = True
            return
        self._named[(host, port)] = url

    def _where_refused(self, page: Page) -> str | None:
        reason = self.policy.refusal(page.url, current=True)
        for child in page.frames:
            if reason is not None:
                break
            if child.parent_frame is not None:
                reason = self.policy.refusal(child.url, current=True, frame=True)
        return reason

    async def _route(self, route: Route) -> None:
        request = route.request
        reason = self.policy.refusal(request.url) or await self._resolved_refusal(request.url)
        if reason is None:
            await route.continue_()
            return
        if request.is_navigation_request():
            self._flag(reason)
        await route.abort(MCP_BROWSER_ROUTE_ABORT)

    async def _route_web_socket(self, route: WebSocketRoute) -> None:
        if (self.policy.refusal(route.url, websocket=True) or await self._resolved_refusal(route.url)) is None:
            # Not awaited: Playwright's async API makes this one call
            # synchronous -- it sends the connect and returns the server-side
            # route -- so awaiting it raises. The real-browser test holds it.
            route.connect_to_server()
        else:
            await route.close()

    async def _resolved_refusal(self, url: str) -> str | None:
        host, port = _host_port(url)
        if host is None or host_address(host) is not None:
            # No name to look up: a literal address was judged as it stands.
            return None
        try:
            # On a worker thread: a lookup can take seconds, and every other
            # request of every session waits on this event loop meanwhile.
            answers = await asyncio.to_thread(socket.getaddrinfo, host, port, type=socket.SOCK_STREAM)
        except (OSError, UnicodeError):
            # A name that does not resolve here reaches nothing through the browser either.
            return None
        return self.policy.resolved_refusal(url, (str(answer[4][0]) for answer in answers))


def _is_subframe(request: Request) -> bool:
    try:
        return request.frame.parent_frame is not None
    except Exception:
        # A service worker's request has no frame; it navigates nothing.
        return False


def _host_port(url: str) -> tuple[str | None, int | None]:
    try:
        parts = urlsplit(url)
        return (parts.hostname or "").rstrip(".").lower() or None, parts.port or MCP_BROWSER_DEFAULT_PORTS.get(
            parts.scheme.lower()
        )
    except ValueError:
        return None, None


def _same_host(host: str, cdp_host: str | None) -> bool:
    if cdp_host is None:
        return False
    # 127.0.0.1, localhost and [::1] all reach a debugger listening on loopback.
    address = host_address(host)
    same = host == cdp_host or (address is not None and address == host_address(cdp_host))
    return same or (is_loopback(host) and is_loopback(cdp_host))
