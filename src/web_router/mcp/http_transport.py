"""The MCP server over streamable HTTP, for a team that runs one shared server.

The tools, handlers and shutdown are :mod:`web_router.mcp.server`'s; this adds the three things a network
listener needs that a stdio pipe does not:

- It binds to ``127.0.0.1`` unless ``--host`` names another; off the tokenless hosts it refuses to start
  without a bearer token (:data:`~web_router.constants_mcp_http.ENV_MCP_TOKEN`), so a shared server is
  never open to anyone who can reach it.
- With a token set, every request must carry it, compared in constant time.
- ``load_env`` and the browser's private addresses are off unless the operator turned them on, so a client
  cannot reach the server's files or its own network. Keys come from the server's own environment.

Every client shares one :class:`~web_router.mcp.tools.WebRouterTools`: one router, one budget, one set of
browser sessions.
"""

from __future__ import annotations

import hmac
import os
import sys
from collections.abc import AsyncIterator, MutableMapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from functools import partial
from typing import Any

import anyio
import uvicorn
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Lifespan, Receive, Scope, Send

from web_router.constants_mcp_env import (
    ENV_MCP_LOAD_ENV,
    ENV_MCP_PRIVATE_URLS,
    MCP_LOAD_ENV_OFF,
    MCP_PRIVATE_URLS_OFF,
)
from web_router.constants_mcp_http import (
    ENV_MCP_TOKEN,
    MCP_HTTP_AUTHORIZATION,
    MCP_HTTP_BEARER,
    MCP_HTTP_CHALLENGE,
    MCP_HTTP_PATH,
    MCP_HTTP_SHUTDOWN_GRACE,
    MCP_HTTP_TOKENLESS_HOSTS,
    MCP_MSG_HTTP_NEEDS_TOKEN,
    MCP_MSG_HTTP_UNAUTHORIZED,
)
from web_router.exceptions import ConfigurationError
from web_router.mcp.env import EnvSources
from web_router.mcp.routing_config import McpRouting
from web_router.mcp.server import _shutdown, build_server
from web_router.mcp.tools import WebRouterTools


@dataclass(frozen=True)
class HttpSettings:
    """Where the server listens and the token it requires, if any."""

    host: str
    port: int
    token: str | None = field(default=None, repr=False)  # a secret: never in a repr or a traceback

    @classmethod
    def resolve(cls, host: str, port: int, token: str | None) -> HttpSettings:
        """Settings for ``host`` and ``port``, with a blank ``token`` read as none.

        Raises:
            ConfigurationError: for a host other than the tokenless ones with no token.
        """
        token = (token or "").strip() or None
        if token is None and not tokenless_host(host):
            raise ConfigurationError(MCP_MSG_HTTP_NEEDS_TOKEN.format(host=host, env=ENV_MCP_TOKEN))
        return cls(host=host, port=port, token=token)


def tokenless_host(host: str) -> bool:
    """Whether the server may listen on ``host`` without a token: only where the SDK guards against DNS rebinding.

    Narrower than loopback: ``127.0.0.2``, ``LOCALHOST`` or ``::ffff:127.0.0.1`` also bind to this machine,
    but the SDK checks ``Host`` and ``Origin`` only for these three spellings, so a web page could reach a
    tokenless server on any other. So this is not the package's ``is_loopback``, which answers another question.
    """
    return host in MCP_HTTP_TOKENLESS_HOSTS


HTTP_OFF_SWITCHES = ((ENV_MCP_LOAD_ENV, MCP_LOAD_ENV_OFF), (ENV_MCP_PRIVATE_URLS, MCP_PRIVATE_URLS_OFF))
"""The switches HTTP turns off unless the operator set them, each with its off value."""


def http_env_defaults(environ: MutableMapping[str, str] = os.environ) -> None:
    """Turn ``load_env`` and the browser's private addresses off, each unless the operator set it themselves.

    Over HTTP both would reach the server's machine, not the user's: its files, and its own network.
    """
    for name, off in HTTP_OFF_SWITCHES:
        if not environ.get(name, "").strip():
            environ[name] = off


