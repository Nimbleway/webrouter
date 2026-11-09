"""The MCP ``search`` tool offers only the ``search_depth`` levels the backends reachable from here serve."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from web_router import AsyncWebRouter
from web_router.constants import MCP_TOOL_SEARCH
from web_router.mcp.methods import METHODS
from web_router.mcp.server import list_tools
from web_router.mcp.tools import WebRouterTools


@pytest.fixture(autouse=True)
def only_the_chain(no_keys: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """The keyless backends serve every level, so they are taken out of what is configured here."""
    monkeypatch.setitem(METHODS, MCP_TOOL_SEARCH, replace(METHODS[MCP_TOOL_SEARCH], configured=lambda: ["auto"]))


def _search_properties(chain: list[str]) -> dict[str, Any]:
    router = AsyncWebRouter(
        provider_config={"auto": {"chain": {"search": [(name, {"api_key": "x"}) for name in chain]}}}
    )
    tools = list_tools(WebRouterTools(router, research_wait=0.0, browse_wait=0.0)).tools
    return next(tool for tool in tools if tool.name == MCP_TOOL_SEARCH).input_schema["properties"]


def test_a_level_no_backend_here_serves_is_not_offered() -> None:
    # Brave serves lite alone and Perplexity lite and fast, so deep would be refused on every route.
    assert _search_properties(["brave", "perplexity"])["search_depth"]["enum"] == ["lite", "fast"]


def test_every_level_is_offered_when_one_backend_serves_each() -> None:
    assert _search_properties(["brave", "exa"])["search_depth"]["enum"] == ["lite", "fast", "deep"]


def test_the_option_goes_when_no_backend_here_maps_it() -> None:
    assert "search_depth" not in _search_properties(["linkup"])
