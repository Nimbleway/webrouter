"""The built-in ``remote`` router: a router service over HTTP, asked with the wire format, schema 1.

Experimental. Opt in by name::

    WebRouter(router="remote", router_config={"url": "https://router.example.com/route", "timeout_s": 1.0})

Each routed call posts its :class:`~web_router.RouteRequest` as JSON
(:func:`~web_router.routing.wire.request_to_wire`) and reads a
:class:`~web_router.RouteDecision` back. The service is shown what any router
is shown: the prompt and the URLs in it, whole, the budget and the candidates,
named ``tool:provider`` -- which say what backends are configured here. No key
value is ever sent: the one credential on the request is the service's own
bearer key, ``router_config["api_key"]`` or ``WEB_ROUTER_REMOTE_ROUTER_API_KEY``,
read once when the router is built.

It is async only (``aroute``), so a call that runs out of time is cancelled
rather than left running: an async caller awaits it on its own loop, and a sync
one on web-router's private loop, each cancelled at the deadline --
``timeout_s`` (1 s unless set), or less when the call's budget or deadline has
less left. Its one httpx connection pool belongs to the event loop that first
used it, so warm calls reuse their connection; every call of a sync caller
runs on the private loop. A call from any other loop gets a client of its own,
closed when the call ends. ``close()`` and ``aclose()`` close the pool on its
own loop, waiting at most 2 s, and leave the router closed: a later call is a
router error and opens nothing. A pool that does not close in time is dropped
with a ``ResourceWarning``. httpx is imported only when the
router is built (the ``remote`` extra installs it). One request per call, never
retried; no redirect is followed, and no proxy or other setting is read from
the environment. The URL must be ``https``, or ``http`` to ``localhost`` or a
loopback address (:mod:`.remote_url`).

A timeout is ``router_timeout``; a transport error or a status other than 200
is ``router_error``; an
answer that is not an ``application/json`` schema-1 decision within 64 KiB --
``schema`` missing or not 1 included -- is ``router_invalid``. Either way the
call runs on the static order and the decision record says why. Fields the
service adds that this version does not know are ignored; its reasons are cut
short. Router-set URLs are held to ``public_only`` when the caller sets no
``url_policy`` (``min_url_policy``).

A decision record names it ``remote(<scheme>://<host>)``: never the URL's
path, and never the key.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import threading
import warnings
import weakref
from concurrent.futures import TimeoutError as FutureTimeout
from contextlib import AbstractAsyncContextManager, nullcontext
from typing import TYPE_CHECKING, Any

from web_router._urls import host_of
from web_router.constants_remote import (
    ENV_REMOTE_ROUTER_API_KEY,
    EXTRA_REMOTE,
    MSG_REMOTE_CLOSE_FAILED,
    MSG_REMOTE_FAILED,
    MSG_REMOTE_KEY,
    MSG_REMOTE_NO_HTTPX,
    MSG_REMOTE_TIMEOUT,
    REMOTE_AUTH_HEADER,
    REMOTE_AUTH_VALUE,
    REMOTE_CLOSE_WAIT_S,
    REMOTE_CONTENT_TYPE,
    REMOTE_CONTENT_TYPE_HEADER,
    REMOTE_DISPLAY_NAME,
    REMOTE_HTTP_OK,
    REMOTE_JSON_TYPE,
    REMOTE_MAX_RESPONSE_BYTES,
    REMOTE_MEDIA_PARAMS,
    REMOTE_TIMEOUT_S,
    REMOTE_URL_POLICY,
    REMOTE_WHY_CLOSE_LOOP,
    REMOTE_WHY_CLOSE_SLOW,
    REMOTE_WHY_CLOSED,
    REMOTE_WHY_CONTENT_TYPE,
    REMOTE_WHY_DECISION,
    REMOTE_WHY_NOT_JSON,
    REMOTE_WHY_STATUS,
    REMOTE_WHY_TOO_LARGE,
    REMOTE_WHY_TRANSPORT,
)
from web_router.exceptions import ConfigurationError, WebRouterError
from web_router.routing._runner import InvalidAnswer, TimedOut
from web_router.routing.contract import RouteDecision, RouteRequest
from web_router.routing.remote_url import checked_url
from web_router.routing.wire import WireError, decision_from_wire, request_to_wire

if TYPE_CHECKING:
    import httpx


class RemoteRouterError(WebRouterError):
    """The router service gave no usable decision. It names the service by scheme and host, never its key."""

    def __init__(self, host: str, why: str) -> None:
        self.host = host
        self.why = why
        super().__init__(MSG_REMOTE_FAILED.format(host=host, why=why))


class RemoteRouterTimeout(RemoteRouterError, TimedOut):
    """The service did not answer within the call's deadline: recorded as ``router_timeout``."""


