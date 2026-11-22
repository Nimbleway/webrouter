"""The ``load_env`` tool: provider keys from a ``.env`` file the user names, by path -- or forgetting one.

Kept out of :mod:`web_router.mcp.tools`, which is at its size limit. The file
rules are :mod:`web_router.mcp.env`'s; this is the tool's side of them, and of
the operator's :data:`~web_router.constants_mcp_env.ENV_MCP_LOAD_ENV`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from functools import partial
from typing import TYPE_CHECKING, Any

from web_router.constants import MCP_MSG_ENV_NOTHING_NEW, MCP_MSG_ENV_REMEMBERED, MCP_TOOL_LOAD_ENV
from web_router.constants_mcp_env import (
    ENV_MCP_LOAD_ENV,
    MCP_ARG_FORGET,
    MCP_ARG_PATH,
    MCP_ARG_REMEMBER,
    MCP_LOAD_ENV_REMEMBER,
    MCP_MSG_ENV_BAD_ARGUMENTS,
    MCP_MSG_ENV_FORGOTTEN,
    MCP_MSG_ENV_NOTHING_TO_FORGET,
    MCP_MSG_ENV_REMEMBER_OFF,
)
from web_router.mcp import methods
from web_router.mcp.env_policy import switch
from web_router.mcp.env_warnings import loose_permissions
from web_router.mcp.key_text import load_env_served

if TYPE_CHECKING:
    from web_router.mcp.tools import WebRouterTools


def load_env_entry(tools: WebRouterTools) -> dict[str, Callable[[Mapping[str, Any]], Awaitable[dict[str, Any]]]]:
    """``load_env``'s handler, keyed by its name -- or nothing when the operator turned it off.

    Off, the tool is neither listed nor callable, so no key arrives through a conversation.

    Raises:
        ConfigurationError: for a value :data:`ENV_MCP_LOAD_ENV` does not take.
    """
    if not load_env_served():
        return {}
    return {MCP_TOOL_LOAD_ENV: partial(load_env_tool, tools)}


async def load_env_tool(tools: WebRouterTools, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Load the file's credentials, rebuild the router when one was new, and say what changed -- names only."""
    kwargs = tools.arguments(MCP_TOOL_LOAD_ENV, arguments)
    path, remember, forget = kwargs.get(MCP_ARG_PATH), kwargs.get(MCP_ARG_REMEMBER, False), kwargs.get(MCP_ARG_FORGET)
    if forget is True and path is None and not remember:
        return _forget(tools)
    if not isinstance(path, str) or not isinstance(remember, bool) or forget not in (None, False):
        raise ValueError(MCP_MSG_ENV_BAD_ARGUMENTS)
    if remember and (mode := switch(ENV_MCP_LOAD_ENV)) != MCP_LOAD_ENV_REMEMBER:
        raise ValueError(MCP_MSG_ENV_REMEMBER_OFF.format(name=ENV_MCP_LOAD_ENV, value=mode))
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
    if loose := loose_permissions(applied.path):
        notes.append(loose)
    if notes:
        payload["note"] = " ".join(notes)
    return payload


def _forget(tools: WebRouterTools) -> dict[str, Any]:
    forgotten = tools.env.forget()
    if forgotten is None:
        return {"forgotten": None, "note": MCP_MSG_ENV_NOTHING_TO_FORGET}
    return {"forgotten": str(forgotten), "note": MCP_MSG_ENV_FORGOTTEN.format(path=forgotten)}
