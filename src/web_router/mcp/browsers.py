"""The browser sessions one MCP server holds, and the id a model holds for each.

A model cannot hold a :class:`~web_router.AsyncBrowserHandle` or run a ``with``
block, so the server keeps every session it opened in a :class:`BrowserSessions`
table and hands the model an opaque id it minted. The backend's own session id,
the CDP URL and any live-view URL stay on this side: each is a way into a billed
browser, and some backends put the account key in the CDP URL.

Each session keeps one handle, and so one CDP connection, for its whole life --
several backends end a session when its CDP client disconnects -- and runs one
action at a time, since two actions racing on one page would each see the other's
half-finished work. The table releases a session on ``browser_close``, when its
page is lost, when it goes :data:`~web_router.constants.MCP_BROWSER_IDLE_TIMEOUT`
without a call, and, all at once, when the server shuts down. Each session is
also held to a :class:`~web_router.mcp.navigation.NavigationPolicy`, installed on its
page when the first action connects and checked again after every action.
"""

from __future__ import annotations

import contextlib
import importlib.util
import re
import secrets
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar
from urllib.parse import urlsplit

import anyio
import anyio.lowlevel

from web_router.constants import (
    EXTRA_BROWSER,
    MCP_BROWSER_END_CANCELLED,
    MCP_BROWSER_END_IDLE,
    MCP_BROWSER_END_LOST,
    MCP_BROWSER_END_SHUTDOWN,
    MCP_BROWSER_ENDED_LIMIT,
    MCP_BROWSER_IDLE_TIMEOUT,
    MCP_BROWSER_MAX_SESSIONS,
    MCP_BROWSER_OPEN_TIMEOUT,
    MCP_BROWSER_REAP_INTERVAL,
    MCP_BROWSER_REF_PATTERN,
    MCP_BROWSER_SECRET_MIN_CHARS,
    MCP_BROWSER_SESSION_ID_BYTES,
    MCP_BROWSER_SESSION_ID_TEMPLATE,
    MCP_MSG_BROWSER_BAD_REF,
    MCP_MSG_BROWSER_EMPTY_SELECTOR,
    MCP_MSG_BROWSER_ENDED,
    MCP_MSG_BROWSER_ERRORS,
    MCP_MSG_BROWSER_NO_PLAYWRIGHT,
    MCP_MSG_BROWSER_NO_SESSIONS,
    MCP_MSG_BROWSER_ONE_TARGET,
    MCP_MSG_BROWSER_OPEN_TIMED_OUT,
    MCP_MSG_BROWSER_TOO_MANY,
    MCP_MSG_BROWSER_UNKNOWN_SESSION,
    MCP_MSG_BROWSER_URL_REFUSED,
    MCP_MSG_BROWSER_URL_REFUSED_BEFORE,
    MCP_REDACTED,
)
from web_router.exceptions import BrowserActionError, ConfigurationError, PageClosedError, WebRouterError
from web_router.mcp.navigation import NavigationGuard, NavigationPolicy
from web_router.mcp.shaping import redact, redaction_note
from web_router.providers.base import AsyncBrowserHandle

_T = TypeVar("_T")

_REF = re.compile(MCP_BROWSER_REF_PATTERN)


class BrowserToolError(WebRouterError):
    """A browser tool failed. The message is already the model's to read: translated and scrubbed."""


def playwright_installed() -> bool:
    """Whether the ``browser`` extra's Playwright can be imported here, checked without importing it."""
    return importlib.util.find_spec("playwright") is not None


def require_playwright() -> None:
    """Refuse before a session is opened, rather than bill for one no action could drive.

    Raises:
        ConfigurationError: when Playwright is not installed.
    """
    if not playwright_installed():
        raise ConfigurationError(MCP_MSG_BROWSER_NO_PLAYWRIGHT.format(extra=EXTRA_BROWSER))


