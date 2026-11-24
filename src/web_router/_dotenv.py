"""Spot a ``.env`` the caller expected the library to read: the hint on a :class:`~web_router.ConfigurationError`.

The library reads the process environment only (the MCP server loads a ``.env`` into it; nothing else does).
Agents and people alike put keys in a ``.env`` and then read "No API key" as a broken setup. When the
variable an error names is set in the nearest ``.env`` but missing from the environment, the error says so.
The file is parsed by hand because python-dotenv is only in the ``mcp`` extra.
"""

from __future__ import annotations

import os
import re
import shlex
from pathlib import Path

from web_router.constants import MCP_ENV_FILE_MAX_BYTES
from web_router.constants_dotenv import DOTENV_FILENAME, MSG_DOTENV_NOT_READ, MSG_DOTENV_PREFIX

_ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")
_VARIABLE = re.compile(r"\b[A-Z][A-Z0-9]*_[A-Z0-9_]+\b")  # an underscore, so "API" in "No API key" is not one


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


def _value(raw: str) -> str:
    """An assignment's value: what a pair of quotes holds, else the text before an inline `` #`` comment."""
    if raw[:1] in ("'", '"'):
        end = raw.find(raw[0], 1)
        return raw[1:end] if end > 0 else raw[1:]
    return "" if raw.startswith("#") else raw.split(" #", 1)[0].strip()


def _set_in(path: Path) -> set[str]:
    """The variables ``path`` gives a non-empty value; an unreadable or oversized file sets none."""
    try:
        if path.stat().st_size > MCP_ENV_FILE_MAX_BYTES:
            return set()
        lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    except OSError:
        return set()
    found = (_ASSIGNMENT.match(line) for line in lines)
    return {match[1] for match in found if match and _value(match[2])}


def dotenv_hint(message: str, start: Path | None = None) -> str:
    """:data:`MSG_DOTENV_NOT_READ` for the first variable ``message`` names that an unread ``.env`` sets, or ""."""
    if MSG_DOTENV_PREFIX in message:  # already added, as on an unpickled error
        return ""
    path = nearest_dotenv(start)
    if path is None:
        return ""
    unread = _set_in(path) - {name for name, value in os.environ.items() if value.strip()}
    named = next((name for name in _VARIABLE.findall(message) if name in unread), None)
    if named is None:
        return ""
    return MSG_DOTENV_NOT_READ.format(path=path, quoted_path=shlex.quote(str(path)), env_var=named)


def with_dotenv_hint(args: tuple[object, ...]) -> tuple[object, ...]:
    """``args`` with the hint appended when they are a single message string."""
    if len(args) == 1 and isinstance(args[0], str):
        return (args[0] + dotenv_hint(args[0]),)
    return args
