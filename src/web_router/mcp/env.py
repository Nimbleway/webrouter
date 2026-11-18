"""Where the server's provider credentials come from: its environment, then ``.env`` files.

The library never reads dotfiles, but this entry point is an application. An
MCP client starts it with the client's own environment, in a working directory
the user rarely chose, so keys nobody exported need another way in.
:meth:`EnvSources.at_startup` reads every one of four places, in this order:

1. the path in ``WEB_ROUTER_ENV_FILE``, which a client's config can set;
2. ``.env`` in the user config directory;
3. the file ``load_env(remember=true)`` recorded in the user config directory;
4. ``.env`` in the working directory -- the repo root, when ``.mcp.json``
   starts the server there.

An earlier place wins, per variable: a later file fills only what is still
unset. So a project's own ``.env``, in whatever directory a client started the
server in, and a remembered file -- a path a conversation recorded -- can only
add keys, never replace the user's. At runtime :meth:`EnvSources.load` reads
one more, from a path the user gives the model, so only a path ever passes
through a conversation.

The rules are the same wherever a file comes from. Only provider credentials
are read -- :func:`credential_variables` -- because a
server registered at user scope can start in any workspace, and a foreign file
must not flip a policy switch such as file-URL navigation or point
``CHROME_PATH`` at a binary of its choosing. A variable already set keeps its
value. And no value is ever returned, reported or put in an error message:
names and paths are.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from web_router.constants import (
    ENV_MCP_ENV_FILE,
    MCP_ENV_FILE,
    MCP_ENV_POINTER_FILE,
    MCP_ENV_SOURCE_CWD,
    MCP_ENV_SOURCE_EXPLICIT,
    MCP_ENV_SOURCE_REMEMBERED,
    MCP_ENV_SOURCE_USER,
    MCP_ENV_STATUS_NO_CREDENTIALS,
    MCP_ENV_STATUS_NOTHING_NEW,
    MCP_ENV_STATUS_USED,
    MCP_MSG_ENV_REMEMBER_FAILED,
    MCP_USER_CONFIG_DIR,
    MCP_USER_CONFIG_ROOT_BY_PLATFORM,
    MCP_USER_CONFIG_ROOT_DEFAULT,
)
from web_router.mcp.env_credentials import EnvLoad, apply_credentials, credential_variables
from web_router.mcp.env_foreign import foreign_files, foreign_warning, noted_instructions
from web_router.mcp.env_read import EnvFileError, absolute, read_credentials

__all__ = ["EnvFileError", "EnvLoad", "EnvLookup", "EnvSources", "read_credentials", "user_config_dir"]


def _set_in_environment() -> tuple[str, ...]:
    return tuple(sorted(name for name in credential_variables() if os.environ.get(name)))


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


@dataclass(frozen=True)
class EnvLookup:
    """One place the startup lookup looked, and what it found there."""

    source: str
    path: Path
    status: str
    reason: str | None = None


@dataclass
class EnvSources:
    """Where this server's provider credentials came from, and the loads that put them there.

    Built empty, it records only which credentials were already in the
    environment: :meth:`at_startup` is what looks for a file.
    """

    from_environment: tuple[str, ...] = field(default_factory=_set_in_environment)
    searched: list[EnvLookup] = field(default_factory=list)
    startup: list[EnvLoad] = field(default_factory=list)
    explicit_missing: bool = False
    runtime: list[EnvLoad] = field(default_factory=list)

    @classmethod
    def at_startup(cls) -> EnvSources:
        """Read every place, in order, each file filling only the credentials still unset."""
        sources = cls()
        for source, path in _candidates():
            try:
                values = read_credentials(path)
            except EnvFileError as exc:
                sources.searched.append(EnvLookup(source, path, exc.status, exc.reason))
                if source == MCP_ENV_SOURCE_EXPLICIT and exc.status != MCP_ENV_STATUS_NO_CREDENTIALS:
                    sources.explicit_missing = True
                continue
            applied = apply_credentials(path, values)
            sources.startup.append(applied)
            status = MCP_ENV_STATUS_USED if applied.loaded else MCP_ENV_STATUS_NOTHING_NEW
            sources.searched.append(EnvLookup(source, path, status))
        return sources

    def load(self, path: str, *, remember: bool = False) -> EnvLoad:
        """Load the credentials ``path`` sets into this process's environment, and optionally remember it.

        The path is recorded before anything is loaded, so a failure to record
        it leaves the environment as it was.

        Raises:
            EnvFileError: for a path with no file, one that cannot be read as
                text, or one that sets no credential.
            ValueError: when ``remember`` is set and the path cannot be recorded.
        """
        target = absolute(path)
        values = read_credentials(target)
        pointer = _remember(target) if remember else None
        applied = apply_credentials(target, values, remembered=pointer)
        self.runtime.append(applied)
        return applied

    def foreign(self) -> list[Path]:
        """The remembered files from outside the working directory that loaded a billed key."""
        return foreign_files(self.searched, self.startup)

    def noted(self, instructions: str) -> str:
        """The server's ``instructions``, with a warning added when :meth:`foreign` found a file."""
        return noted_instructions(instructions, self.foreign())

    def report(self) -> dict[str, Any]:
        """The ``env`` object of the capabilities resource: places, files and variable names -- never a value.

        A remembered file from another directory leads it as a ``warning``, so a
        model reading the resource sees it before anything else.
        """
        warning = foreign_warning(self.foreign(), user_config_dir() / MCP_ENV_POINTER_FILE)
        return {
            **warning,
            "searched": [_lookup_report(lookup) for lookup in self.searched],
            # Each variable a file set at startup, and the file it came from.
            "loaded": {name: str(load.path) for load in self.startup for name in load.loaded},
            "explicit_file_missing": self.explicit_missing,
            "from_environment": list(self.from_environment),
            "loaded_at_runtime": [{"file": str(load.path), "loaded": list(load.loaded)} for load in self.runtime],
        }


