"""Which tools the server lists and serves: every one, or the operator's allowlist.

Every tool listed is read on every turn of a conversation, so an operator who
needs only ``search`` and ``extract`` can say so in :data:`ENV_MCP_TOOLS` and the
rest cost nothing. A tool left out is unknown to this server, listed or called.
"""

from __future__ import annotations

import fnmatch
import logging
import os
from collections.abc import Mapping
from typing import TypeVar

from web_router.constants_mcp_context import ENV_MCP_TOOLS, MCP_MSG_TOOLS_UNKNOWN, MCP_TOOLS_SEPARATOR

_T = TypeVar("_T")

_LOG = logging.getLogger(__name__)


def allowed(handlers: Mapping[str, _T], value: str | None = None) -> dict[str, _T]:
    """``handlers`` cut to the tools the allowlist names, in their own order; all of them with none set.

    ``value`` is the allowlist, read from :data:`ENV_MCP_TOOLS` when omitted. An
    entry that names no tool is ignored, with a warning on stderr, the log a stdio
    client keeps.
    """
    value = os.environ.get(ENV_MCP_TOOLS, "") if value is None else value
    patterns = [entry.strip() for entry in value.split(MCP_TOOLS_SEPARATOR) if entry.strip()]
    if not patterns:
        return dict(handlers)
    if unknown := [pattern for pattern in patterns if not fnmatch.filter(handlers, pattern)]:
        _LOG.warning(MCP_MSG_TOOLS_UNKNOWN, ENV_MCP_TOOLS, ", ".join(unknown), ", ".join(handlers))
    return {name: handler for name, handler in handlers.items() if any(fnmatch.fnmatchcase(name, p) for p in patterns)}
