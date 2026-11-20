"""Which backends this install can serve each method with, in one table.

The command line's ``providers`` and the MCP server's capabilities resource
both print it, so it lives here, with no extra to import, rather than in each.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from web_router.constants import (
    ANSWER_ENV_BY_PROVIDER,
    AUTO_BROWSER_KEYLESS,
    AUTO_EXTRACT_KEYLESS,
    AUTO_KEYLESS,
    BROWSE_ENV_BY_PROVIDER,
    MCP_METHOD_BROWSER,
    MCP_TOOL_ANSWER,
    MCP_TOOL_BROWSE,
    MCP_TOOL_EXTRACT,
    MCP_TOOL_RESEARCH,
    MCP_TOOL_SEARCH,
    PROVIDER_AUTO,
    RESEARCH_ENV_BY_PROVIDER,
)
from web_router.constants_cli import CLI_KEYLESS, CLI_NEEDS_KEY
from web_router.constants_live_data import LIVE_DATA_ENV_BY_PROVIDER, MCP_METHOD_LIVE_DATA
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

KEYLESS_BY_METHOD: dict[str, tuple[str, ...]] = {
    method: tuple(name for name, _ in chain)
    for method, chain in (
        (MCP_TOOL_SEARCH, AUTO_KEYLESS),
        (MCP_TOOL_EXTRACT, AUTO_EXTRACT_KEYLESS),
        (MCP_METHOD_BROWSER, AUTO_BROWSER_KEYLESS),
    )
}
"""The backends ``auto`` runs with no key, per method, in the order it tries them; the other methods have none."""

KEYED_ENV_BY_METHOD: dict[str, Mapping[str, tuple[str, ...]]] = {
    MCP_METHOD_LIVE_DATA: LIVE_DATA_ENV_BY_PROVIDER,
    MCP_TOOL_ANSWER: ANSWER_ENV_BY_PROVIDER,
    MCP_TOOL_RESEARCH: RESEARCH_ENV_BY_PROVIDER,
    MCP_TOOL_BROWSE: BROWSE_ENV_BY_PROVIDER,
}
"""The methods with no keyless tier, and the variables each backend of theirs needs."""


def configured_by_method() -> dict[str, list[str]]:
    """Per method, the backends the environment makes usable; reads variables only and calls no backend."""
    return {method: configured() for method, configured in CONFIGURED_BY_METHOD.items()}


def providers_report() -> dict[str, Any]:
    """:func:`configured_by_method`, plus what ``auto`` runs keyless and the variables a method with nothing needs.

    A keyless backend missing from the method's own names is reached through ``auto`` only: naming it as
    ``provider`` without its key fails. One in both, such as ``jina``, needs no key either way.
    """
    configured = configured_by_method()
    needs_key = {
        method: sorted({env_var for env_vars in table.values() for env_var in env_vars})
        for method, table in KEYED_ENV_BY_METHOD.items()
        if not configured[method]
    }
    return {
        **configured,
        CLI_KEYLESS: {m: list(names) for m, names in KEYLESS_BY_METHOD.items()},
        CLI_NEEDS_KEY: needs_key,
    }