class RemoteRouterInvalid(RemoteRouterError, InvalidAnswer):
    """The service answered, but not with a schema-1 decision: recorded as ``router_invalid``."""


def _httpx() -> Any:
    try:
        import httpx
    except ImportError as exc:
        raise ConfigurationError(MSG_REMOTE_NO_HTTPX.format(extra=EXTRA_REMOTE)) from exc
    return httpx


def _checked_timeout(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not 0 < value < math.inf:
        raise ConfigurationError(MSG_REMOTE_TIMEOUT.format(value=value))
    return float(value)


def _auth_headers(api_key: object) -> dict[str, str]:
    key = os.environ.get(ENV_REMOTE_ROUTER_API_KEY) if api_key is None else api_key
    if key is not None and not isinstance(key, str):
        raise ConfigurationError(MSG_REMOTE_KEY)
    return {**REMOTE_CONTENT_TYPE, **({REMOTE_AUTH_HEADER: REMOTE_AUTH_VALUE.format(key=key)} if key else {})}


def _close_failed(host: str, why: str) -> None:
    """Record a pool that did not close: the library logs nothing, so it warns, as a leaked browser session does."""
    warnings.warn(MSG_REMOTE_CLOSE_FAILED.format(host=host, why=why), ResourceWarning, stacklevel=3)


def _why(exc: BaseException) -> str:
    """A close failure by its class only: an httpx error's text names the whole URL."""
    late = isinstance(exc, (TimeoutError, asyncio.TimeoutError, FutureTimeout))  # three classes on Python 3.10
    return REMOTE_WHY_CLOSE_SLOW.format(wait=REMOTE_CLOSE_WAIT_S) if late else type(exc).__name__


_CLOSING: set[asyncio.Task[None]] = set()
"""Pool closes ``close()`` started as tasks on the pool's own running loop, until each is done."""


class RemoteRouter:
    """A router that asks an HTTP router service: what ``router="remote"`` builds from ``router_config``."""

    min_url_policy = REMOTE_URL_POLICY
    """The URL policy this router's options are held to when the caller sets none."""

    def __init__(
        self, *, url: str | None = None, api_key: str | None = None, timeout_s: float = REMOTE_TIMEOUT_S
    ) -> None:
        """
        Args:
            url: The service's endpoint: ``https``, or ``http`` to ``localhost`` or a loopback address. No
                credentials, query or fragment.
            api_key: Sent as ``Authorization: Bearer <api_key>``; ``WEB_ROUTER_REMOTE_ROUTER_API_KEY``, read now,
                when omitted, and no header when neither is set.
            timeout_s: Seconds to wait for the service, cut to what the call has left. 1 s unless set.

        Raises:
            ConfigurationError: for httpx not installed, a URL :func:`~.remote_url.checked_url` refuses, a
                ``timeout_s`` that is not a positive number, or an ``api_key`` that is not a string.
        """
        self._httpx = _httpx()
        self._url = checked_url(url, self._httpx)
        self.host = host_of(self._url)
        self.router_name = REMOTE_DISPLAY_NAME.format(host=self.host)
        self.timeout_s = _checked_timeout(timeout_s)
        self._request_headers = _auth_headers(api_key)
        self._lock = threading.Lock()
        self._closed = False
        self._pool: httpx.AsyncClient | None = None
        # Weakly held: a loop that is gone takes its pool's connections with it, and the next loop makes a new one.
        self._owner: weakref.ref[asyncio.AbstractEventLoop] | None = None

    def __repr__(self) -> str:
        return f"RemoteRouter({self.host!r}, timeout_s={self.timeout_s})"

    async def aroute(self, req: RouteRequest) -> RouteDecision:
        """Ask the service: one POST, cancelled by the caller at ``req.deadline_s``."""
        chunks: list[bytes] = []
        failed, timed_out = None, False
        try:
            async with (
                self._client() as client,
                client.stream("POST", self._url, json=request_to_wire(req), timeout=req.deadline_s) as r,
            ):
                self._check_head(r)
                size = 0
                async for chunk in r.aiter_bytes():
                    size += len(chunk)
                    if size > REMOTE_MAX_RESPONSE_BYTES:
                        raise RemoteRouterInvalid(
                            self.host, REMOTE_WHY_TOO_LARGE.format(limit=REMOTE_MAX_RESPONSE_BYTES)
                        )
                    chunks.append(chunk)
        except self._httpx.HTTPError as exc:
            failed = type(exc).__name__  # raised below, outside this block: httpx's error names the whole URL
            timed_out = isinstance(exc, self._httpx.TimeoutException)
        if timed_out:
            raise RemoteRouterTimeout(self.host, REMOTE_WHY_TRANSPORT.format(kind=failed))
        return self._decision(b"".join(chunks), failed)

    def close(self) -> None:
        """Close the pool on its own loop, waiting at most 2 s, and the router with it.

        From the pool's own running loop it cannot wait, so the close runs there as a task: use ``aclose()``.
        """
        pool, loop = self._shut()
        if pool is None or loop is None or loop.is_closed():
            return  # nothing opened, or its loop is gone and took the connections with it
        try:
            here: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            here = None
        if loop is here:
            task = loop.create_task(pool.aclose())
            _CLOSING.add(task)  # held until done: a loop keeps only a weak reference to a task
            task.add_done_callback(self._closed_task)
            return
        if not loop.is_running():
            _close_failed(self.host, REMOTE_WHY_CLOSE_LOOP)
            return
        closing = asyncio.run_coroutine_threadsafe(pool.aclose(), loop)
        try:
            closing.result(REMOTE_CLOSE_WAIT_S)
        except Exception as exc:
            closing.cancel()
            _close_failed(self.host, _why(exc))

    async def aclose(self) -> None:
        """Close the pool and the router: awaited here, or on its own loop without blocking this one, 2 s at most."""
        pool, loop = self._shut()
        if pool is None or loop is None or loop.is_closed():
            return
        try:
            if loop is asyncio.get_running_loop():
                await pool.aclose()
            elif not loop.is_running():
                _close_failed(self.host, REMOTE_WHY_CLOSE_LOOP)
            else:
                closing = asyncio.wrap_future(asyncio.run_coroutine_threadsafe(pool.aclose(), loop))
                await asyncio.wait_for(closing, REMOTE_CLOSE_WAIT_S)  # cancels the close on its loop when late
        except Exception as exc:
            _close_failed(self.host, _why(exc))

    def _shut(self) -> tuple[httpx.AsyncClient | None, asyncio.AbstractEventLoop | None]:
        """Mark the router closed and take its pool, with the loop it belongs to: no call after this opens one."""
        with self._lock:
            self._closed = True
            pool, owner = self._pool, self._owner
            self._pool = self._owner = None
        return pool, None if owner is None else owner()

    def _closed_task(self, task: asyncio.Task[None]) -> None:
        _CLOSING.discard(task)
        failure = None if task.cancelled() else task.exception()
        if failure is not None:
            _close_failed(self.host, _why(failure))

    def _client(self) -> AbstractAsyncContextManager[httpx.AsyncClient]:
        """The pool, on the loop it belongs to; on another loop, a client for this call alone, closed after it.

        A pool is bound to its loop, so one loop's calls never use or close another's under it.

        Raises:
            RemoteRouterError: once the router is closed.
        """
        loop = asyncio.get_running_loop()
        with self._lock:
            if self._closed:
                raise RemoteRouterError(self.host, REMOTE_WHY_CLOSED)
            owner = None if self._owner is None else self._owner()
            if self._pool is None or owner is None or owner.is_closed():
                self._pool, self._owner, owner = self._new_client(), weakref.ref(loop), loop
            if owner is loop:
                return nullcontext(self._pool)
        return self._new_client()

    def _new_client(self) -> httpx.AsyncClient:
        # No redirect followed, so a 3xx is a router error and the key goes nowhere else; no proxy, netrc or
        # certificate setting read from the environment, so no HTTP_PROXY ever sees the key.
        client: httpx.AsyncClient = self._httpx.AsyncClient(
            headers=self._request_headers, follow_redirects=False, trust_env=False
        )
        return client

    def _check_head(self, response: Any) -> None:
        if response.status_code != REMOTE_HTTP_OK:
            raise RemoteRouterError(self.host, REMOTE_WHY_STATUS.format(status=response.status_code))
        header = response.headers.get(REMOTE_CONTENT_TYPE_HEADER, "")
        media = header.partition(REMOTE_MEDIA_PARAMS)[0].strip().lower()
        if media != REMOTE_JSON_TYPE:
            raise RemoteRouterInvalid(self.host, REMOTE_WHY_CONTENT_TYPE)

    def _decision(self, body: bytes, failed: str | None) -> RouteDecision:
        """The decision ``body`` carries, or the router error: ``failed`` names the transport's error class."""
        if failed is not None:
            raise RemoteRouterError(self.host, REMOTE_WHY_TRANSPORT.format(kind=failed))
        try:
            data = json.loads(body)
        except (ValueError, RecursionError):  # a reply nested deeper than Python parses is no decision either
            raise RemoteRouterInvalid(self.host, REMOTE_WHY_NOT_JSON) from None
        try:
            return decision_from_wire(data)
        except WireError as exc:
            raise RemoteRouterInvalid(self.host, str(exc)) from None
        except RecursionError:
            raise RemoteRouterInvalid(self.host, REMOTE_WHY_DECISION) from None
