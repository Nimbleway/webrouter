"""Over HTTP the model is told neither to call ``load_env`` nor to offer a visible local browser.

Each check has the stdio control beside it: with ``load_env`` served, the text is the same as before.
"""

from __future__ import annotations

from pathlib import Path

from tests.mcp.conftest import FAKE
from tests.mcp.http_helpers import initialize
from web_router.client import AsyncWebRouter
from web_router.mcp.http_transport import HttpSettings, build_app
from web_router.mcp.key_text import env_hint, key_hint, no_backend_hint, server_instructions
from web_router.mcp.tools import WebRouterTools

LOAD_ENV = "load_env"
LOCAL_WINDOW = "visible window on their machine"


def _hints() -> list[str]:
    return [
        server_instructions(keyed=False),
        key_hint(),
        no_backend_hint(["TAVILY_API_KEY"]),
        env_hint("WEB_ROUTER_ENV_FILE", Path("/home/u/.config/web-router")),
    ]


def test_stdio_still_offers_load_env_and_a_local_window(stdio_switches: None, fake_provider: object) -> None:
    text = WebRouterTools(AsyncWebRouter(FAKE)).instructions()

    assert LOAD_ENV in text
    assert LOCAL_WINDOW in text
    assert all(LOAD_ENV in hint for hint in _hints())


def test_with_load_env_off_no_hint_names_it(http_switches: None) -> None:
    assert not any(LOAD_ENV in hint for hint in _hints())
    assert "TAVILY_API_KEY" in no_backend_hint(["TAVILY_API_KEY"])


async def test_an_http_client_is_told_neither(http_switches: None, fake_provider: object) -> None:
    app = build_app(WebRouterTools(AsyncWebRouter(FAKE)), HttpSettings.resolve("127.0.0.1", 8000, None))

    [response] = await initialize(app, {})

    assert '"instructions"' in response.text
    assert LOAD_ENV not in response.text
    assert LOCAL_WINDOW not in response.text
