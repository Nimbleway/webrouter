"""The MCP shapes around a result: structured content beside the text, and the spec's error for an unknown resource."""

from __future__ import annotations

import json

import pytest
from mcp.shared.exceptions import MCPError

from web_router.constants import MCP_TOOL_SEARCH
from web_router.constants_mcp import MCP_RESOURCE_NOT_FOUND
from web_router.mcp.server import call_tool, read_resource
from web_router.mcp.tools import WebRouterTools


async def test_a_result_carries_the_same_object_as_structured_content(tools: WebRouterTools) -> None:
    result = await call_tool(tools, MCP_TOOL_SEARCH, {"query": "hello"})

    assert not result.is_error
    assert result.structured_content == json.loads(result.content[0].text)


async def test_an_error_carries_no_structured_content(tools: WebRouterTools) -> None:
    result = await call_tool(tools, MCP_TOOL_SEARCH, {})

    assert result.is_error and result.structured_content is None


def test_an_unknown_resource_is_the_specs_resource_not_found(tools: WebRouterTools) -> None:
    with pytest.raises(MCPError) as raised:
        read_resource(tools, "web-router://nope")

    assert raised.value.error.code == MCP_RESOURCE_NOT_FOUND
    assert "web-router://capabilities" in raised.value.error.message
