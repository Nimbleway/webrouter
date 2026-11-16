"""What a tool's schema offers and declares: narrowed to the backends serving it here, and its behaviour hints."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, get_args

from mcp_types import ToolAnnotations

from web_router.constants import (
    DELEGATING_PROVIDERS,
    MCP_ARG_FORMAT,
    MCP_ARG_PROVIDER,
    MCP_ARG_STEALTH,
    MCP_TOOL_BROWSER_OPEN,
    MCP_TOOL_EXTRACT,
    MCP_TOOL_PROPERTIES,
    MCP_TOOL_SEARCH,
)
from web_router.constants_live_data import MCP_TOOL_LIVE_DATA
from web_router.constants_mcp_hints import MCP_TOOL_HINTS
from web_router.exceptions import ProviderNotFoundError, ProviderNotInstalledError
from web_router.mcp.chains import CHAIN_OF
from web_router.mcp.methods import METHODS, keyed
from web_router.providers.base import BrowserProvider, LiveDataProvider, SearchProvider, _BaseProvider
from web_router.types import ExtractFormat, SearchDepth, StealthLevel

if TYPE_CHECKING:
    from web_router.client import AsyncWebRouter


def serving(tool: str, providers: list[str], router: AsyncWebRouter) -> list[type[_BaseProvider]]:
    """The classes that would serve ``providers`` for this tool's method: each one's own, a delegating one's chain.

    A name this method does not know, or whose extra is not installed, can
    serve nothing, so it adds nothing.
    """
    names: list[str] = []
    for provider in providers:
        if provider in DELEGATING_PROVIDERS and tool in CHAIN_OF:
            # Not every delegating name is every method's: ``max`` is extract's alone.
            try:
                names.extend(CHAIN_OF[tool](router, provider))
            except (ProviderNotFoundError, ProviderNotInstalledError):
                continue
        else:
            names.append(provider)
    classes: list[type[_BaseProvider]] = []
    for name in dict.fromkeys(names):
        try:
            classes.append(METHODS[tool].cls(name))
        except (ProviderNotFoundError, ProviderNotInstalledError):
            continue
    return classes


NARROWED: dict[str, tuple[str, tuple[str, ...], type[_BaseProvider], str]] = {
    MCP_TOOL_BROWSER_OPEN: (MCP_ARG_STEALTH, get_args(StealthLevel), BrowserProvider, "stealth_levels"),
    MCP_TOOL_LIVE_DATA: (MCP_ARG_FORMAT, get_args(ExtractFormat), LiveDataProvider, "supported_formats"),
    MCP_TOOL_SEARCH: ("search_depth", get_args(SearchDepth), SearchProvider, "depth_levels"),
}
"""Per tool, an option whose choices differ per backend: the option, its choices, and where a backend lists its own.

A browser's ``stealth`` levels, a template run's page ``format`` and a search's ``search_depth`` levels.
"""


def narrow(tool: str, properties: dict[str, Any], serving: list[type[_BaseProvider]]) -> None:
    """Narrow ``tool``'s :data:`NARROWED` option to the choices some backend serving here, that maps it, has.

    A choice none of them has would be refused on every route, so it is not
    offered; with none at all the option goes too. A backend that does not map
    the option lists choices it never serves, so it adds none.
    """
    if tool not in NARROWED or NARROWED[tool][0] not in properties:
        return
    name, choices, base, attribute = NARROWED[tool]
    optional = name in METHODS[tool].options
    mapping = [cls for cls in serving if issubclass(cls, base) and (not optional or name in cls.supported_parameters)]
    served = frozenset().union(*(getattr(cls, attribute) for cls in mapping))
    if offered := [choice for choice in choices if choice in served]:
        properties[name] = {**properties[name], "enum": offered}
    else:
        del properties[name]


BILLED_WHEN_KEYED = frozenset({MCP_TOOL_SEARCH, MCP_TOOL_EXTRACT})
"""Tools free on their keyless tier and billed once a paid backend is configured."""


def tool_annotations(tool: str) -> ToolAnnotations:
    """``tool``'s behaviour hints, from :data:`MCP_TOOL_HINTS`; its title rides on the tool itself.

    ``search`` and ``extract`` are free on the keyless tier, so they are
    read-only until a paid backend is configured for them: ``auto`` tries a
    keyed backend first, and each of its calls bills. Destructive and
    idempotent mean something only for a tool that is not read-only, so a
    read-only tool leaves them out of the list every turn pays for.
    """
    _, read_only, destructive, idempotent, open_world = MCP_TOOL_HINTS[tool]
    if tool in BILLED_WHEN_KEYED and keyed(METHODS[tool]):
        read_only, idempotent = False, False
    return ToolAnnotations(
        read_only_hint=read_only,
        destructive_hint=None if read_only else destructive,
        idempotent_hint=None if read_only else idempotent,
        open_world_hint=open_world,
    )


def argument_names(tool: str) -> list[str]:
    """Every argument ``tool`` declares, not only this install's schema: the library refuses what none here maps."""
    return [*MCP_TOOL_PROPERTIES.get(tool, {}), *([MCP_ARG_PROVIDER] if tool in METHODS else [])]
