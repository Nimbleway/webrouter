"""The MCP server over streamable HTTP, for a team that runs one shared server.

The tools, handlers and shutdown are :mod:`web_router.mcp.server`'s; this adds the three things a network
listener needs that a stdio pipe does not:

- It binds to loopback unless ``--host`` names another address, and then refuses to start without a bearer
  token (:data:`~web_router.constants_mcp_http.ENV_MCP_TOKEN`), so a shared server is never open to anyone
  who can reach it.
- With a token set, every request must carry it, compared in constant time.
- ``load_env`` is off unless the operator turned it on, so a client cannot point the server at files on the
  host it runs on. Keys come from the server's own environment.

Every client shares one :class:`~web_router.mcp.tools.WebRouterTools`: one router, one budget, one set of
browser sessions.
"""

from __future__ import annotations

import hmac
import ipaddress
import os
import sys
from collections.abc import MutableMapping
from dataclasses import dataclass, field
from functools import partial

import anyio
import uvicorn
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from web_router.constants_mcp_env import ENV_MCP_LOAD_ENV, MCP_LOAD_ENV_OFF
from web_router.constants_mcp_http import (
    ENV_MCP_TOKEN,
    MCP_HTTP_AUTHORIZATION,
    MCP_HTTP_BEARER,
    MCP_HTTP_CHALLENGE,
    MCP_HTTP_LOCALHOST,
    MCP_HTTP_PATH,
    MCP_HTTP_SHUTDOWN_GRACE,
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
            ConfigurationError: for an address other than loopback with no token.
        """
        token = (token or "").strip() or None
        if token is None and not is_loopback(host):
            raise ConfigurationError(MCP_MSG_HTTP_NEEDS_TOKEN.format(host=host, env=ENV_MCP_TOKEN))
        return cls(host=host, port=port, token=token)


def is_loopback(host: str) -> bool:
    """Whether ``host`` is ``localhost`` or a loopback IP address; a name that needs DNS is not."""
    if host.lower() == MCP_HTTP_LOCALHOST:
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def http_env_defaults(environ: MutableMapping[str, str] = os.environ) -> None:
    """Turn ``load_env`` off unless the operator set :data:`ENV_MCP_LOAD_ENV` themselves."""
    if not environ.get(ENV_MCP_LOAD_ENV, "").strip():
        environ[ENV_MCP_LOAD_ENV] = MCP_LOAD_ENV_OFF


class BearerAuth:
    """ASGI middleware that answers 401 to any HTTP request without ``Authorization: Bearer <token>``."""

    def __init__(self, app: ASGIApp, token: str) -> None:
        self._app = app
        self._token = token.encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and not self._authorized(scope):
            response = JSONResponse({"error": MCP_MSG_HTTP_UNAUTHORIZED}, status_code=401, headers=MCP_HTTP_CHALLENGE)
            await response(scope, receive, send)
            return
        await self._app(scope, receive, send)

    def _authorized(self, scope: Scope) -> bool:
        header = dict(scope.get("headers", [])).get(MCP_HTTP_AUTHORIZATION, b"")
        scheme, _, presented = header.partition(b" ")
        # compare_digest takes as long whatever the bytes, so a wrong token says nothing about the right one.
        return scheme.lower() == MCP_HTTP_BEARER.encode() and hmac.compare_digest(presented.strip(), self._token)


def build_app(tools: WebRouterTools, settings: HttpSettings) -> ASGIApp:
    """The server's streamable HTTP app at :data:`MCP_HTTP_PATH`, behind the token when one is set.

    On a loopback host the SDK also checks ``Host`` and ``Origin``, so a web page cannot reach it through
    DNS rebinding.
    """
    app = build_server(tools).streamable_http_app(streamable_http_path=MCP_HTTP_PATH, host=settings.host)
    return BearerAuth(app, settings.token) if settings.token else app


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
        async with anyio.create_task_group() as group:
            group.start_soon(tools.browsers.run_reaper)
            await uvicorn.Server(config).serve()  # uvicorn handles SIGINT and SIGTERM itself
            group.cancel_scope.cancel()
    finally:
        await _shutdown(tools)


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
