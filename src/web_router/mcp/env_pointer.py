"""The user config directory, and the record ``load_env(remember=true)`` keeps there: a path, never a key.

Split from :mod:`web_router.mcp.env`. The record is written owner-only
(``0600``): it names where this user's keys are, which is nobody else's business.
"""

from __future__ import annotations

import errno
import os
import sys
from pathlib import Path

from web_router.constants import (
    MCP_ENV_POINTER_FILE,
    MCP_MSG_ENV_REMEMBER_FAILED,
    MCP_USER_CONFIG_DIR,
    MCP_USER_CONFIG_ROOT_BY_PLATFORM,
    MCP_USER_CONFIG_ROOT_DEFAULT,
)
from web_router.constants_mcp_env import MCP_MSG_ENV_FORGET_FAILED
from web_router.mcp.env_read import absolute

_OWNER_ONLY = 0o600
_OWNER_ONLY_DIR = 0o700
_NO_FOLLOW = getattr(os, "O_NOFOLLOW", 0)
"""A symlink planted at the record's path is refused, not followed to whatever file it names."""


def user_config_dir() -> Path:
    """This user's web-router configuration directory, where the platform keeps such things.

    ``~/.config/web-router`` on Linux (or under ``XDG_CONFIG_HOME``),
    ``~/Library/Application Support/web-router`` on macOS, and
    ``%APPDATA%\\web-router`` on Windows. It need not exist.
    """
    env_var, default = MCP_USER_CONFIG_ROOT_BY_PLATFORM.get(sys.platform, MCP_USER_CONFIG_ROOT_DEFAULT)
    configured = os.environ.get(env_var, "") if env_var else ""
    root = configured if os.path.isabs(configured) else os.path.expanduser(default)
    return Path(root) / MCP_USER_CONFIG_DIR


def pointer_file() -> Path:
    """Where the remembered path is recorded."""
    return user_config_dir() / MCP_ENV_POINTER_FILE


def remembered() -> Path | None:
    """The path ``load_env(remember=true)`` recorded, or ``None`` when there is no readable record."""
    try:
        recorded = pointer_file().read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None
    return absolute(recorded) if recorded else None


def remember(target: Path) -> Path:
    """Record ``target`` for the next start, as a path rather than a copy, so no second copy of a key is written.

    Raises:
        ValueError: when the record cannot be written.
    """
    pointer = pointer_file()
    try:
        pointer.parent.mkdir(mode=_OWNER_ONLY_DIR, parents=True, exist_ok=True)
        if pointer.is_symlink():  # O_NOFOLLOW refuses it too; this says why where the platform has no such flag
            raise OSError(errno.ELOOP, os.strerror(errno.ELOOP))
        descriptor = os.open(pointer, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | _NO_FOLLOW, _OWNER_ONLY)
        with os.fdopen(descriptor, "w", encoding="utf-8") as record:
            # A record left by an older version keeps its old mode through O_CREAT; this narrows it.
            _narrow(record.fileno(), pointer)
            record.write(f"{target}\n")
    except OSError as exc:
        reason = exc.strerror or type(exc).__name__
        raise ValueError(MCP_MSG_ENV_REMEMBER_FAILED.format(pointer=pointer, reason=reason)) from exc
    return pointer


def _narrow(descriptor: int, pointer: Path) -> None:
    """Make the open record owner-only, through its descriptor where the platform allows, so no link is followed."""
    if hasattr(os, "fchmod"):
        os.fchmod(descriptor, _OWNER_ONLY)
    else:  # Windows: no fchmod, and no symlink reached this far
        os.chmod(pointer, _OWNER_ONLY)


def forget() -> Path | None:
    """Remove the record and return the path it named, or ``None`` when there was none.

    Raises:
        ValueError: when the record exists and cannot be removed.
    """
    target, pointer = remembered(), pointer_file()
    try:
        pointer.unlink()
    except FileNotFoundError:
        return None
    except OSError as exc:
        reason = exc.strerror or type(exc).__name__
        raise ValueError(MCP_MSG_ENV_FORGET_FAILED.format(pointer=pointer, reason=reason)) from exc
    return target if target is not None else pointer
