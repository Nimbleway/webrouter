"""What the operator lets a model's browser open beyond the fixed rules: ``file:`` URLs, and private addresses.

Apart from :mod:`web_router.mcp.navigation`, which enforces them, so that file stops growing. Both are read
from the real environment when a session opens.
"""

from __future__ import annotations

import os

from web_router._hosts import is_loopback, is_private
from web_router.constants import ENV_MCP_BROWSER_ALLOW_FILE_URLS, MCP_TRUE_VALUES
from web_router.constants_mcp_env import ENV_MCP_PRIVATE_URLS, MCP_MSG_BROWSER_REFUSED_PRIVATE, MCP_PRIVATE_URLS_OFF
from web_router.constants_urls import LOCALHOST_SUFFIX
from web_router.mcp.env_policy import switch


def files_allowed() -> bool:
    """Whether the operator has opted in to ``file:`` URLs through the environment."""
    return os.environ.get(ENV_MCP_BROWSER_ALLOW_FILE_URLS, "").strip().lower() in MCP_TRUE_VALUES


def private_allowed() -> bool:
    """Whether loopback and private-network addresses may be opened: yes unless the operator, or HTTP, said off.

    Raises:
        ConfigurationError: for a value :data:`ENV_MCP_PRIVATE_URLS` does not take.
    """
    return switch(ENV_MCP_PRIVATE_URLS) != MCP_PRIVATE_URLS_OFF


def private_refusal(host: str) -> str | None:
    """Why ``host`` -- a name or a resolved address -- is refused when private addresses are off, or ``None``.

    Any ``*.localhost`` name counts as loopback: Chromium answers it with loopback itself, without asking DNS.
    """
    name = host.lower().rstrip(".")
    if is_loopback(name) or name.endswith(LOCALHOST_SUFFIX) or is_private(host):
        return MCP_MSG_BROWSER_REFUSED_PRIVATE.format(env_var=ENV_MCP_PRIVATE_URLS)
    return None
