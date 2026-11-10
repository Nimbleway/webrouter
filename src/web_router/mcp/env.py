"""Where the server's provider credentials come from: its environment, then ``.env`` files.

The library never reads dotfiles, but this entry point is an application. An
MCP client starts it with the client's own environment, in a working directory
the user rarely chose, so keys nobody exported need another way in.
:meth:`EnvSources.at_startup` reads every one of four places, in this order:

1. the path in ``WEB_ROUTER_ENV_FILE``, which a client's config can set;
2. ``.env`` in the user config directory;
3. the file ``load_env(remember=true)`` recorded in the user config directory;
4. the path in ``WEB_ROUTER_PROJECT_ENV_FILE``, which a project's own client
   config sets -- this repo's ``.mcp.json`` sets ``.env``. A relative path is
   taken from the working directory, and a missing file is no error.

A ``.env`` that merely sits in the working directory is not read: a server
registered at user scope starts in whatever repo is open.

An earlier place wins, per variable: a later file fills only what is still
unset. So a remembered file -- a path a conversation recorded -- and a
project's ``.env`` can only add keys, never replace the user's. At runtime
:meth:`EnvSources.load` reads one more, from a path the user gives the model,
so only a path ever passes through a conversation.

The rules are the same wherever a file comes from. Only provider keys are
read -- :func:`file_variables` -- because a file a model was pointed at, a
remembered one or a project's must not flip a policy switch such as file-URL
navigation, point ``CHROME_PATH`` at a binary of its choosing or set
``AGENTCORE_GATEWAY_URL``, which is read from the environment only
(:data:`ENV_ONLY`). A variable already set keeps its value. And no value is
ever returned, reported or put in an error message: names and paths are.
"""

from __future__ import annotations

import io
import os
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from web_router.constants import (
    ENV_MCP_ENV_FILE,
    MCP_ENV_FILE,
    MCP_ENV_FILE_MAX_BYTES,
    MCP_ENV_POINTER_FILE,
    MCP_ENV_REASON_NO_DOTENV,
    MCP_ENV_REASON_NOT_A_FILE,
    MCP_ENV_REASON_NOT_TEXT,
    MCP_ENV_REASON_TOO_LARGE,
    MCP_ENV_SOURCE_EXPLICIT,
    MCP_ENV_SOURCE_REMEMBERED,
    MCP_ENV_SOURCE_USER,
    MCP_ENV_STATUS_MISSING,
    MCP_ENV_STATUS_NO_CREDENTIALS,
    MCP_ENV_STATUS_NOTHING_NEW,
    MCP_ENV_STATUS_UNREADABLE,
    MCP_ENV_STATUS_USED,
    MCP_MSG_ENV_HINT,
    MCP_MSG_ENV_MISSING,
    MCP_MSG_ENV_NO_CREDENTIALS,
    MCP_MSG_ENV_REMEMBER_FAILED,
    MCP_MSG_ENV_UNREADABLE,
    MCP_USER_CONFIG_DIR,
    MCP_USER_CONFIG_ROOT_BY_PLATFORM,
    MCP_USER_CONFIG_ROOT_DEFAULT,
)
from web_router.constants_mcp import (
    ENV_MCP_PROJECT_ENV_FILE,
    MCP_ENV_SOURCE_PROJECT,
    MCP_MSG_ENV_HINT_EXPLICIT_MISSING,
    MCP_MSG_ENV_HINT_EXPLICIT_UNREADABLE,
    MCP_MSG_ENV_HINT_EXPLICIT_UNUSED,
    MCP_MSG_ENV_ONLY_FILE,
)
from web_router.mcp.env_credentials import (
    ENV_ONLY,
    EnvLoad,
    apply_credentials,
    credential_variables,
    env_only_notes,
    file_variables,
)


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


