"""The built-in ``remote`` router: a router service over HTTP, asked with the wire format, schema 1.

Experimental. Opt in by name::

    WebRouter(router="remote", router_config={"url": "https://router.example.com/route", "timeout_s": 1.0})

Each routed call posts its :class:`~web_router.RouteRequest` as JSON
(:func:`~web_router.routing.wire.request_to_wire`) and reads a
:class:`~web_router.RouteDecision` back. The service is shown what any router
is shown: the prompt, the URLs in it, the budget and the candidates, named
``tool:provider`` -- which say what backends are configured here. No key value
is ever sent: the one credential on the request is the service's own bearer
key, ``router_config["api_key"]`` or ``WEB_ROUTER_REMOTE_ROUTER_API_KEY``.

The transport is httpx, imported only when this router is built (the
``remote`` extra installs it). One request per call, never retried, waiting
``timeout_s`` (1 s unless set) or less when the call's budget or deadline has
less left; no redirect is followed. The URL must be ``https``, or ``http`` to
a loopback host. An error, a timeout, a status other than 200, an answer that
is not JSON, or one of another wire-schema major version is a router error:
the call runs on the static order and the decision record says so. Fields the
service adds that this version does not know are ignored.

A decision record names it ``remote(<scheme>://<host>)``: never the URL's
path, query or credentials, and never the key.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import threading
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from web_router._hosts import is_loopback
from web_router._urls import host_of
from web_router.constants_remote import (
    ENV_REMOTE_ROUTER_API_KEY,
    EXTRA_REMOTE,
    MSG_REMOTE_FAILED,
    MSG_REMOTE_HTTPS,
    MSG_REMOTE_KEY,
    MSG_REMOTE_NO_HTTPX,
    MSG_REMOTE_NO_URL,
    MSG_REMOTE_TIMEOUT,
    MSG_REMOTE_URL,
    REMOTE_AUTH_HEADER,
    REMOTE_AUTH_VALUE,
    REMOTE_CONTENT_TYPE,
    REMOTE_DISPLAY_NAME,
    REMOTE_HTTP_OK,
    REMOTE_LOOPBACK_SCHEME,
    REMOTE_MAX_RESPONSE_BYTES,
    REMOTE_SECURE_SCHEME,
    REMOTE_TIMEOUT_S,
    REMOTE_WHY_NOT_JSON,
    REMOTE_WHY_STATUS,
    REMOTE_WHY_TOO_LARGE,
    REMOTE_WHY_TRANSPORT,
)
from web_router.exceptions import ConfigurationError, WebRouterError
from web_router.routing.contract import RouteDecision, RouteRequest
from web_router.routing.wire import WireError, decision_from_wire, request_to_wire

if TYPE_CHECKING:
    import httpx


class RemoteRouterError(WebRouterError):
    """The router service gave no usable decision. It names the service by scheme and host, never its key."""

    def __init__(self, host: str, why: str) -> None:
        self.host = host
        self.why = why
        super().__init__(MSG_REMOTE_FAILED.format(host=host, why=why))


def _httpx() -> Any:
    try:
        import httpx
    except ImportError as exc:
        raise ConfigurationError(MSG_REMOTE_NO_HTTPX.format(extra=EXTRA_REMOTE)) from exc
    return httpx


def _checked_url(url: object) -> str:
    """``url`` when it is ``https``, or ``http`` to a loopback host.

    Raises:
        ConfigurationError: for no URL, one with no host, or a plain-``http`` one to any other host.
    """
    if not isinstance(url, str) or not url.strip():
        raise ConfigurationError(MSG_REMOTE_NO_URL)
    shown = host_of(url)
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:
        raise ConfigurationError(MSG_REMOTE_URL.format(host=shown)) from None
    scheme = parts.scheme.lower()
    if not host or scheme not in (REMOTE_SECURE_SCHEME, REMOTE_LOOPBACK_SCHEME):
        raise ConfigurationError(MSG_REMOTE_URL.format(host=shown))
    if scheme != REMOTE_SECURE_SCHEME and not is_loopback(host):
        raise ConfigurationError(MSG_REMOTE_HTTPS.format(host=shown))
    return url


def _checked_timeout(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not 0 < value < math.inf:
        raise ConfigurationError(MSG_REMOTE_TIMEOUT.format(value=value))
    return float(value)


def _auth_headers(api_key: object) -> dict[str, str]:
    key = os.environ.get(ENV_REMOTE_ROUTER_API_KEY) if api_key is None else api_key
    if key is not None and not isinstance(key, str):
        raise ConfigurationError(MSG_REMOTE_KEY)
    return {**REMOTE_CONTENT_TYPE, **({REMOTE_AUTH_HEADER: REMOTE_AUTH_VALUE.format(key=key)} if key else {})}


class RemoteRouter:
    """A router that asks an HTTP router service: what ``router="remote"`` builds from ``router_config``."""

    def __init__(
        self, *, url: str | None = None, api_key: str | None = None, timeout_s: float = REMOTE_TIMEOUT_S
    ) -> None:
        """
        Args:
            url: The service's endpoint: ``https``, or ``http`` to a loopback host.
            api_key: Sent as ``Authorization: Bearer <api_key>``; ``WEB_ROUTER_REMOTE_ROUTER_API_KEY`` when
                omitted, and no header when neither is set.
            timeout_s: Seconds to wait for the service, cut to what the call has left. 1 s unless set.

        Raises:
            ConfigurationError: for httpx not installed, a URL that is not ``https`` (or ``http`` to a loopback
                host), a ``timeout_s`` that is not a positive number, or an ``api_key`` that is not a string.
        """
        self._httpx = _httpx()
        self._url = _checked_url(url)
        self.host = host_of(self._url)
        self.router_name = REMOTE_DISPLAY_NAME.format(host=self.host)
        self.timeout_s = _checked_timeout(timeout_s)
        self._request_headers = _auth_headers(api_key)
        self._lock = threading.Lock()
        self._client: httpx.Client | None = None
        self._aclient: httpx.AsyncClient | None = None
        self._aloop: asyncio.AbstractEventLoop | None = None

    def __repr__(self) -> str:
        return f"RemoteRouter({self.host!r}, timeout_s={self.timeout_s})"

    def route(self, req: RouteRequest) -> RouteDecision:
        """Ask the service from sync code: one POST, within ``req.deadline_s``."""
        body, failed = b"", None
        try:
            client = self._sync_client()
            with client.stream("POST", self._url, json=request_to_wire(req), timeout=req.deadline_s) as r:
                body = self._capped(r.status_code, r.iter_bytes())
        except self._httpx.HTTPError as exc:
            failed = type(exc).__name__  # raised below, outside this block: httpx's error names the whole URL
        return self._decision(body, failed)

    async def aroute(self, req: RouteRequest) -> RouteDecision:
        """Ask the service from async code: one POST, within ``req.deadline_s``."""
        chunks: list[bytes] = []
        failed = None
        try:
            client = self._async_client()
            async with client.stream("POST", self._url, json=request_to_wire(req), timeout=req.deadline_s) as r:
                chunks = [chunk async for chunk in self._acapped(r.status_code, r.aiter_bytes())]
        except self._httpx.HTTPError as exc:
            failed = type(exc).__name__  # as in route()
        return self._decision(b"".join(chunks), failed)

    def close(self) -> None:
        """Close the sync connection pool; the async one is dropped, since only its own loop may close it."""
        with self._lock:
            client, self._client = self._client, None
            self._aclient = self._aloop = None
        if client is not None:
            client.close()

    async def aclose(self) -> None:
        """Close both connection pools: the async one when it was opened on this loop."""
        with self._lock:
            aclient, loop = self._aclient, self._aloop
        if aclient is not None and loop is asyncio.get_running_loop():
            await aclient.aclose()
        self.close()

    def _sync_client(self) -> httpx.Client:
        with self._lock:
            if self._client is None:
                self._client = self._httpx.Client(headers=self._request_headers, follow_redirects=False)
            return self._client

    def _async_client(self) -> httpx.AsyncClient:
        """This loop's pool: an async client is bound to the loop it first ran on, so another loop gets its own."""
        loop = asyncio.get_running_loop()
        with self._lock:
            if self._aclient is None or self._aloop is not loop:
                self._aclient = self._httpx.AsyncClient(headers=self._request_headers, follow_redirects=False)
                self._aloop = loop
            return self._aclient

    def _capped(self, status: int, chunks: Any) -> bytes:
        self._check_status(status)
        body = bytearray()
        for chunk in chunks:
            body += chunk
            self._check_size(len(body))
        return bytes(body)

    async def _acapped(self, status: int, chunks: Any) -> Any:
        self._check_status(status)
        size = 0
        async for chunk in chunks:
            size += len(chunk)
            self._check_size(size)
            yield chunk

    def _check_status(self, status: int) -> None:
        if status != REMOTE_HTTP_OK:
            raise RemoteRouterError(self.host, REMOTE_WHY_STATUS.format(status=status))

    def _check_size(self, size: int) -> None:
        if size > REMOTE_MAX_RESPONSE_BYTES:
            raise RemoteRouterError(self.host, REMOTE_WHY_TOO_LARGE.format(limit=REMOTE_MAX_RESPONSE_BYTES))

    def _decision(self, body: bytes, failed: str | None) -> RouteDecision:
        """The decision ``body`` carries, or the router error: ``failed`` names the transport's error class."""
        if failed is not None:
            raise RemoteRouterError(self.host, REMOTE_WHY_TRANSPORT.format(kind=failed))
        try:
            data = json.loads(body)
        except ValueError:
            raise RemoteRouterError(self.host, REMOTE_WHY_NOT_JSON) from None
        try:
            return decision_from_wire(data)
        except WireError as exc:
            raise RemoteRouterError(self.host, str(exc)) from None
