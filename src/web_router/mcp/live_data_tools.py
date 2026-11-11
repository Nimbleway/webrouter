"""The three live data tools: list a vendor's sources, describe one, run one.

Kept out of :mod:`web_router.mcp.tools`, which is at its size limit. Each is a
thin projection of the router's live data methods, as every other tool is of
its method; a run spends from the connection's budget like any billed call.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from web_router.constants_live_data import MCP_TOOL_LIVE_DATA, MCP_TOOL_LIVE_DATA_SOURCE, MCP_TOOL_LIVE_DATA_SOURCES
from web_router.mcp.budget import live_data_charged
from web_router.mcp.shaping import max_response_chars
from web_router.mcp.shaping_live_data import shape_live_data, shape_live_data_source, shape_live_data_sources
from web_router.mcp.urls import screen, url_values

if TYPE_CHECKING:
    from web_router.mcp.tools import WebRouterTools


async def live_data_sources_tool(tools: WebRouterTools, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """One page of a vendor's catalogue, cut to the words a model picks a source by."""
    page = await tools.router.live_data_sources(**tools.arguments(MCP_TOOL_LIVE_DATA_SOURCES, arguments))
    record = tools.store.add(MCP_TOOL_LIVE_DATA_SOURCES, raw=page.raw)
    return shape_live_data_sources(page, record.handle, budget=max_response_chars())


async def live_data_source_tool(tools: WebRouterTools, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """One source with its schemas and samples."""
    source = await tools.router.live_data_source(**tools.arguments(MCP_TOOL_LIVE_DATA_SOURCE, arguments))
    record = tools.store.add(MCP_TOOL_LIVE_DATA_SOURCE, raw=source.raw)
    return shape_live_data_source(source, record.handle, budget=max_response_chars())


async def live_data_tool(tools: WebRouterTools, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """One run, charged to the connection's budget, its records whole or left behind the handle."""
    kwargs = tools.arguments(MCP_TOOL_LIVE_DATA, arguments)
    # A URL among the inputs is screened like any other tool's: no private or metadata address.
    screen(MCP_TOOL_LIVE_DATA, url_values(kwargs.get("inputs")))
    with live_data_charged(tools.router, kwargs):
        response = await tools.router.live_data(**kwargs)
    record = tools.store.add(MCP_TOOL_LIVE_DATA, raw=response.raw)
    return shape_live_data(response, record.handle, budget=max_response_chars())
