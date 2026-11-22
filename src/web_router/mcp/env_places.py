"""The places the startup lookup reads, in order, and which of them the operator's switches leave out.

Split from :mod:`web_router.mcp.env`, which reads each place this names.
"""

from __future__ import annotations

import os
from pathlib import Path

from web_router.constants import (
    ENV_MCP_ENV_FILE,
    MCP_ENV_FILE,
    MCP_ENV_SOURCE_CWD,
    MCP_ENV_SOURCE_EXPLICIT,
    MCP_ENV_SOURCE_REMEMBERED,
    MCP_ENV_SOURCE_USER,
)
from web_router.constants_mcp_env import (
    ENV_MCP_CWD_ENV,
    ENV_MCP_REMEMBERED_ENV,
    MCP_CWD_ENV_OFF,
    MCP_ENV_REASON_OUTSIDE_PROJECT,
    MCP_ENV_REASON_SWITCHED_OFF,
    MCP_REMEMBERED_OFF,
    MCP_REMEMBERED_PROJECT,
)
from web_router.mcp import env_pointer
from web_router.mcp.env_foreign import in_project
from web_router.mcp.env_policy import switch
from web_router.mcp.env_read import absolute

Place = tuple[str, Path, str | None]
"""A source's name, its path, and why the lookup skips it -- ``None`` when it is read."""


def places() -> list[Place]:
    """Every place the startup lookup knows, in order, each with the reason a switch skips it, if one does.

    Raises:
        ConfigurationError: for a switch set to a value it does not take.
    """
    found: list[Place] = []
    scope, cwd = switch(ENV_MCP_REMEMBERED_ENV), switch(ENV_MCP_CWD_ENV)  # read first: a bad value always stops
    explicit = os.environ.get(ENV_MCP_ENV_FILE)
    if explicit:
        found.append((MCP_ENV_SOURCE_EXPLICIT, absolute(explicit), None))
    found.append((MCP_ENV_SOURCE_USER, env_pointer.user_config_dir() / MCP_ENV_FILE, None))
    remembered = env_pointer.remembered()
    if remembered is not None:
        # After the user .env: a conversation recorded it, and may have been steered to.
        found.append((MCP_ENV_SOURCE_REMEMBERED, remembered, _remembered_skip(remembered, scope)))
    skip_cwd = MCP_ENV_REASON_SWITCHED_OFF.format(name=ENV_MCP_CWD_ENV, value=cwd) if cwd == MCP_CWD_ENV_OFF else None
    found.append((MCP_ENV_SOURCE_CWD, Path.cwd() / MCP_ENV_FILE, skip_cwd))
    return found


def _remembered_skip(path: Path, scope: str) -> str | None:
    if scope == MCP_REMEMBERED_OFF:
        return MCP_ENV_REASON_SWITCHED_OFF.format(name=ENV_MCP_REMEMBERED_ENV, value=scope)
    if scope == MCP_REMEMBERED_PROJECT and not in_project(path):
        return MCP_ENV_REASON_OUTSIDE_PROJECT.format(name=ENV_MCP_REMEMBERED_ENV, value=scope)
    return None