class EnvFileError(ValueError):
    """A file the server was pointed at and could not use. Its message names the path and why, never a value."""

    def __init__(self, message: str, *, status: str, reason: str | None = None, env_only: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.status = status
        self.reason = reason
        self.env_only = env_only


@dataclass(frozen=True)
class EnvLookup:
    """One place the startup lookup looked, and what it found there."""

    source: str
    path: Path
    status: str
    reason: str | None = None
    notes: tuple[str, ...] = ()


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
                values, env_only = read_credentials(path)
            except EnvFileError as exc:
                notes = tuple(env_only_notes(exc.env_only))
                sources.searched.append(EnvLookup(source, path, exc.status, exc.reason, notes))
                if source == MCP_ENV_SOURCE_EXPLICIT and exc.status != MCP_ENV_STATUS_NO_CREDENTIALS:
                    sources.explicit_missing = True
                continue
            applied = apply_credentials(path, values, env_only=env_only)
            sources.startup.append(applied)
            status = MCP_ENV_STATUS_USED if applied.loaded else MCP_ENV_STATUS_NOTHING_NEW
            sources.searched.append(EnvLookup(source, path, status, notes=tuple(applied.notes())))
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
        target = _absolute(path)
        values, env_only = read_credentials(target)
        pointer = _remember(target) if remember else None
        applied = apply_credentials(target, values, remembered=pointer, env_only=env_only)
        self.runtime.append(applied)
        return applied

    def report(self) -> dict[str, Any]:
        """The ``env`` object of the capabilities resource: places, files and variable names -- never a value."""
        return {
            "searched": [_lookup_report(lookup) for lookup in self.searched],
            # Each variable a file set at startup, and the file it came from.
            "loaded": {name: str(load.path) for load in self.startup for name in load.loaded},
            "explicit_file_missing": self.explicit_missing,
            "from_environment": list(self.from_environment),
            "loaded_at_runtime": [{"file": str(load.path), "loaded": list(load.loaded)} for load in self.runtime],
        }

    def hint(self) -> str:
        """How to add a key, for a capabilities resource that lists only the keyless backends."""
        for lookup in self.searched:
            # A file named by WEB_ROUTER_ENV_FILE or the project's variable is where keys belong: point there.
            if lookup.source == MCP_ENV_SOURCE_EXPLICIT:
                env_var = ENV_MCP_ENV_FILE
            elif lookup.source == MCP_ENV_SOURCE_PROJECT and lookup.status != MCP_ENV_STATUS_NOTHING_NEW:
                env_var = ENV_MCP_PROJECT_ENV_FILE
            else:
                continue
            if lookup.status == MCP_ENV_STATUS_MISSING:
                return MCP_MSG_ENV_HINT_EXPLICIT_MISSING.format(env_var=env_var, path=lookup.path)
            if lookup.status == MCP_ENV_STATUS_UNREADABLE:
                return MCP_MSG_ENV_HINT_EXPLICIT_UNREADABLE.format(
                    env_var=env_var, path=lookup.path, reason=lookup.reason
                )
            return MCP_MSG_ENV_HINT_EXPLICIT_UNUSED.format(env_var=env_var, path=lookup.path, status=lookup.status)
        return MCP_MSG_ENV_HINT.format(env_var=ENV_MCP_ENV_FILE, user_dir=user_config_dir())


def read_credentials(path: Path) -> tuple[dict[str, str], tuple[str, ...]]:
    """The provider keys ``path`` sets, and the :data:`ENV_ONLY` variables it set and that were left out.

    Refuses anything that is not a small text file before reading it, since a
    model can name any path on the machine.

    Raises:
        EnvFileError: for a path with no file, one that cannot be read as
            text or one that sets no key.
    """
    try:
        info = path.stat()
    except (FileNotFoundError, NotADirectoryError):
        raise EnvFileError(MCP_MSG_ENV_MISSING.format(path=path), status=MCP_ENV_STATUS_MISSING) from None
    except OSError as exc:
        raise _unreadable(path, exc.strerror or type(exc).__name__) from exc
    if not stat.S_ISREG(info.st_mode):
        raise _unreadable(path, MCP_ENV_REASON_NOT_A_FILE)
    if info.st_size > MCP_ENV_FILE_MAX_BYTES:
        raise _unreadable(path, MCP_ENV_REASON_TOO_LARGE.format(limit=MCP_ENV_FILE_MAX_BYTES))
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        # Not chained: the decode error quotes a byte of the file.
        raise _unreadable(path, MCP_ENV_REASON_NOT_TEXT) from None
    except OSError as exc:
        raise _unreadable(path, exc.strerror or type(exc).__name__) from exc
    try:
        from dotenv import dotenv_values
    except ImportError:
        # The mcp extra installs it; without it only exported variables reach the server.
        raise _unreadable(path, MCP_ENV_REASON_NO_DOTENV) from None
    # Not interpolated: `${OTHER}` would copy a secret the file never held into
    # a provider's key, and send it to that provider with every call.
    values = dotenv_values(stream=io.StringIO(text), interpolate=False)
    wanted = sorted(file_variables())
    # An empty assignment -- a copied .env.example -- carries no credential.
    found = {name: value for name in wanted if (value := values.get(name))}
    # Not one already exported: that value is kept, as for any variable already set.
    env_only = tuple(name for name in ENV_ONLY if values.get(name) and not os.environ.get(name))
    if not found:
        message = (
            MCP_MSG_ENV_ONLY_FILE.format(path=path, note=" ".join(env_only_notes(env_only)))
            if env_only
            else MCP_MSG_ENV_NO_CREDENTIALS.format(path=path, names=", ".join(wanted))
        )
        raise EnvFileError(message, status=MCP_ENV_STATUS_NO_CREDENTIALS, env_only=env_only)
    return found, env_only


def _candidates() -> list[tuple[str, Path]]:
    """Every place the startup lookup reads, in order."""
    places: list[tuple[str, Path]] = []
    explicit = os.environ.get(ENV_MCP_ENV_FILE)
    if explicit:
        places.append((MCP_ENV_SOURCE_EXPLICIT, _absolute(explicit)))
    config = user_config_dir()
    places.append((MCP_ENV_SOURCE_USER, config / MCP_ENV_FILE))
    remembered = _remembered(config / MCP_ENV_POINTER_FILE)
    if remembered is not None:
        # After the user .env: a conversation recorded it, and may have been steered to.
        places.append((MCP_ENV_SOURCE_REMEMBERED, remembered))
    project = os.environ.get(ENV_MCP_PROJECT_ENV_FILE)
    if project:
        # Last: the project's own file only adds keys, and never replaces the user's.
        places.append((MCP_ENV_SOURCE_PROJECT, _absolute(project)))
    return places


def _remembered(pointer: Path) -> Path | None:
    try:
        recorded = pointer.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None
    return _absolute(recorded) if recorded else None


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


def _absolute(raw: str) -> Path:
    return Path(os.path.abspath(os.path.expanduser(raw)))


def _unreadable(path: Path, reason: str) -> EnvFileError:
    return EnvFileError(
        MCP_MSG_ENV_UNREADABLE.format(path=path, reason=reason), status=MCP_ENV_STATUS_UNREADABLE, reason=reason
    )


def _lookup_report(lookup: EnvLookup) -> dict[str, Any]:
    report: dict[str, Any] = {"source": lookup.source, "path": str(lookup.path), "status": lookup.status}
    if lookup.reason is not None:
        report["reason"] = lookup.reason
    if lookup.notes:
        report["note"] = " ".join(lookup.notes)
    return report
