"""The ``load_env`` tool: provider keys from a ``.env`` file the user names, by path.

Kept out of :mod:`web_router.mcp.tools`, which is at its size limit. The file
rules are :mod:`web_router.mcp.env`'s; this is the tool's side of them.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from web_router.constants import (
    MCP_ARG_PATH,
    MCP_ARG_REMEMBER,
    MCP_MSG_ENV_BAD_ARGUMENTS,
    MCP_MSG_ENV_NOTHING_NEW,
    MCP_MSG_ENV_REMEMBERED,
    MCP_TOOL_LOAD_ENV,
)
from web_router.mcp import methods

if TYPE_CHECKING:
    from web_router.mcp.tools import WebRouterTools


async def load_env_tool(tools: WebRouterTools, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Load the file's credentials, rebuild the router when one was new, and say what changed -- names only."""
    kwargs = tools.arguments(MCP_TOOL_LOAD_ENV, arguments)
    path, remember = kwargs[MCP_ARG_PATH], kwargs.get(MCP_ARG_REMEMBER, False)
    if not isinstance(path, str) or not isinstance(remember, bool):
        raise ValueError(MCP_MSG_ENV_BAD_ARGUMENTS)
    applied = tools.env.load(path, remember=remember)
    if applied.loaded:
        tools.rebuild()
    payload: dict[str, Any] = {"file": str(applied.path), "loaded": list(applied.loaded)}
    if applied.already_set:
        payload["already_set"] = list(applied.already_set)
    payload["configured"] = methods.configured()
    notes = [] if applied.loaded else [MCP_MSG_ENV_NOTHING_NEW]
    if applied.remembered is not None:
        notes.append(MCP_MSG_ENV_REMEMBERED.format(pointer=applied.remembered))
    if notes:
        payload["note"] = " ".join(notes)
    return payload
