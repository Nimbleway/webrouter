"""The HTTP transport's three guards: the token on every request, no open bind off loopback, no ``load_env``."""

from __future__ import annotations

import os
from typing import Any

import anyio
import httpx
import pytest
from starlette.types import Receive, Scope, Send

from tests.mcp.conftest import FAKE
from tests.mcp.http_helpers import initialize
from web_router.client import AsyncWebRouter
from web_router.constants import MCP_TOOL_LOAD_ENV
from web_router.constants_mcp_env import ENV_MCP_LOAD_ENV, MCP_LOAD_ENV_LOAD, MCP_LOAD_ENV_OFF
from web_router.constants_mcp_http import (
    ENV_MCP_TOKEN,
    MCP_HTTP_PATH,
    MCP_HTTP_SHUTDOWN_GRACE,
    MCP_HTTP_TOKENLESS_HOSTS,
)
from web_router.exceptions import ConfigurationError
from web_router.mcp import http_transport
from web_router.mcp.env_policy import switch
from web_router.mcp.http_transport import BearerAuth, HttpSettings, build_app, http_env_defaults, serve_http
from web_router.mcp.tools import WebRouterTools

TOKEN = "s3cret-token"


async def _ok(scope: Scope, receive: Receive, send: Send) -> None:
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


async def _status(app: object, headers: dict[str, str]) -> int:
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
        return (await client.post(MCP_HTTP_PATH, headers=headers, json={})).status_code


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({}, 401),
        ({"Authorization": "Bearer wrong"}, 401),
        ({"Authorization": f"Basic {TOKEN}"}, 401),
        ({"Authorization": f"Bearer {TOKEN}x"}, 401),
        ({"Authorization": f"Bearer {TOKEN}"}, 200),
        ({"Authorization": f"bearer {TOKEN}"}, 200),
    ],
)
async def test_only_the_right_bearer_token_gets_through(headers: dict[str, str], expected: int) -> None:
    assert await _status(BearerAuth(_ok, TOKEN), headers) == expected


async def test_the_served_app_requires_the_token(tools: WebRouterTools) -> None:
    app = build_app(tools, HttpSettings.resolve("0.0.0.0", 8000, TOKEN))

    assert await _status(app, {}) == 401


@pytest.mark.parametrize(
    "host",
    [
        "0.0.0.0",
        "::",
        "192.168.1.5",
        "example.com",
        "127.0.0.2",
        "LOCALHOST",
        "[::1]",
        "::ffff:127.0.0.1",
        "::ffff:7f00:1",
    ],
)
@pytest.mark.parametrize("token", [None, "", "   "])
def test_an_address_off_loopback_needs_a_token(host: str, token: str | None) -> None:
    with pytest.raises(ConfigurationError, match=ENV_MCP_TOKEN):
        HttpSettings.resolve(host, 8000, token)


@pytest.mark.parametrize("host", sorted(MCP_HTTP_TOKENLESS_HOSTS))
def test_loopback_runs_without_a_token(host: str) -> None:
    assert HttpSettings.resolve(host, 8000, None).token is None


def test_load_env_is_off_under_http_unless_the_operator_set_it(
    monkeypatch: pytest.MonkeyPatch, stdio_switches: None
) -> None:
    http_env_defaults()
    assert switch(ENV_MCP_LOAD_ENV) == MCP_LOAD_ENV_OFF

    monkeypatch.setenv(ENV_MCP_LOAD_ENV, MCP_LOAD_ENV_LOAD)
    http_env_defaults()
    assert os.environ[ENV_MCP_LOAD_ENV] == MCP_LOAD_ENV_LOAD


def test_an_http_session_does_not_list_load_env(stdio_switches: None, tools: WebRouterTools) -> None:
    assert MCP_TOOL_LOAD_ENV in {tool.name for tool in tools.list_tools()}

    http_env_defaults()  # before the session is built, as the command runs it

    served = WebRouterTools(AsyncWebRouter(FAKE))
    assert MCP_TOOL_LOAD_ENV not in {tool.name for tool in served.list_tools()}


async def test_an_authorized_client_opens_a_session(tools: WebRouterTools) -> None:
    app = build_app(tools, HttpSettings.resolve("127.0.0.1", 8000, TOKEN))

    [response] = await initialize(app, {"Authorization": f"Bearer {TOKEN}"})

    assert response.status_code == 200
    assert '"serverInfo"' in response.text


def test_the_settings_repr_hides_the_token() -> None:
    assert TOKEN not in repr(HttpSettings.resolve("0.0.0.0", 8000, TOKEN))


async def test_shutdown_waits_a_bounded_time_for_open_requests(
    monkeypatch: pytest.MonkeyPatch, tools: WebRouterTools
) -> None:
    configs: list[Any] = []

    class _Server:
        def __init__(self, config: Any) -> None:
            configs.append(config)

        async def serve(self) -> None:
            return None

    monkeypatch.setattr(http_transport.uvicorn, "Server", _Server)

    await serve_http(HttpSettings.resolve("127.0.0.1", 8000, None), tools)

    assert configs[0].timeout_graceful_shutdown == MCP_HTTP_SHUTDOWN_GRACE


async def test_the_app_releases_every_session_as_it_shuts_down(
    monkeypatch: pytest.MonkeyPatch, tools: WebRouterTools
) -> None:
    """The reaper runs while the app does and stops first; then everything is released, before uvicorn
    re-raises a SIGTERM, which may end the process before anything after ``serve()``."""
    events: list[str] = []

    async def reaper() -> None:
        events.append("reaping")
        try:
            await anyio.sleep_forever()
        finally:
            events.append("reaper stopped")

    async def aclose() -> None:
        events.append("released")

    monkeypatch.setattr(tools.browsers, "run_reaper", reaper)
    monkeypatch.setattr(tools, "aclose", aclose)
    app = build_app(tools, HttpSettings.resolve("127.0.0.1", 8000, None))

    async with app.router.lifespan_context(app):  # type: ignore[attr-defined]
        await anyio.sleep(0)
        assert events == ["reaping"]

    assert events == ["reaping", "reaper stopped", "released"]


@pytest.mark.parametrize("host", sorted(MCP_HTTP_TOKENLESS_HOSTS))
async def test_a_tokenless_host_turns_away_a_foreign_host_header(tools: WebRouterTools, host: str) -> None:
    """The token rule and the SDK's DNS-rebinding check cover the same hosts."""
    app = build_app(tools, HttpSettings.resolve(host, 8000, None))
    foreign = {"Host": "attacker.example:8000", "Origin": "http://attacker.example:8000"}
    bound = {"Host": f"[{host}]:8000" if ":" in host else f"{host}:8000"}

    rejected, accepted = await initialize(app, foreign, bound)

    assert rejected.status_code == 421
    assert accepted.status_code != 421


async def test_with_a_token_a_proxied_host_gets_through(tools: WebRouterTools) -> None:
    """A TLS proxy in front forwards its own public hostname; the token, not the Host check, guards it."""
    app = build_app(tools, HttpSettings.resolve("127.0.0.1", 8000, TOKEN))
    proxied = {"Host": "mcp.team.example", "Origin": "https://mcp.team.example"}

    with_token, without = await initialize(app, {**proxied, "Authorization": f"Bearer {TOKEN}"}, proxied)

    assert with_token.status_code == 200
    assert without.status_code == 401
