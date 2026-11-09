"""What the ``.env`` tests share: two fake keys, a file writer, and checks that never print ``os.environ``."""

from __future__ import annotations

import os
from pathlib import Path

from web_router.mcp.env import EnvSources

KEY = "not-a-real-key-0123"
OTHER_KEY = "not-a-real-key-4567"


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