class BearerAuth:
    """ASGI middleware that answers 401 to any HTTP request without ``Authorization: Bearer <token>``."""

    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self._token = token.encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and not self._authorized(scope):
            response = JSONResponse({"error": MCP_MSG_HTTP_UNAUTHORIZED}, status_code=401, headers=MCP_HTTP_CHALLENGE)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)

    def _authorized(self, scope: Scope) -> bool:
        header = dict(scope.get("headers", [])).get(MCP_HTTP_AUTHORIZATION, b"")
        scheme, _, presented = header.partition(b" ")
        # compare_digest takes as long whatever the bytes, so a wrong token says nothing about the right one.
        return scheme.lower() == MCP_HTTP_BEARER.encode() and hmac.compare_digest(presented.strip(), self._token)


def build_app(tools: WebRouterTools, settings: HttpSettings) -> ASGIApp:
    """The server's streamable HTTP app at :data:`MCP_HTTP_PATH`, behind the token when one is set.

    Without a token -- only on the tokenless hosts -- the SDK checks ``Host`` and ``Origin``, so a web page
    cannot reach it through DNS rebinding. With one, the token is the guard (such a page cannot send it), and
    the check is off, so a reverse proxy may forward its own public hostname.
    """
    server = build_server(tools, remote=True)  # the client is elsewhere: no local window, keys from the operator
    security = TransportSecuritySettings(enable_dns_rebinding_protection=False) if settings.token else None
    app = server.streamable_http_app(
        streamable_http_path=MCP_HTTP_PATH, host=settings.host, transport_security=security
    )
    app.router.lifespan_context = _releasing(app.router.lifespan_context, tools)
    return BearerAuth(app, settings.token) if settings.token else app


def _releasing(lifespan: Lifespan[Any], tools: WebRouterTools) -> Lifespan[Any]:
    """``lifespan``, with the idle-browser reaper running inside it, then every session and provider released.

    Uvicorn re-raises a SIGTERM or SIGINT once ``serve()`` returns, which can end the process before any
    code after it runs; its lifespan shutdown runs before that, so the release happens here. The reaper is
    stopped first, as stdio stops it, so it is not releasing a session while the providers close.
    """

    @asynccontextmanager
    async def run(app: Any) -> AsyncIterator[None]:
        try:
            async with lifespan(app), anyio.create_task_group() as group:
                group.start_soon(tools.browsers.run_reaper)
                yield
                group.cancel_scope.cancel()
        finally:
            await _shutdown(tools)

    return run


async def serve_http(
    settings: HttpSettings,
    tools: WebRouterTools | None = None,
    *,
    env: EnvSources | None = None,
    routing: McpRouting | None = None,
) -> None:
    """Serve HTTP until a shutdown signal, then release every browser session and close every provider."""
    tools = tools if tools is not None else WebRouterTools(env=env, routing=routing)
    config = uvicorn.Config(
        build_app(tools, settings),
        host=settings.host,
        port=settings.port,
        lifespan="on",
        timeout_graceful_shutdown=MCP_HTTP_SHUTDOWN_GRACE,  # an open stream must not hold the browsers open
    )
    try:
        await uvicorn.Server(config).serve()  # uvicorn handles SIGINT and SIGTERM itself
    finally:
        await _shutdown(tools)  # again, for a server that stopped before its lifespan ran


def main(host: str, port: int) -> None:
    """Check the bind against the token, turn ``load_env`` off by default, then serve until stopped.

    A refused bind prints why and exits with status 2, before any key is read.
    """
    try:
        settings = HttpSettings.resolve(host, port, os.environ.get(ENV_MCP_TOKEN))
    except ConfigurationError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(2) from None
    http_env_defaults()
    routing = McpRouting.from_environ()  # router keys before .env, as stdio reads them
    anyio.run(partial(serve_http, settings, routing=routing, env=EnvSources.at_startup()))