def _candidates() -> list[tuple[str, Path]]:
    """Every place the startup lookup reads, in order."""
    places: list[tuple[str, Path]] = []
    explicit = os.environ.get(ENV_MCP_ENV_FILE)
    if explicit:
        places.append((MCP_ENV_SOURCE_EXPLICIT, absolute(explicit)))
    config = user_config_dir()
    places.append((MCP_ENV_SOURCE_USER, config / MCP_ENV_FILE))
    remembered = _remembered(config / MCP_ENV_POINTER_FILE)
    if remembered is not None:
        # After the user .env: a conversation recorded it, and may have been steered to.
        places.append((MCP_ENV_SOURCE_REMEMBERED, remembered))
    places.append((MCP_ENV_SOURCE_CWD, Path.cwd() / MCP_ENV_FILE))
    return places


def _remembered(pointer: Path) -> Path | None:
    try:
        recorded = pointer.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None
    return absolute(recorded) if recorded else None


def _remember(target: Path) -> Path:
    """Record ``target`` for the next start, as a path rather than a copy, so no second copy of a key is written."""
    pointer = user_config_dir() / MCP_ENV_POINTER_FILE
    try:
        pointer.parent.mkdir(parents=True, exist_ok=True)
        pointer.write_text(f"{target}\n", encoding="utf-8")
    except OSError as exc:
        reason = exc.strerror or type(exc).__name__
        raise ValueError(MCP_MSG_ENV_REMEMBER_FAILED.format(pointer=pointer, reason=reason)) from exc
    return pointer


def _lookup_report(lookup: EnvLookup) -> dict[str, Any]:
    report: dict[str, Any] = {"source": lookup.source, "path": str(lookup.path), "status": lookup.status}
    if lookup.reason is not None:
        report["reason"] = lookup.reason
    return report
