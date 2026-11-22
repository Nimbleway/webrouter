"""Which tools the server lists and serves: the operator's allowlist, else the profile's.

Every tool listed is read on every turn of a conversation, and some drive the
user's own browser or accounts, so the default ``slim`` profile serves only the
tools that read the web (:data:`MCP_PROFILE_SLIM_TOOLS`). ``full`` serves every
one, and an allowlist in :data:`ENV_MCP_TOOLS` wins over both. A tool left out
is unknown to this server, listed or called. A profile's tool with nothing to
run on here is not listed (:func:`unrunnable`), though a call still says which
key it needs.
"""

from __future__ import annotations

import fnmatch
import logging
import os
from collections.abc import Collection, Mapping
from typing import TypeVar

from web_router.constants import MCP_BROWSER_TOOLS, MCP_TOOL_BROWSER_OPEN
from web_router.constants_live_data import MCP_METHOD_LIVE_DATA
from web_router.constants_mcp_context import (
    ENV_MCP_TOOLS,
    MCP_KEYLESS_TOOLS,
    MCP_MSG_TOOLS_UNKNOWN,
    MCP_PROFILE_SLIM_TOOLS,
    MCP_TOOL_PARTNERS,
    MCP_TOOLS_SEPARATOR,
)
from web_router.constants_mcp_env import (
    ENV_MCP_PROFILE,
    MCP_PROFILE_FULL,
)
from web_router.live_data_backends import custom_live_data_backends
from web_router.mcp import methods
from web_router.mcp.env_policy import switch

_T = TypeVar("_T")

_LOG = logging.getLogger(__name__)

_OWNER = {partner: tool for tool, partner in MCP_TOOL_PARTNERS.items()} | dict.fromkeys(
    MCP_BROWSER_TOOLS, MCP_TOOL_BROWSER_OPEN
)
"""The tool whose backend each follow-up tool needs: a poll its run's, a browser action the browser's."""


def allowed(handlers: Mapping[str, _T], value: str | None = None) -> dict[str, _T]:
    """``handlers`` cut to the tools the allowlist names, in their own order; the profile's with none set.

    ``value`` is the allowlist, read from :data:`ENV_MCP_TOOLS` when omitted. An
    entry that names no tool is ignored, with a warning on stderr, the log a stdio
    client keeps. A named tool brings its partner (:data:`MCP_TOOL_PARTNERS`), so a
    research run can still be polled and a browser closed.

    Raises:
        ConfigurationError: for a value :data:`ENV_MCP_PROFILE` does not take.
    """
    patterns = _patterns(value)
    if not patterns:
        if switch(ENV_MCP_PROFILE) == MCP_PROFILE_FULL:
            return dict(handlers)
        return {name: handler for name, handler in handlers.items() if name in MCP_PROFILE_SLIM_TOOLS}
    if unknown := [pattern for pattern in patterns if not fnmatch.filter(handlers, pattern)]:
        _LOG.warning(MCP_MSG_TOOLS_UNKNOWN, ENV_MCP_TOOLS, ", ".join(unknown), ", ".join(handlers))
    named = {name for name in handlers if any(fnmatch.fnmatchcase(name, p) for p in patterns)}
    named |= {MCP_TOOL_PARTNERS[name] for name in named if name in MCP_TOOL_PARTNERS}
    return {name: handler for name, handler in handlers.items() if name in named}


def unrunnable(names: Collection[str], *, browser: bool) -> frozenset[str]:
    """The tools among ``names`` with nothing to run on here, left out of the tool list.

    A tool is unrunnable when its method -- or, for a poll or a browser action, its owner's -- has no backend
    configured, and every browser tool is when ``browser`` (Playwright installed) is false. A tool an allowlist
    names is listed anyway, its description saying nothing serves it; the capabilities resource's ``unlock``
    names the key each would need either way.
    """
    hidden = set() if _patterns(None) else {name for name in names if not _runnable(_OWNER.get(name, name))}
    return frozenset(hidden | (set() if browser else set(MCP_BROWSER_TOOLS) & set(names)))


def _runnable(tool: str) -> bool:
    method = methods.METHODS.get(tool)
    if method is None or tool in MCP_KEYLESS_TOOLS or methods.usable(method):
        return True
    return method.key == MCP_METHOD_LIVE_DATA and bool(custom_live_data_backends())


def _patterns(value: str | None) -> list[str]:
    value = os.environ.get(ENV_MCP_TOOLS, "") if value is None else value
    return [entry.strip() for entry in value.split(MCP_TOOLS_SEPARATOR) if entry.strip()]
