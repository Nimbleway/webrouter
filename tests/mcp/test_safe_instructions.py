"""Instructions and failures describe the tools this server actually offers."""

from __future__ import annotations

import pytest

from tests.mcp.env_helpers import OPENED_UP
from web_router.constants_mcp_context import ENV_MCP_TOOLS
from web_router.constants_mcp_env import ENV_MCP_LOAD_ENV, ENV_MCP_PROFILE
from web_router.mcp.tools import WebRouterTools


@pytest.fixture(autouse=True)
def defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (*OPENED_UP, ENV_MCP_TOOLS):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("load_env", [False, True])
async def test_live_data_no_key_error_only_names_load_env_when_available(
    no_keys: None, monkeypatch: pytest.MonkeyPatch, load_env: bool
) -> None:
    monkeypatch.setenv(ENV_MCP_TOOLS, "search,live_data")
    if load_env:
        monkeypatch.setenv(ENV_MCP_LOAD_ENV, "remember")
    tools = WebRouterTools()

    with pytest.raises(ValueError) as error:
        await tools.call("live_data", {"source": "x", "inputs": {}})

    assert ("load_env" in str(error.value)) is load_env
    assert "APIFY_TOKEN" in str(error.value)
    await tools.router.aclose()


async def test_missing_key_tools_are_named_until_a_key_makes_them_runnable(
    no_keys: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    tools = WebRouterTools()
    instructions = tools.instructions()

    assert "answer (" in instructions and "research (" in instructions
    assert "LINKUP_API_KEY" in instructions and "operator" in instructions
    monkeypatch.setenv("LINKUP_API_KEY", "fake")
    assert "Tools unavailable without a backend key:" not in tools.instructions()
    await tools.router.aclose()


@pytest.mark.parametrize("profile", ["slim", "full"])
async def test_local_browser_advice_requires_served_browser_tools(
    no_keys: None, monkeypatch: pytest.MonkeyPatch, profile: str
) -> None:
    monkeypatch.setenv(ENV_MCP_PROFILE, profile)
    monkeypatch.setattr("web_router.mcp.tools.playwright_installed", lambda: True)
    tools = WebRouterTools()

    assert ("offer `browser_open`" in tools.instructions()) is (profile == "full")
    assert "Never ask for a password, an ID number or a date of birth in the conversation." in tools.instructions()
    assert "Never ask for a password, an ID number or a date of birth in the conversation." in tools.instructions(
        remote=True
    )
    await tools.router.aclose()
