"""What the ``.env`` tests share: two fake keys, the switches opened up, a file writer, and checks that never print
``os.environ``."""

from __future__ import annotations

import os
from pathlib import Path

from web_router.constants_mcp_env import (
    ENV_MCP_LOAD_ENV,
    ENV_MCP_PRIVATE_URLS,
    ENV_MCP_PROFILE,
    MCP_LOAD_ENV_REMEMBER,
    MCP_PRIVATE_URLS_ON,
    MCP_PROFILE_FULL,
)
from web_router.mcp.env import EnvSources

KEY = "not-a-real-key-0123"
OTHER_KEY = "not-a-real-key-4567"

OPENED_UP = {
    ENV_MCP_PROFILE: MCP_PROFILE_FULL,
    ENV_MCP_LOAD_ENV: MCP_LOAD_ENV_REMEMBER,
    ENV_MCP_PRIVATE_URLS: MCP_PRIVATE_URLS_ON,
}
"""Every tool served and every switch open, as ``tests/mcp`` runs by default, so its modules test what the tools
do; ``test_safe_defaults`` and the HTTP fixtures test the defaults themselves."""


# Assert through is_set and holds, never on os.environ itself: pytest prints the operands of
# a failed assertion, and os.environ's repr is every variable this process holds,
# the developer's real keys included.
def is_set(name: str) -> bool:
    return name in os.environ


def holds(name: str, value: str) -> bool:
    return os.environ.get(name) == value


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def statuses(sources: EnvSources) -> list[tuple[str, str]]:
    return [(lookup.source, lookup.status) for lookup in sources.searched]
