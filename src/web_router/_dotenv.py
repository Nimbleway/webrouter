"""Spot a ``.env`` the caller expected the library to read: the hint on a :class:`~web_router.ConfigurationError`.

The library reads the process environment only (the MCP server loads a ``.env`` into it; nothing else does).
Agents and people alike put keys in a ``.env`` and then read "No API key" as a broken setup. When the
variable an error names is set in the nearest ``.env`` but missing from the environment, the error says so.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from web_router.constants_dotenv import DOTENV_FILENAME, MSG_DOTENV_NOT_READ

_ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")
_VARIABLE = re.compile(r"\b[A-Z][A-Z0-9_]*\b")


def nearest_dotenv(start: Path | None = None) -> Path | None:
    """The ``.env`` in ``start`` (the working directory by default) or its nearest parent that has one."""
    try:
        here = (start or Path.cwd()).resolve()
    except OSError:  # a working directory deleted under the process
        return None
    for directory in (here, *here.parents):
        candidate = directory / DOTENV_FILENAME
        if candidate.is_file():
            return candidate
    return None


def _set_in(path: Path) -> set[str]:
    """The variables ``path`` gives a non-empty value; an unreadable file sets none."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return set()
    found = (_ASSIGNMENT.match(line) for line in lines)
    return {match[1] for match in found if match and match[2].strip("'\"")}


def dotenv_hint(message: str, start: Path | None = None) -> str:
    """:data:`MSG_DOTENV_NOT_READ` for the first variable ``message`` names that an unread ``.env`` sets, or ""."""
    if DOTENV_FILENAME + " exists at" in message:  # already added, as on an unpickled error
        return ""
    path = nearest_dotenv(start)
    if path is None:
        return ""
    unread = _set_in(path) - {name for name, value in os.environ.items() if value.strip()}
    named = next((name for name in _VARIABLE.findall(message) if name in unread), None)
    return "" if named is None else MSG_DOTENV_NOT_READ.format(path=path, env_var=named)
