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

import io
import os
import stat
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from web_router.constants import (
    ANSWER_ENV_BY_PROVIDER,
    BROWSE_ENV_BY_PROVIDER,
    BROWSER_ENV_BY_PROVIDER,
    ENV_BY_PROVIDER,
    ENV_JINA_API_KEY,
    ENV_MCP_ENV_FILE,
    EXTRACT_ENV_BY_PROVIDER,
    MCP_ENV_FILE,
    MCP_ENV_FILE_MAX_BYTES,
    MCP_ENV_POINTER_FILE,
    MCP_ENV_REASON_NO_DOTENV,
    MCP_ENV_REASON_NOT_A_FILE,
    MCP_ENV_REASON_NOT_TEXT,
    MCP_ENV_REASON_TOO_LARGE,
    MCP_ENV_SOURCE_CWD,
    MCP_ENV_SOURCE_EXPLICIT,
    MCP_ENV_SOURCE_REMEMBERED,
    MCP_ENV_SOURCE_USER,
    MCP_ENV_STATUS_MISSING,
    MCP_ENV_STATUS_NO_CREDENTIALS,
    MCP_ENV_STATUS_NOTHING_NEW,
    MCP_ENV_STATUS_UNREADABLE,
    MCP_ENV_STATUS_USED,
    MCP_MSG_ENV_MISSING,
    MCP_MSG_ENV_NO_CREDENTIALS,
    MCP_MSG_ENV_REMEMBER_FAILED,
    MCP_MSG_ENV_UNREADABLE,
    MCP_USER_CONFIG_DIR,
    MCP_USER_CONFIG_ROOT_BY_PLATFORM,
    MCP_USER_CONFIG_ROOT_DEFAULT,
    RESEARCH_ENV_BY_PROVIDER,
    TEMPLATE_ENV_BY_PROVIDER,
)

ALL_ENV_TABLES: tuple[Mapping[str, str | tuple[str, ...] | None], ...] = (
    ENV_BY_PROVIDER,
    EXTRACT_ENV_BY_PROVIDER,
    TEMPLATE_ENV_BY_PROVIDER,
    ANSWER_ENV_BY_PROVIDER,
    RESEARCH_ENV_BY_PROVIDER,
    BROWSER_ENV_BY_PROVIDER,
    BROWSE_ENV_BY_PROVIDER,
)
"""Every method's ``*_ENV_BY_PROVIDER`` table: what a file may set, and, in the tools, what would unlock more."""

_OPTIONAL_KEYS = (ENV_JINA_API_KEY,)
"""Keys a keyless backend takes only to raise its rate limit, so no table names them: Jina Reader's."""


def env_names(env: str | tuple[str, ...] | None) -> tuple[str, ...]:
    """The variable names one ``*_ENV_BY_PROVIDER`` entry holds: search's a name or none, every other a tuple."""
    if env is None:
        return ()
    return (env,) if isinstance(env, str) else env


def credential_variables() -> frozenset[str]:
    """Every provider credential variable: what :data:`ALL_ENV_TABLES` names, and the optional keys none does."""
    named = {name for table in ALL_ENV_TABLES for env in table.values() for name in env_names(env)}
    return frozenset(named | set(_OPTIONAL_KEYS))


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

    def __init__(self, message: str, *, status: str, reason: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.reason = reason


@dataclass(frozen=True)
class EnvLookup:
    """One place the startup lookup looked, and what it found there."""

    source: str
    path: Path
    status: str
    reason: str | None = None


@dataclass(frozen=True)
class EnvLoad:
    """One file's credentials applied: the names it set, and the ones it left to a value already set."""

    path: Path
    loaded: tuple[str, ...]
    already_set: tuple[str, ...]
    remembered: Path | None = None


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
            applied = _apply(path, values)
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
        target = _absolute(path)
        values = read_credentials(target)
        pointer = _remember(target) if remember else None
        applied = _apply(target, values, remembered=pointer)
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


def read_credentials(path: Path) -> dict[str, str]:
    """The provider credentials ``path`` sets, and nothing else it holds.

    Refuses anything that is not a small text file before reading it, since a
    model can name any path on the machine.

    Raises:
        EnvFileError: for a path with no file, one that cannot be read as
            text, or one that sets no credential.
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
    wanted = sorted(credential_variables())
    # An empty assignment -- a copied .env.example -- carries no credential.
    found = {name: value for name in wanted if (value := values.get(name))}
    if not found:
        raise EnvFileError(
            MCP_MSG_ENV_NO_CREDENTIALS.format(path=path, names=", ".join(wanted)),
            status=MCP_ENV_STATUS_NO_CREDENTIALS,
        )
    return found


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
    places.append((MCP_ENV_SOURCE_CWD, Path.cwd() / MCP_ENV_FILE))
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


def _apply(path: Path, values: dict[str, str], *, remembered: Path | None = None) -> EnvLoad:
    loaded: list[str] = []
    kept: list[str] = []
    for name, value in values.items():
        # Set-and-empty counts as unset, as the registry reads it.
        if os.environ.get(name):
            kept.append(name)
        else:
            os.environ[name] = value
            loaded.append(name)
    return EnvLoad(path, tuple(loaded), tuple(kept), remembered)


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
    return report
