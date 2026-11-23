"""The capabilities resource's profile report, separate from tool selection."""

from __future__ import annotations

from collections.abc import Collection
from typing import Any

from web_router.constants import MCP_TOOL_LOAD_ENV
from web_router.constants_mcp_context import ENV_MCP_TOOLS, MCP_MSG_ALLOWLIST_SWITCH, MCP_MSG_PROFILE_SWITCH
from web_router.constants_mcp_env import (
    ENV_MCP_LOAD_ENV,
    ENV_MCP_PROFILE,
    MCP_LOAD_ENV_LOAD,
    MCP_MSG_LOAD_ENV_OFF,
    MCP_PROFILE_ALLOWLIST,
)
from web_router.mcp.allowlist import allowlist_set
from web_router.mcp.env_policy import switch


def report(every: Collection[str], served: Collection[str]) -> dict[str, Any]:
    """The capabilities resource's ``tools``: the profile, the tools it left off, and the switch that adds them."""
    profile = MCP_PROFILE_ALLOWLIST if allowlist_set() else switch(ENV_MCP_PROFILE)
    tools: dict[str, Any] = {"profile": profile}
    if off := [name for name in every if name not in served]:
        tools["off"] = off
        message = MCP_MSG_ALLOWLIST_SWITCH if profile == MCP_PROFILE_ALLOWLIST else MCP_MSG_PROFILE_SWITCH
        tools["switch"] = message.format(profile=ENV_MCP_PROFILE, tools=ENV_MCP_TOOLS)
    if MCP_TOOL_LOAD_ENV not in every:
        tools[MCP_TOOL_LOAD_ENV] = MCP_MSG_LOAD_ENV_OFF.format(name=ENV_MCP_LOAD_ENV, value=MCP_LOAD_ENV_LOAD)
    return tools
