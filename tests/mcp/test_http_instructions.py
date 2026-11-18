"""Over HTTP the model is told neither to call ``load_env`` nor to offer a visible local browser.

Each check has the stdio control beside it: with ``load_env`` served, the text is the same as before.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from tests.mcp.conftest import FAKE
from web_router.client import AsyncWebRouter
from web_router.constants_mcp_env import ENV_MCP_LOAD_ENV
from web_router.constants_mcp_http import MCP_HTTP_PATH
from web_router.mcp.http_transport import HttpSettings, build_app, http_env_defaults
from web_router.mcp.key_text import env_hint, key_hint, no_backend_hint, server_instructions
from web_router.mcp.tools import WebRouterTools

LOAD_ENV = "load_env"
LOCAL_WINDOW = "visible window on their machine"

INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}},
}


@pytest.fixture
def stdio(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_MCP_LOAD_ENV, "")  # blank reads as the default, which serves load_env


@pytest.fixture
def http(stdio: None) -> None:
    http_env_defaults()


def _hints() -> list[str]:
    return [
        server_instructions(keyed=False),
        key_hint(),
        no_backend_hint(["TAVILY_API_KEY"]),
        env_hint("WEB_ROUTER_ENV_FILE", Path("/home/u/.config/web-router")),
    ]


def test_stdio_still_offers_load_env_and_a_local_window(stdio: None, fake_provider: object) -> None:
    text = WebRouterTools(AsyncWebRouter(FAKE)).instructions()

    assert LOAD_ENV in text
    assert LOCAL_WINDOW in text
    assert all(LOAD_ENV in hint for hint in _hints())


def test_with_load_env_off_no_hint_names_it(http: None) -> None:
    assert not any(LOAD_ENV in hint for hint in _hints())
    assert "TAVILY_API_KEY" in no_backend_hint(["TAVILY_API_KEY"])


async def test_an_http_client_is_told_neither(http: None, fake_provider: object) -> None:
    tools = WebRouterTools(AsyncWebRouter(FAKE))
    app = build_app(tools, HttpSettings.resolve("127.0.0.1", 8000, None))
    headers = {"Accept": "application/json, text/event-stream"}
    async with app.router.lifespan_context(app):  # type: ignore[attr-defined]
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
            response = await client.post(MCP_HTTP_PATH, headers=headers, json=INITIALIZE)

    assert '"instructions"' in response.text
    assert LOAD_ENV not in response.text
    assert LOCAL_WINDOW not in response.text
