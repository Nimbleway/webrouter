"""What reaches stdout and stderr, and the status the process exits with.

A response goes to stdout as one JSON object; an error goes to stderr as
``{"error": {"type", "message"}}`` with stdout left empty, so a caller can
parse whichever stream it reads without guessing which one it got.
"""

from __future__ import annotations

import json
from typing import IO, Any

from pydantic import BaseModel, ValidationError

from web_router.cli.args import CliInputError
from web_router.cli.parser import UsageError
from web_router.constants_cli import (
    CLI_JSON_INDENT,
    CLI_OUTPUT_EXCLUDED,
    CLI_USAGE_ERROR,
    EXIT_CONFIG,
    EXIT_FAILED,
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
from web_router.exceptions_provider import NoAnswerProviderError, ProviderNotFoundError
from web_router.exceptions_typed import InvalidParameterError

# First match wins, so a subclass must come before its base.
_EXIT_BY_ERROR: tuple[tuple[type[BaseException], int], ...] = (
    (UsageError, EXIT_USAGE),
    (CliInputError, EXIT_USAGE),
    (ValidationError, EXIT_USAGE),
    (UnsupportedParameterError, EXIT_USAGE),
    (UnsupportedFormatError, EXIT_USAGE),
    (MissingParameterError, EXIT_USAGE),
    (InvalidParameterError, EXIT_USAGE),
    (BudgetExceededError, EXIT_USAGE),  # raised before any backend starts, never after one ran
    (ProviderNotFoundError, EXIT_USAGE),
    (ConfigurationError, EXIT_CONFIG),
    (ProviderNotInstalledError, EXIT_CONFIG),
)


def to_json(value: Any, *, raw: bool, pretty: bool) -> str:
    """A response as JSON: a pydantic model through its own serializer, the backend's payload only when asked."""
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", exclude=None if raw else set(CLI_OUTPUT_EXCLUDED))
    return json.dumps(value, indent=CLI_JSON_INDENT if pretty else None, ensure_ascii=False, default=str)


def write_error(error: BaseException, stream: IO[str]) -> int:
    """Print ``error`` as the JSON envelope on ``stream`` and return the status to exit with."""
    kind = CLI_USAGE_ERROR if isinstance(error, UsageError) else type(error).__name__
    envelope = {"error": {"type": kind, "message": str(error)}}
    print(json.dumps(envelope, ensure_ascii=False), file=stream)
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