def check_target(tool: str, ref: Any, selector: Any) -> None:
    """Raise ``ValueError`` unless exactly one of ``ref`` / ``selector`` is given, and it is well formed.

    Checked here rather than left to the handle so a model's malformed ref is
    named as one, rather than surfacing as a stale element it never had.
    """
    given = [name for name, value in (("ref", ref), ("selector", selector)) if value is not None]
    if len(given) != 1:
        raise ValueError(MCP_MSG_BROWSER_ONE_TARGET.format(tool=tool, given=" and ".join(given) or "neither"))
    if ref is not None and not (isinstance(ref, str) and _REF.fullmatch(ref)):
        raise ValueError(MCP_MSG_BROWSER_BAD_REF.format(ref=ref))
    if selector is not None and not (isinstance(selector, str) and selector.strip()):
        raise ValueError(MCP_MSG_BROWSER_EMPTY_SELECTOR)


@dataclass
class _Session:
    session_id: str
    handle: AsyncBrowserHandle
    guard: NavigationGuard
    last_used: float
    lock: anyio.Lock = field(default_factory=anyio.Lock)
    busy: int = 0


class BrowserSessions:
    """The table of open sessions, keyed by an id this server minted, with an idle reaper and a cap."""

    def __init__(
        self,
        *,
        idle_timeout: float = MCP_BROWSER_IDLE_TIMEOUT,
        max_sessions: int = MCP_BROWSER_MAX_SESSIONS,
        reap_interval: float = MCP_BROWSER_REAP_INTERVAL,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """
        Args:
            idle_timeout: Seconds without a call after which a session is released.
            max_sessions: How many sessions may be open, or opening, at once.
            reap_interval: Seconds between sweeps of :meth:`run_reaper`.
            clock: Where "now" comes from, so a test can move it.
        """
        self.idle_timeout = idle_timeout
        self.max_sessions = max_sessions
        self.reap_interval = reap_interval
        self._clock = clock
        self._sessions: dict[str, _Session] = {}
        self._ended: OrderedDict[str, str] = OrderedDict()
        self._opening = 0
        # Every value that opens a session this server ever held. Kept after a
        # session ends: a backend can recycle a port, and an old URL is still
        # not the model's to see.
        self._secrets: set[str] = set()

    def __len__(self) -> int:
        return len(self._sessions)

    def session_ids(self) -> list[str]:
        """The ids of every open session, oldest first."""
        return list(self._sessions)

    def provider_of(self, session_id: str) -> str:
        """The backend serving ``session_id``."""
        return self._get(session_id).handle.provider

    async def open(
        self, opener: Callable[[], Awaitable[AsyncBrowserHandle]], *, timeout: float = MCP_BROWSER_OPEN_TIMEOUT
    ) -> str:
        """Open a session with ``opener``, keep its handle, and return the id the model will hold.

        The open runs shielded from the caller being cancelled, for at most
        ``timeout`` seconds: a client that gives up on the call half way would
        otherwise leave a billed session nobody holds. A cancel that arrives
        during the open is honoured once the session is in the table, by
        releasing it -- the model never learned its id.

        Raises:
            BrowserToolError: when :attr:`max_sessions` are already open, or the
                backend did not answer within ``timeout``.
        """
        await self.reap()
        if len(self._sessions) + self._opening >= self.max_sessions:
            raise BrowserToolError(
                MCP_MSG_BROWSER_TOO_MANY.format(open=len(self._sessions) + self._opening, session_ids=self._listed())
            )
        handle: AsyncBrowserHandle | None = None
        self._opening += 1
        try:
            with anyio.CancelScope(shield=True), anyio.move_on_after(timeout):
                handle = await opener()
        finally:
            self._opening -= 1
        if handle is None:
            raise BrowserToolError(MCP_MSG_BROWSER_OPEN_TIMED_OUT.format(timeout=timeout))
        session_id = MCP_BROWSER_SESSION_ID_TEMPLATE.format(token=secrets.token_urlsafe(MCP_BROWSER_SESSION_ID_BYTES))
        try:
            self._remember_secrets(handle)
            guard = NavigationGuard(NavigationPolicy.for_session(handle.cdp_url))
            self._sessions[session_id] = _Session(session_id, handle, guard, self._clock())
            handle.on_connect(guard.install)
            await anyio.lowlevel.checkpoint()
        except BaseException:
            self._end(session_id, MCP_BROWSER_END_CANCELLED)
            with anyio.CancelScope(shield=True):
                await _release_quietly(handle)
            raise
        return session_id

    def check_url(self, session_id: str, tool: str, url: str) -> None:
        """Refuse ``url`` before ``tool`` sends the session there, if the session's policy does not allow it.

        Raises:
            BrowserToolError: naming why, when the URL is refused.
        """
        reason = self._get(session_id).guard.policy.refusal(url)
        if reason is not None:
            raise BrowserToolError(MCP_MSG_BROWSER_URL_REFUSED_BEFORE.format(tool=tool, reason=reason))

    async def run(self, session_id: str, tool: str, action: Callable[[AsyncBrowserHandle], Awaitable[_T]]) -> _T:
        """Run one page action on ``session_id``'s handle, one at a time per session.

        A page-action failure comes back as a :class:`BrowserToolError` the model
        can act on. One that says the page is gone also releases the session,
        because nothing further can be done with it and it may still be billing.
        """
        session = self._get(session_id)
        session.busy += 1
        try:
            async with session.lock:
                if self._sessions.get(session_id) is not session:
                    # Closed while this call waited its turn.
                    raise BrowserToolError(self._ended_message(session_id))
                try:
                    result = await action(session.handle)
                except PageClosedError as exc:
                    self._end(session_id, MCP_BROWSER_END_LOST)
                    with anyio.CancelScope(shield=True):
                        await _release_quietly(session.handle)
                    raise self._translated(exc, tool, session_id) from None
                except BrowserActionError as exc:
                    # A navigation the route aborted fails as one; the refusal is the truer answer.
                    raise (await self._settled(session, tool) or self._translated(exc, tool, session_id)) from None
                except WebRouterError as exc:
                    raise BrowserToolError(self.scrub_text(str(exc))) from None
                refusal = await self._settled(session, tool)
                if refusal is not None:
                    raise refusal
                return result
        finally:
            session.busy -= 1
            session.last_used = self._clock()

    async def close(self, session_id: str, reason: str) -> str | None:
        """Release ``session_id``. Returns ``None``, or why it had already ended if it had.

        Waits for an action in flight on the session to finish first, so a
        release never pulls the page out from under a click.

        Raises:
            ValueError: for an id this server never handed out.
        """
        session = self._sessions.get(session_id)
        if session is None:
            if session_id in self._ended:
                return self._ended[session_id]
            raise ValueError(self._unknown_message(session_id))
        self._end(session_id, reason)
        async with session.lock:
            try:
                await session.handle.release()
            except WebRouterError as exc:
                raise BrowserToolError(self.scrub_text(str(exc))) from None
        return None

    async def reap(self) -> list[str]:
        """Release every session idle for :attr:`idle_timeout` or longer, and return their ids.

        A session with an action running or waiting to run is never idle, however
        long that action takes.
        """
        now = self._clock()
        idle = [
            session
            for session in self._sessions.values()
            if session.busy == 0 and now - session.last_used >= self.idle_timeout
        ]
        for session in idle:
            self._end(session.session_id, MCP_BROWSER_END_IDLE.format(idle=self.idle_timeout))
        async with anyio.create_task_group() as group:
            for session in idle:
                group.start_soon(_release_quietly, session.handle)
        return [session.session_id for session in idle]

    async def run_reaper(self) -> None:
        """Sweep for idle sessions every :attr:`reap_interval` seconds, until cancelled."""
        while True:
            await anyio.sleep(self.reap_interval)
            await self.reap()

    async def aclose(self) -> None:
        """Release every session still open, all at once and without waiting on their locks.

        Called as the server exits, when an action still in flight is being
        cancelled anyway; one release that fails does not keep the rest open.
        """
        sessions = list(self._sessions.values())
        for session in sessions:
            self._end(session.session_id, MCP_BROWSER_END_SHUTDOWN)
        async with anyio.create_task_group() as group:
            for session in sessions:
                group.start_soon(_release_quietly, session.handle)

    def scrub(self, value: Any) -> tuple[Any, int]:
        """``value`` with every session value this server holds, and every browser-session URL, redacted.

        Literal first -- the CDP URL, its query, the backend's session id and
        live-view URL, wherever they appear -- then :func:`~web_router.mcp.shaping.redact`
        for the ones only a pattern can find.
        """
        literal, literal_count = _replace_literals(value, sorted(self._secrets, key=len, reverse=True))
        redacted, count = redact(literal)
        return redacted, literal_count + count

    def scrub_text(self, text: str) -> str:
        """A message, scrubbed, with a note saying so when anything was taken out."""
        cleaned, count = self.scrub(text)
        return str(cleaned) + redaction_note(count)

    def _get(self, session_id: str) -> _Session:
        session = self._sessions.get(session_id)
        if session is not None:
            return session
        if session_id in self._ended:
            raise BrowserToolError(self._ended_message(session_id))
        raise ValueError(self._unknown_message(session_id))

    def _end(self, session_id: str, reason: str) -> None:
        self._sessions.pop(session_id, None)
        self._ended[session_id] = reason
        while len(self._ended) > MCP_BROWSER_ENDED_LIMIT:
            self._ended.popitem(last=False)

    async def _settled(self, session: _Session, tool: str) -> BrowserToolError | None:
        """The refusal to raise if the action left the page somewhere its policy forbids, after sending it back."""
        with anyio.CancelScope(shield=True):
            reason = await session.guard.settle()
        if reason is None:
            return None
        return BrowserToolError(self.scrub_text(MCP_MSG_BROWSER_URL_REFUSED.format(tool=tool, reason=reason)))

    def _remember_secrets(self, handle: AsyncBrowserHandle) -> None:
        cdp_url = handle.cdp_url
        # The control URL is read from the session, never asked for: the server
        # does not fetch one, but a backend that returns it on open puts it here.
        control_url = handle.session.control_url.get_secret_value() if handle.session.control_url else ""
        candidates = [cdp_url, urlsplit(cdp_url).query, handle.session_id, handle.live_view_url or "", control_url]
        self._secrets.update(value for value in candidates if len(value) >= MCP_BROWSER_SECRET_MIN_CHARS)

    def _translated(self, exc: BrowserActionError, tool: str, session_id: str) -> BrowserToolError:
        template = next(
            MCP_MSG_BROWSER_ERRORS[kind.__name__]
            for kind in type(exc).__mro__
            if kind.__name__ in MCP_MSG_BROWSER_ERRORS
        )
        # From `detail`, never `str(exc)`: the library's own message names the backend's session id.
        return BrowserToolError(self.scrub_text(template.format(tool=tool, detail=exc.detail, session_id=session_id)))

    def _listed(self) -> str:
        return ", ".join(self._sessions) or MCP_MSG_BROWSER_NO_SESSIONS

    def _unknown_message(self, session_id: str) -> str:
        return MCP_MSG_BROWSER_UNKNOWN_SESSION.format(session_id=session_id, session_ids=self._listed())

    def _ended_message(self, session_id: str) -> str:
        return MCP_MSG_BROWSER_ENDED.format(session_id=session_id, reason=self._ended.get(session_id, ""))


async def _release_quietly(handle: AsyncBrowserHandle) -> None:
    # Nothing is waiting on the answer -- the reaper, a lost page, shutdown --
    # and one session that will not end must not keep the others open.
    with contextlib.suppress(Exception):
        await handle.release()


def _replace_literals(value: Any, needles: list[str]) -> tuple[Any, int]:
    if not needles:
        return value, 0
    if isinstance(value, str):
        count = 0
        for needle in needles:
            hits = value.count(needle)
            if hits:
                value = value.replace(needle, MCP_REDACTED)
                count += hits
        return value, count
    if isinstance(value, dict):
        total = 0
        cleaned: dict[Any, Any] = {}
        for key, item in value.items():
            cleaned[key], count = _replace_literals(item, needles)
            total += count
        return cleaned, total
    if isinstance(value, (list, tuple)):
        items = [_replace_literals(item, needles) for item in value]
        cleaned_items = [item for item, _ in items]
        return (tuple(cleaned_items) if isinstance(value, tuple) else cleaned_items), sum(n for _, n in items)
    return value, 0
