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
closed when the call ends; :mod:`.remote_close` closes the pool. httpx is
imported only when the router is built (:mod:`.remote_config`). One request per
call, never retried; no redirect is followed, and no proxy or other setting is
read from the environment. The URL must be ``https``, or ``http`` to
``localhost`` or a loopback address (:mod:`.remote_url`).

A failure is ``router_timeout``, ``router_error`` or ``router_invalid``
(:mod:`.remote_errors`), and the call runs on the static order. Fields the
service adds that this version does not know are ignored; its reasons are cut
short, and an echo of its bearer key in its answer is redacted
(:mod:`.remote_redact`). Router-set URLs are held to
``public_only`` when the caller sets no ``url_policy`` (``min_url_policy``).

A decision record names it ``remote(<scheme>://<host>)``: never the URL's
path, and never the key.
"""

from __future__ import annotations

import asyncio
import json
import threading
import weakref
from collections.abc import Collection
from contextlib import AbstractAsyncContextManager, nullcontext
from typing import TYPE_CHECKING, Any

from web_router._urls import host_of
from web_router.constants_remote import (
    REMOTE_CONTENT_ENCODING_HEADER,
    REMOTE_CONTENT_TYPE_HEADER,
    REMOTE_DISPLAY_NAME,
    REMOTE_HTTP_OK,
    REMOTE_IDENTITY_ENCODING,
    REMOTE_JSON_TYPE,
    REMOTE_MAX_RESPONSE_BYTES,
    REMOTE_MEDIA_PARAMS,
    REMOTE_TIMEOUT_S,
    REMOTE_URL_POLICY,
    REMOTE_WHY_CLOSED,
    REMOTE_WHY_CONTENT_TYPE,
    REMOTE_WHY_DECISION,
    REMOTE_WHY_ENCODING,
    REMOTE_WHY_NOT_JSON,
    REMOTE_WHY_STATUS,
    REMOTE_WHY_TOO_LARGE,
    REMOTE_WHY_TRANSPORT,
)
from web_router.routing.contract import RouteDecision, RouteRequest
from web_router.routing.remote_close import PoolCloser
from web_router.routing.remote_config import bearer_key, checked_timeout, load_httpx, request_headers
from web_router.routing.remote_errors import RemoteRouterError, RemoteRouterInvalid, RemoteRouterTimeout
from web_router.routing.remote_redact import unechoed_decision
from web_router.routing.remote_url import checked_url
from web_router.routing.wire import WireError, WireVersionError, decision_from_wire, request_to_wire

if TYPE_CHECKING:
    import httpx


class RemoteRouter(PoolCloser):
    """A router that asks an HTTP router service: what ``router="remote"`` builds from ``router_config``."""

    min_url_policy = REMOTE_URL_POLICY
    """The URL policy this router's options are held to when the caller sets none."""

    def __init__(
        self,
        *,
        url: str | None = None,
        api_key: str | None = None,
        timeout_s: float = REMOTE_TIMEOUT_S,
        route_tools: Collection[str] | None = None,
    ) -> None:
        """
        Args:
            url: The service's endpoint: ``https``, or ``http`` to ``localhost`` or a loopback address. No
                credentials, query or fragment.
            api_key: Sent as ``Authorization: Bearer <api_key>``; ``WEB_ROUTER_REMOTE_ROUTER_API_KEY``, read now,
                when omitted, and no header when neither is set.
            timeout_s: Seconds to wait for the service, cut to what the call has left. 1 s unless set.
            route_tools: The tools this router is consulted on when the caller passes no ``route_tools``; all
                five unless set. Checked when it is attached.

        Raises:
            ConfigurationError: for httpx not installed, a URL :func:`~.remote_url.checked_url` refuses, a
                ``timeout_s`` that is not a positive number, or an ``api_key`` that is not a string.
        """
        self._httpx = load_httpx()
        self._url = checked_url(url, self._httpx)
        self.host = host_of(self._url)
        self.router_name = REMOTE_DISPLAY_NAME.format(host=self.host)
        self.timeout_s = checked_timeout(timeout_s)
        self.route_tools = route_tools
        key = bearer_key(api_key)
        self._request_headers = request_headers(key)
        self._key = key
        self._lock = threading.Lock()
        self._closed = False
        self._pool: httpx.AsyncClient | None = None
        # Weakly held: a loop that is gone takes its pool's connections with it, and the next loop makes a new one.
        self._owner: weakref.ref[asyncio.AbstractEventLoop] | None = None

    def __repr__(self) -> str:
        return f"RemoteRouter({self.host!r}, timeout_s={self.timeout_s})"

    async def aroute(self, req: RouteRequest) -> RouteDecision:
        """Ask the service: one POST, cancelled by the caller at ``req.deadline_s``."""
        body, failed, timed_out = b"", None, False
        try:
            async with (
                self._client() as client,
                client.stream("POST", self._url, json=request_to_wire(req), timeout=req.deadline_s) as r,
            ):
                self._check_head(r)
                body = await self._read_capped(r)
        except self._httpx.HTTPError as exc:
            failed = type(exc).__name__  # raised below, outside this block: httpx's error names the whole URL
            timed_out = isinstance(exc, self._httpx.TimeoutException)
        if timed_out:
            raise RemoteRouterTimeout(self.host, REMOTE_WHY_TRANSPORT.format(kind=failed))
        return self._decision(body, failed)

    async def _read_capped(self, response: Any) -> bytes:
        """The answer's bytes as sent, refused past the size cap: identity only, so nothing grows past it."""
        chunks: list[bytes] = []
        size = 0
        async for chunk in response.aiter_raw():
            size += len(chunk)
            if size > REMOTE_MAX_RESPONSE_BYTES:
                raise RemoteRouterInvalid(self.host, REMOTE_WHY_TOO_LARGE.format(limit=REMOTE_MAX_RESPONSE_BYTES))
            chunks.append(chunk)
        return b"".join(chunks)

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
            # Made under the lock, so a close that has returned is never followed by a new client.
            return nullcontext(self._pool) if owner is loop else self._new_client()

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
        coding = response.headers.get(REMOTE_CONTENT_ENCODING_HEADER, "").strip().lower()
        if coding not in ("", REMOTE_IDENTITY_ENCODING):  # asked for identity; a compressed answer is not read
            raise RemoteRouterInvalid(self.host, REMOTE_WHY_ENCODING)

    def _decision(self, body: bytes, failed: str | None) -> RouteDecision:
        """The decision ``body`` carries, or the router error: ``failed`` names the transport's error class."""
        if failed is not None:
            raise RemoteRouterError(self.host, REMOTE_WHY_TRANSPORT.format(kind=failed))
        try:
            data = json.loads(body)
        except (ValueError, RecursionError):  # a reply nested deeper than Python parses is no decision either
            raise RemoteRouterInvalid(self.host, REMOTE_WHY_NOT_JSON) from None
        try:
            return decision_from_wire(unechoed_decision(data, self._key) if self._key else data)
        except WireError as exc:
            raise RemoteRouterInvalid(self.host, str(exc)) from None
        except WireVersionError as exc:  # well formed, of another version: a mismatch, so a router error
            raise RemoteRouterError(self.host, str(exc)) from None
        except RecursionError:
            raise RemoteRouterInvalid(self.host, REMOTE_WHY_DECISION) from None
