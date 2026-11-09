"""What a tool says about itself beyond its schema, and what a call is checked for before anything runs."""

from __future__ import annotations

from typing import Any

import pytest

from tests.mcp.conftest import FAKE
from web_router.constants import MCP_TOOL_RESEARCH, MCP_TOOL_SEARCH
from web_router.constants_mcp import MCP_TOOL_BUDGET_STATUS, MCP_TOOL_HINTS, MCP_TOOL_WEB_RUN
from web_router.mcp.server import call_tool
from web_router.mcp.tools import WebRouterTools


async def test_an_unknown_argument_is_refused_with_the_valid_names(tools: WebRouterTools) -> None:
    """A guessed option must not look applied: ``recency`` dropped silently returns unfiltered results."""
    result = await call_tool(tools, MCP_TOOL_SEARCH, {"query": "hello", "recency": "week"})

    assert result.is_error
    text = result.content[0].text
    assert "recency" in text and "max_results" in text and "provider" in text
    assert tools.router.get_provider(FAKE).requests == []  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        (MCP_TOOL_BUDGET_STATUS, {"bogus": 1}),
        (MCP_TOOL_WEB_RUN, {"prompt": "x", "dry_run": True, "bogus": 1}),
        (MCP_TOOL_SEARCH, {"query": "x", "max_result": 3}),
    ],
)
async def test_every_tool_refuses_a_name_it_does_not_declare(
    tools: WebRouterTools, tool: str, arguments: dict[str, Any]
) -> None:
    result = await call_tool(tools, tool, arguments)

    assert result.is_error
    assert "does not take" in result.content[0].text


def test_every_listed_tool_carries_a_title_and_behaviour_hints(tools: WebRouterTools) -> None:
    listed = {tool.name: tool for tool in tools.list_tools()}

    assert set(MCP_TOOL_HINTS) >= set(listed) and set(MCP_TOOL_HINTS) == set(tools._handlers)
    assert all(tool.title and tool.annotations for tool in listed.values())
    # A client auto-approves a free lookup and asks before a billed run.
    assert listed[MCP_TOOL_SEARCH].annotations.read_only_hint is True  # type: ignore[union-attr]
    assert listed[MCP_TOOL_RESEARCH].annotations.idempotent_hint is False  # type: ignore[union-attr]
