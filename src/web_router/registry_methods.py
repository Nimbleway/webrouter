"""Which backends this install can serve each method with, in one table.

The command line's ``providers`` and the MCP server's capabilities resource
both print it, so it lives here, with no extra to import, rather than in each.
"""

from __future__ import annotations

from collections.abc import Callable

from web_router.constants import (
    MCP_METHOD_BROWSER,
    MCP_TOOL_ANSWER,
    MCP_TOOL_BROWSE,
    MCP_TOOL_EXTRACT,
    MCP_TOOL_RESEARCH,
    MCP_TOOL_SEARCH,
    PROVIDER_AUTO,
)
from web_router.constants_live_data import MCP_METHOD_LIVE_DATA
from web_router.registry import (
    configured_browse_providers,
    configured_browser_providers,
    configured_extract_providers,
    configured_live_data_providers,
    configured_providers,
    configured_research_providers,
)
from web_router.registry_answer import configured_answer_providers


def configured_answer() -> list[str]:
    """Answer's backends with a key, led by ``auto`` once there is one, as research's are; nothing with none."""
    names = configured_answer_providers()
    return [PROVIDER_AUTO, *names] if names else []


CONFIGURED_BY_METHOD: dict[str, Callable[[], list[str]]] = {
    MCP_TOOL_SEARCH: configured_providers,
    MCP_TOOL_EXTRACT: configured_extract_providers,
    MCP_METHOD_LIVE_DATA: configured_live_data_providers,
    MCP_TOOL_ANSWER: configured_answer,
    MCP_TOOL_RESEARCH: configured_research_providers,
    MCP_TOOL_BROWSE: configured_browse_providers,
    MCP_METHOD_BROWSER: configured_browser_providers,
}
"""Each method, keyed as the capabilities resource names it, and the lookup of the backends it can use here."""


def configured_by_method() -> dict[str, list[str]]:
    """Per method, the backends the environment makes usable; reads variables only and calls no backend."""
    return {method: configured() for method, configured in CONFIGURED_BY_METHOD.items()}
