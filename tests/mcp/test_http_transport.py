"""The HTTP transport's three guards: the token on every request, no open bind off loopback, no ``load_env``."""

from __future__ import annotations

import os

import httpx
import pytest
from starlette.types import Receive, Scope, Send

from tests.mcp.conftest import FAKE
from web_router import mcp_cli
from web_router.client import AsyncWebRouter
from web_router.constants import MCP_TOOL_LOAD_ENV
from web_router.constants_mcp_env import ENV_MCP_LOAD_ENV, MCP_LOAD_ENV_LOAD, MCP_LOAD_ENV_OFF
from web_router.constants_mcp_http import ENV_MCP_TOKEN, MCP_HTTP_PATH
from web_router.exceptions import ConfigurationError
from web_router.mcp import http_transport
from web_router.mcp.env_policy import switch
from web_router.mcp.http_transport import BearerAuth, HttpSettings, build_app, http_env_defaults
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


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.5", "example.com"])
@pytest.mark.parametrize("token", [None, "", "   "])
def test_an_address_off_loopback_needs_a_token(host: str, token: str | None) -> None:
    with pytest.raises(ConfigurationError, match=ENV_MCP_TOKEN):
        HttpSettings.resolve(host, 8000, token)


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.2", "::1", "[::1]", "localhost", "LOCALHOST"])
def test_loopback_runs_without_a_token(host: str) -> None:
    assert HttpSettings.resolve(host, 8000, None).token is None


def _never_serves(*args: object) -> None:
    raise AssertionError("an open bind without a token started serving")


def test_the_command_refuses_an_open_bind_before_serving(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(ENV_MCP_TOKEN, raising=False)
    monkeypatch.setattr(http_transport.anyio, "run", _never_serves)

    with pytest.raises(SystemExit) as exited:
        mcp_cli.main(["--transport", "http", "--host", "0.0.0.0"])

    assert exited.value.code == 2
    assert ENV_MCP_TOKEN in capsys.readouterr().err


def test_host_without_http_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as exited:
        mcp_cli.main(["--host", "0.0.0.0"])

    assert exited.value.code == 2


def test_load_env_is_off_under_http_unless_the_operator_set_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_MCP_LOAD_ENV, "")
    http_env_defaults()
    assert switch(ENV_MCP_LOAD_ENV) == MCP_LOAD_ENV_OFF

    monkeypatch.setenv(ENV_MCP_LOAD_ENV, MCP_LOAD_ENV_LOAD)
    http_env_defaults()
    assert os.environ[ENV_MCP_LOAD_ENV] == MCP_LOAD_ENV_LOAD


def test_an_http_session_does_not_list_load_env(monkeypatch: pytest.MonkeyPatch, tools: WebRouterTools) -> None:
    monkeypatch.setenv(ENV_MCP_LOAD_ENV, "")  # blank reads as the default; recorded for undo
    assert MCP_TOOL_LOAD_ENV in {tool.name for tool in tools.list_tools()}

    http_env_defaults()  # before the session is built, as the command runs it

    served = WebRouterTools(AsyncWebRouter(FAKE))
    assert MCP_TOOL_LOAD_ENV not in {tool.name for tool in served.list_tools()}


async def test_an_authorized_client_opens_a_session(tools: WebRouterTools) -> None:
    served = build_app(tools, HttpSettings.resolve("127.0.0.1", 8000, None))
    app = BearerAuth(served, TOKEN)
    initialize = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}},
    }
    headers = {"Authorization": f"Bearer {TOKEN}", "Accept": "application/json, text/event-stream"}
    async with served.router.lifespan_context(served):  # type: ignore[attr-defined]
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
            response = await client.post(MCP_HTTP_PATH, headers=headers, json=initialize)

    assert response.status_code == 200
    assert '"serverInfo"' in response.text


def test_the_settings_repr_hides_the_token() -> None:
    assert TOKEN not in repr(HttpSettings.resolve("0.0.0.0", 8000, TOKEN))


@pytest.mark.parametrize("port", ["0", "-1", "65536", "http"])
def test_a_port_out_of_range_is_a_usage_error(port: str) -> None:
    with pytest.raises(SystemExit) as exited:
        mcp_cli.main(["--transport", "http", "--port", port])

    assert exited.value.code == 2
