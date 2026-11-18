"""What the startup lookup says on stderr, the log a stdio client keeps, for the person who runs the server.

The capabilities resource and the server instructions tell the model; these
tell the operator, who may never see either: keys from a remembered file, a
``WEB_ROUTER_ENV_FILE`` that names nothing, a key file others can read, and
billed keys with no ceiling. Each names paths and variables, never a value.
"""

from __future__ import annotations

import logging
import os
import stat
from pathlib import Path
from typing import TYPE_CHECKING

from web_router.constants import (
    ENV_MCP_ENV_FILE,
    MCP_ENV_SOURCE_EXPLICIT,
    MCP_ENV_STATUS_NOTHING_NEW,
    MCP_ENV_STATUS_USED,
)
from web_router.constants_mcp import ENV_MCP_MAX_COST_USD
from web_router.constants_mcp_env import (
    MCP_LOG_ENV_EXPLICIT_MISSING,
    MCP_LOG_ENV_LOOSE_PERMISSIONS,
    MCP_LOG_ENV_REMEMBERED,
    MCP_LOG_NO_CEILING,
    MCP_MSG_ENV_LOOSE_PERMISSIONS,
)
from web_router.mcp import env_pointer, methods
from web_router.mcp.env_foreign import remembered_billed

if TYPE_CHECKING:
    from web_router.mcp.env import EnvSources

_LOG = logging.getLogger(__name__)
_READ = frozenset({MCP_ENV_STATUS_USED, MCP_ENV_STATUS_NOTHING_NEW})
"""The statuses of a file that held credentials: whether or not they were new, the file holds keys."""


def log_startup(sources: EnvSources) -> None:
    """Warn on stderr about each thing the startup lookup found that the operator should know."""
    for path in remembered_billed(sources.searched, sources.startup):
        _LOG.warning(MCP_LOG_ENV_REMEMBERED, path, env_pointer.pointer_file())
    for lookup in sources.searched:
        if lookup.source == MCP_ENV_SOURCE_EXPLICIT and sources.explicit_missing:
            _LOG.warning(MCP_LOG_ENV_EXPLICIT_MISSING, ENV_MCP_ENV_FILE, lookup.path)
        if lookup.status in _READ and (note := loose_permissions(lookup.path)):
            _LOG.warning(MCP_LOG_ENV_LOOSE_PERMISSIONS, note)
    keyed = any(methods.keyed(method) for method in methods.EACH_METHOD)
    if keyed and not os.environ.get(ENV_MCP_MAX_COST_USD, "").strip():
        _LOG.warning(MCP_LOG_NO_CEILING, ENV_MCP_MAX_COST_USD)


def loose_permissions(path: Path) -> str | None:
    """A sentence saying ``path`` is readable by others, or ``None`` when it is not (or the OS has no such bits)."""
    if os.name != "posix":
        return None
    try:
        mode = path.stat().st_mode
    except OSError:
        return None
    return MCP_MSG_ENV_LOOSE_PERMISSIONS.format(path=path) if mode & (stat.S_IRWXG | stat.S_IRWXO) else None
