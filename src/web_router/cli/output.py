"""What reaches stdout and stderr, and the status the process exits with.

A response goes to stdout as one JSON object; an error goes to stderr as
``{"error": {"type", "message"}}`` with stdout left empty, so a caller can
parse whichever stream it reads without guessing which one it got.
"""

from __future__ import annotations

import codecs
import json
import sys
from typing import IO, Any

from pydantic import BaseModel

from web_router.cli.args import CliInputError
from web_router.cli.parser import UsageError
from web_router.constants_cli import (
    CLI_JSON_INDENT,
    CLI_OUTPUT_EXCLUDED,
    CLI_USAGE_ERROR,
    EXIT_CONFIG,
    EXIT_FAILED,
    EXIT_INTERRUPTED,
    EXIT_USAGE,
)
from web_router.exceptions import (
    ConfigurationError,
    MissingParameterError,
    ProviderNotInstalledError,
    UnsupportedFormatError,
    UnsupportedParameterError,
)
from web_router.exceptions_budget import BudgetExceededError
from web_router.exceptions_provider import (
    LiveDataBackendChoiceError,
    NoAnswerProviderError,
    ProviderNotFoundError,
)
from web_router.exceptions_typed import InvalidParameterError

# First match wins, so a subclass must come before its base. A pydantic ValidationError is not here: the
# library turns a caller's bad value into InvalidParameterError, so one reaching the command is a backend
# reply that failed to parse, from a call that ran.
_EXIT_BY_ERROR: tuple[tuple[type[BaseException], int], ...] = (
    (KeyboardInterrupt, EXIT_INTERRUPTED),
    (UsageError, EXIT_USAGE),
    (CliInputError, EXIT_USAGE),
    (UnsupportedParameterError, EXIT_USAGE),
    (UnsupportedFormatError, EXIT_USAGE),
    (MissingParameterError, EXIT_USAGE),
    (InvalidParameterError, EXIT_USAGE),
    (BudgetExceededError, EXIT_USAGE),  # raised before any backend starts, never after one ran
    (ProviderNotFoundError, EXIT_USAGE),
    (LiveDataBackendChoiceError, EXIT_USAGE),  # several backends could serve it; -p picks one
    (ConfigurationError, EXIT_CONFIG),
    (ProviderNotInstalledError, EXIT_CONFIG),
)


def to_json(value: Any, *, raw: bool, pretty: bool, stream: IO[str] | None = None) -> str:
    """A response as JSON: a pydantic model through its own serializer, the backend's payload only when asked.

    Text goes out as written unless ``stream`` (stdout by default) cannot encode it, as an ASCII locale cannot;
    then non-ASCII characters are escaped, which is still the same JSON, rather than failing on the way out.
    """
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", exclude=None if raw else _excluded(value))
    indent = CLI_JSON_INDENT if pretty else None
    return json.dumps(value, indent=indent, ensure_ascii=_ascii_only(stream or sys.stdout), default=str)


def _ascii_only(stream: IO[str]) -> bool:
    """Whether ``stream`` cannot take every character, so JSON must escape what is not ASCII."""
    encoding = getattr(stream, "encoding", None) or "utf-8"
    try:
        return not codecs.lookup(encoding).name.startswith("utf")
    except LookupError:
        return True


def _excluded(value: Any) -> Any:
    """The fields to leave out of ``value``, at every depth: a model's own ``raw``, never a user's key of that name.

    Built from the models themselves, so a ``raw`` inside a record or an input dict stays.
    """
    if isinstance(value, BaseModel):
        fields: dict[Any, Any] = {}
        for name in type(value).model_fields:
            nested = True if name in CLI_OUTPUT_EXCLUDED else _excluded(getattr(value, name))
            if nested:
                fields[name] = nested
        return fields or None
    if isinstance(value, (list, tuple)):
        items = {index: nested for index, item in enumerate(value) if (nested := _excluded(item))}
        return items or None
    if isinstance(value, dict):
        entries = {key: nested for key, item in value.items() if (nested := _excluded(item))}
        return entries or None
    return None


def write_error(error: BaseException, stream: IO[str]) -> int:
    """Print ``error`` as the JSON envelope on ``stream`` and return the status to exit with."""
    kind = CLI_USAGE_ERROR if isinstance(error, UsageError) else type(error).__name__
    envelope = {"error": {"type": kind, "message": str(error) or kind}}
    print(json.dumps(envelope, ensure_ascii=_ascii_only(stream)), file=stream)
    return exit_code(error)


def exit_code(error: BaseException) -> int:
    """The status an error exits with: usage, configuration, or the call itself failing."""
    if isinstance(error, NoAnswerProviderError):
        # No answer key at all is setup; several keys and no default is a choice the caller must make (-p).
        return EXIT_USAGE if error.configured else EXIT_CONFIG
    for kind, code in _EXIT_BY_ERROR:
        if isinstance(error, kind):
            return code
    return EXIT_FAILED
