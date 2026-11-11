"""The MCP search tool's data API matches: listed under ``data_apis`` only when there are some, never called."""

from __future__ import annotations

from typing import Any

from tests.mcp.conftest import ok_on
from web_router.constants import MCP_TOOL_SEARCH
from web_router.constants_vendors import DATA_APIS_ONLY_PROVIDER
from web_router.mcp.tools import WebRouterTools


async def test_search_lists_the_matching_data_apis_beside_the_results(tools: WebRouterTools) -> None:
    payload = await ok_on(tools, MCP_TOOL_SEARCH, query="datadog monitors")
    assert payload["results"]
    assert payload["data_apis"]
    assert {match["data_api"] for match in payload["data_apis"]} == {"datadog"}
    assert {"endpoint", "configured", "missing", "kind"} <= set(payload["data_apis"][0])


async def test_no_match_and_discovery_off_leave_the_field_out(tools: WebRouterTools) -> None:
    assert "data_apis" not in await ok_on(tools, MCP_TOOL_SEARCH, query="who won the 2022 world cup")
    assert "data_apis" not in await ok_on(tools, MCP_TOOL_SEARCH, query="datadog monitors", data_apis=False)


async def test_data_apis_only_reaches_no_web_backend(tools: WebRouterTools, fake_provider: Any) -> None:
    payload = await ok_on(tools, MCP_TOOL_SEARCH, query="datadog monitors", data_apis_only=True)
    assert payload["provider"] == DATA_APIS_ONLY_PROVIDER
    assert payload["results"] == []
    assert payload["data_apis"]
    assert tools.router.get_provider("fake").requests == []  # type: ignore[attr-defined]
