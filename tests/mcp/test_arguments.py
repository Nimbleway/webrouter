"""What a tool says about itself beyond its schema, and what a call is checked for before anything runs."""

from __future__ import annotations

from typing import Any

import pytest

from tests.mcp.conftest import FAKE
from web_router.constants import (
    ENV_LOCAL_BROWSER_PATH,
    ENV_NIMBLE_API_KEY,
    ENV_TAVILY_API_KEY,
    LOCAL_BROWSER_PATH_OPTION,
    MCP_TOOL_LOAD_ENV,
    MCP_TOOL_RESEARCH,
    MCP_TOOL_RUN_TEMPLATE,
    MCP_TOOL_SEARCH,
    MCP_TOOL_TEMPLATE,
    MCP_TOOL_TEMPLATES,
    MSG_LOCAL_NO_BROWSER,
    PROVIDER_TAVILY,
)
from web_router.constants_mcp import MCP_NO_ARGUMENTS, MCP_TOOL_BUDGET_STATUS
from web_router.constants_mcp_hints import MCP_TOOL_HINTS
from web_router.mcp.messages import error_text
from web_router.mcp.server import call_tool
from web_router.mcp.tools import WebRouterTools


async def test_an_unknown_argument_is_refused_with_the_valid_names(tools: WebRouterTools) -> None:
    """A guessed option must not look applied: ``freshness`` dropped silently returns unfiltered results."""
    result = await call_tool(tools, MCP_TOOL_SEARCH, {"query": "hello", "freshness": "week"})

    assert result.is_error
    text = result.content[0].text
    assert "freshness" in text and "max_results" in text and "provider" in text
    assert tools.router.get_provider(FAKE).requests == []  # type: ignore[attr-defined]


async def test_a_tool_with_no_arguments_refuses_one_and_says_so(tools: WebRouterTools) -> None:
    """Even a tool that takes no arguments: the refusal says so rather than listing nothing."""
    result = await call_tool(tools, MCP_TOOL_BUDGET_STATUS, {"bogus": 1})

    assert result.is_error
    assert "does not take" in result.content[0].text
    assert MCP_NO_ARGUMENTS in result.content[0].text


def test_every_listed_tool_carries_a_title_and_behaviour_hints(tools: WebRouterTools) -> None:
    listed = {tool.name: tool for tool in tools.list_tools()}

    assert set(MCP_TOOL_HINTS) >= set(listed) and set(MCP_TOOL_HINTS) == set(tools._handlers)
    assert all(tool.title and tool.annotations for tool in listed.values())
    # A client auto-approves a free lookup and asks before a billed run.
    assert listed[MCP_TOOL_SEARCH].annotations.read_only_hint is True  # type: ignore[union-attr]
    assert listed[MCP_TOOL_RESEARCH].annotations.idempotent_hint is False  # type: ignore[union-attr]


async def test_a_no_key_error_points_to_load_env_not_to_code(tools: WebRouterTools, no_keys: None) -> None:
    """``provider_config`` is a Python argument: over MCP, the way in is a ``.env`` and ``load_env``."""
    text = (await call_tool(tools, MCP_TOOL_SEARCH, {"query": "x", "provider": PROVIDER_TAVILY})).content[0].text

    assert ENV_TAVILY_API_KEY in text and MCP_TOOL_LOAD_ENV in text
    assert "provider_config" not in text


def test_the_no_browser_error_names_no_provider_config_over_mcp() -> None:
    """A browser path is not a key: the ``provider_config`` aside goes, and no ``load_env`` hint replaces it."""
    message = MSG_LOCAL_NO_BROWSER.format(env_var=ENV_LOCAL_BROWSER_PATH, option=LOCAL_BROWSER_PATH_OPTION)

    text = error_text(ValueError(message))

    assert "provider_config" not in text and ENV_LOCAL_BROWSER_PATH in text
    assert MCP_TOOL_LOAD_ENV not in text


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [(MCP_TOOL_TEMPLATES, {}), (MCP_TOOL_TEMPLATE, {"name": "t"}), (MCP_TOOL_RUN_TEMPLATE, {"template": "t"})],
)
async def test_a_template_tool_with_nothing_configured_says_what_would_add_one(
    no_keys: None, tool: str, arguments: dict[str, Any]
) -> None:
    """Keyless, the schema has no ``provider`` to offer, so the error must not ask for one."""
    result = await call_tool(WebRouterTools(), tool, arguments)

    text = result.content[0].text
    assert result.is_error and ENV_NIMBLE_API_KEY in text and MCP_TOOL_LOAD_ENV in text
    assert "needs a 'provider'" not in text
