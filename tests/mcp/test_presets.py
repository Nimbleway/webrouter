"""Presets over MCP: list, read and create them, and `preset` on the method tools, resolved by the server."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.mcp.conftest import FAKE
from tests.mcp.test_browser_tools import clock, fake_pages, session  # noqa: F401 -- fixtures
from web_router import FilePresetStore, InMemoryPresetStore, Preset
from web_router.constants import (
    BROWSER_TIMEOUT,
    MCP_TOOL_BROWSE,
    MCP_TOOL_BROWSER_OPEN,
    MCP_TOOL_RESEARCH,
    MCP_TOOL_SEARCH,
    PROVIDER_AUTO,
)
from web_router.constants_mcp_presets import MCP_TOOL_CREATE_PRESET, MCP_TOOL_GET_PRESET, MCP_TOOL_LIST_PRESETS
from web_router.exceptions import ConfigurationError
from web_router.mcp.presets import store_from
from web_router.mcp.routing_config import McpRouting
from web_router.mcp.server import call_tool, list_tools
from web_router.mcp.tools import WebRouterTools

EU_SEMICONDUCTOR_NEWS = {
    "name": "eu_semiconductor_news",
    "tool": "search",
    "description": "EU chip-industry news, English, trade press only",
    "params": {"max_results": 20, "search_depth": "fast", "country": "DE", "locale": "en"},
}


def _tool(tools: WebRouterTools, name: str) -> Any:
    return next(tool for tool in list_tools(tools).tools if tool.name == name)


async def _call(tools: WebRouterTools, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    result = await call_tool(tools, name, arguments)
    assert not result.is_error, result.content[0].text
    return dict(json.loads(result.content[0].text))


async def _refused(tools: WebRouterTools, name: str, arguments: dict[str, Any]) -> str:
    result = await call_tool(tools, name, arguments)
    assert result.is_error
    return str(result.content[0].text)


async def test_a_created_preset_is_listed_and_read_back(tools: WebRouterTools) -> None:
    created = await _call(tools, MCP_TOOL_CREATE_PRESET, EU_SEMICONDUCTOR_NEWS)

    assert created == EU_SEMICONDUCTOR_NEWS
    assert (await _call(tools, MCP_TOOL_LIST_PRESETS, {}))["presets"] == [EU_SEMICONDUCTOR_NEWS]
    assert await _call(tools, MCP_TOOL_GET_PRESET, {"name": "eu_semiconductor_news"}) == EU_SEMICONDUCTOR_NEWS


async def test_a_taken_name_and_a_bad_param_are_refused(tools: WebRouterTools) -> None:
    await _call(tools, MCP_TOOL_CREATE_PRESET, EU_SEMICONDUCTOR_NEWS)

    assert "already have a preset named" in await _refused(tools, MCP_TOOL_CREATE_PRESET, EU_SEMICONDUCTOR_NEWS)
    bad = {"name": "typo", "tool": "search", "params": {"max_result": 5}}
    assert "`max_result` is not a search param" in await _refused(tools, MCP_TOOL_CREATE_PRESET, bad)
    assert "No preset named 'nope'" in await _refused(tools, MCP_TOOL_GET_PRESET, {"name": "nope"})


async def test_a_search_with_a_preset_sends_its_params_and_says_so(tools: WebRouterTools) -> None:
    await _call(tools, MCP_TOOL_CREATE_PRESET, {**EU_SEMICONDUCTOR_NEWS, "params": {"max_results": 20}})

    payload = await _call(
        tools, MCP_TOOL_SEARCH, {"query": "ASML export licences", "preset": "eu_semiconductor_news", "provider": FAKE}
    )

    assert tools.router.get_provider(FAKE).requests[-1].max_results == 20  # type: ignore[attr-defined]
    assert payload["preset"] == {"name": "eu_semiconductor_news", "params": {"max_results": 20, "provider": FAKE}}


async def test_the_calls_own_argument_wins(tools: WebRouterTools) -> None:
    await _call(tools, MCP_TOOL_CREATE_PRESET, {**EU_SEMICONDUCTOR_NEWS, "params": {"max_results": 20}})

    payload = await _call(
        tools, MCP_TOOL_SEARCH, {"query": "q", "preset": "eu_semiconductor_news", "max_results": 5, "provider": FAKE}
    )

    assert tools.router.get_provider(FAKE).requests[-1].max_results == 5  # type: ignore[attr-defined]
    assert payload["preset"]["params"]["max_results"] == 5


async def test_a_preset_of_another_tool_is_refused(tools: WebRouterTools) -> None:
    await _call(tools, MCP_TOOL_CREATE_PRESET, {"name": "page_text", "tool": "extract", "params": {"format": "text"}})

    assert "Preset 'page_text' runs extract" in await _refused(
        tools, MCP_TOOL_SEARCH, {"query": "q", "preset": "page_text"}
    )


async def test_a_preset_param_the_tool_does_not_take_over_mcp_is_refused(tools: WebRouterTools) -> None:
    tools.router.presets.create(Preset(name="raw", tool="search", params={"provider_options": {"x": 1}}))

    assert "sets provider_options" in await _refused(tools, MCP_TOOL_SEARCH, {"query": "q", "preset": "raw"})


async def test_a_research_presets_timeout_gives_way_to_the_servers_wait(tools: WebRouterTools) -> None:
    tools.router.presets.create(
        Preset(name="supplier_profile", tool="research", params={"effort": "high", "timeout": 3600})
    )

    payload = await _call(
        tools, MCP_TOOL_RESEARCH, {"query": "Infineon", "preset": "supplier_profile", "provider": FAKE}
    )

    assert tools.router.get_research_provider(FAKE).requests[-1].effort == "high"  # type: ignore[attr-defined]
    assert payload["preset"] == {"name": "supplier_profile", "params": {"effort": "high", "provider": FAKE}}


async def test_a_null_argument_is_unset_in_what_runs_and_in_the_echo(tools: WebRouterTools) -> None:
    await _call(tools, MCP_TOOL_CREATE_PRESET, {**EU_SEMICONDUCTOR_NEWS, "params": {"max_results": 20}})

    payload = await _call(
        tools,
        MCP_TOOL_SEARCH,
        {"query": "q", "preset": "eu_semiconductor_news", "max_results": None, "provider": FAKE},
    )

    assert tools.router.get_provider(FAKE).requests[-1].max_results == 20  # type: ignore[attr-defined]
    assert payload["preset"]["params"] == {"max_results": 20, "provider": FAKE}


async def test_a_call_without_a_preset_carries_no_echo_after_one_with_it(tools: WebRouterTools) -> None:
    await _call(tools, MCP_TOOL_CREATE_PRESET, {**EU_SEMICONDUCTOR_NEWS, "params": {"max_results": 20}})
    shown = await _call(tools, MCP_TOOL_SEARCH, {"query": "q", "preset": "eu_semiconductor_news", "provider": FAKE})
    plain = await _call(tools, MCP_TOOL_SEARCH, {"query": "q", "provider": FAKE})

    assert shown["preset"]["params"]["max_results"] == 20
    assert "preset" not in plain


def test_each_method_tool_takes_a_preset(tools: WebRouterTools, no_keys: None) -> None:
    schema = _tool(tools, MCP_TOOL_BROWSE).input_schema

    # `provider` is there with no key at all because `auto` is, as for research.
    assert set(schema["properties"]) == {
        "task",
        "start_url",
        "output_schema",
        "max_steps",
        "allowed_domains",
        "provider",
        "preset",
    }
    assert schema["properties"]["provider"]["enum"] == [PROVIDER_AUTO]
    assert schema["required"] == ["task"]
    assert "waits 0s" in _tool(tools, MCP_TOOL_BROWSE).description


def test_the_store_is_picked_by_the_environment(tmp_path: Path) -> None:
    assert isinstance(store_from(None), InMemoryPresetStore)
    assert isinstance(store_from(str(tmp_path)), FilePresetStore)
    with pytest.raises(ConfigurationError, match="WEB_ROUTER_PRESETS is set but empty"):
        store_from(" ")


def test_a_rebuilt_router_keeps_the_same_store(tmp_path: Path) -> None:
    routing = McpRouting.from_environ({"WEB_ROUTER_PRESETS": str(tmp_path)})

    assert routing.build().presets is routing.build().presets is routing.presets


def test_a_database_url_picks_the_sql_store(tmp_path: Path) -> None:
    pytest.importorskip("sqlalchemy")
    from web_router import SqlPresetStore
    from web_router.presets_sql import upgrade

    url = f"sqlite:///{tmp_path / 'presets.db'}"
    upgrade(url)
    store = store_from(url)

    assert isinstance(store, SqlPresetStore)
    store.close()


async def test_a_browser_presets_timeout_is_lowered_and_echoed_as_it_ran(session: WebRouterTools) -> None:  # noqa: F811
    session.router.presets.create(
        Preset(name="long_session", tool="open_browser", params={"timeout": 10 * BROWSER_TIMEOUT, "headless": True})
    )

    opened = await _call(session, MCP_TOOL_BROWSER_OPEN, {"preset": "long_session"})

    assert session.router.get_browser_provider("fake").requests[-1].timeout == BROWSER_TIMEOUT  # type: ignore[attr-defined]
    assert opened["preset"] == {"name": "long_session", "params": {"timeout": BROWSER_TIMEOUT, "headless": True}}


async def test_listing_and_reading_a_preset_never_show_provider_options_values(tools: WebRouterTools) -> None:
    tools.router.presets.create(Preset(name="keyed", tool="search", params={"provider_options": {"api_key": "SEKRIT"}}))

    listed = await _call(tools, MCP_TOOL_LIST_PRESETS, {})
    read = await _call(tools, MCP_TOOL_GET_PRESET, {"name": "keyed"})

    assert listed["presets"][0]["params"] == read["params"] == {"provider_options": {"api_key": "[redacted]"}}
