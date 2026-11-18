"""Reading one ``.env`` file: its provider credentials, and nothing else it holds.

Split from :mod:`web_router.mcp.env`, which finds the files and keeps the
record of what loaded; the rules for what a file may set are there.
"""

from __future__ import annotations

import io
import os
import stat
from pathlib import Path

from web_router.constants import (
    MCP_ENV_FILE,
    MCP_ENV_FILE_MAX_BYTES,
    MCP_ENV_REASON_NO_DOTENV,
    MCP_ENV_REASON_NOT_A_FILE,
    MCP_ENV_REASON_NOT_TEXT,
    MCP_ENV_REASON_TOO_LARGE,
    MCP_ENV_STATUS_MISSING,
    MCP_ENV_STATUS_NO_CREDENTIALS,
    MCP_ENV_STATUS_UNREADABLE,
    MCP_MSG_ENV_NO_CREDENTIALS,
)
from web_router.constants_mcp import MCP_MSG_ENV_MISSING, MCP_MSG_ENV_UNREADABLE
from web_router.constants_mcp_env import MCP_ENV_STATUS_REFUSED, MCP_MSG_ENV_NOT_A_DOTENV_NAME
from web_router.mcp.env_credentials import credential_variables


class EnvFileError(ValueError):
    """A file the server was pointed at and could not use. Its message names the path and why, never a value."""

    def __init__(self, message: str, *, status: str, reason: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.reason = reason


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


def absolute(raw: str) -> Path:
    return Path(os.path.abspath(os.path.expanduser(raw)))


def _unreadable(path: Path, reason: str) -> EnvFileError:
    return EnvFileError(
        MCP_MSG_ENV_UNREADABLE.format(path=path, reason=reason), status=MCP_ENV_STATUS_UNREADABLE, reason=reason
    )


def is_dotenv_name(name: str) -> bool:
    """Whether a file's name is one a ``.env`` goes by: ``.env``, ``.env.<name>`` or ``<name>.env``."""
    return name == MCP_ENV_FILE or name.startswith(f"{MCP_ENV_FILE}.") or name.endswith(MCP_ENV_FILE)


def require_dotenv_name(given: str, target: Path) -> None:
    """Refuse, before opening anything, a path whose name -- or, through a link, whose target's -- is not a ``.env``'s.

    One refusal for every such path, whether a file is there or not, so a model
    steered to probe the machine learns nothing about what exists.

    Raises:
        EnvFileError: for any such path.
    """
    if not (is_dotenv_name(target.name) and is_dotenv_name(target.resolve().name)):
        raise EnvFileError(MCP_MSG_ENV_NOT_A_DOTENV_NAME.format(path=given), status=MCP_ENV_STATUS_REFUSED)
