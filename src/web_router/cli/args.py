"""Turn the command line's strings into the values the router's methods take.

Every failure here is the caller's input, so each raises :class:`CliInputError`,
which exits with the usage status before any backend is called.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from web_router.constants_cli import (
    CLI_FILE_PREFIX,
    MSG_BAD_JSON,
    MSG_BAD_PAIR,
    MSG_NOT_FINITE,
    MSG_NOT_LIST,
    MSG_NOT_OBJECT,
    MSG_UNREADABLE,
)


class CliInputError(ValueError):
    """An argument the command line could not turn into a value."""


class _NotFinite(ValueError):
    """``NaN`` or ``Infinity``, which :func:`json.loads` accepts and no cap, count or timeout should."""


def _refuse_constant(name: str) -> Any:
    raise _NotFinite(name)


def _loads(text: str) -> Any:
    """JSON without ``NaN``/``Infinity``: those would lift a cap or a timeout rather than set one."""
    return json.loads(text, parse_constant=_refuse_constant)


def pairs(values: list[str] | None, *, typed: bool = True) -> dict[str, Any] | None:
    """``KEY=VALUE`` arguments as a dict.

    ``typed`` reads each value as JSON when it parses, so ``--option max_age=3600`` sends a number and
    ``--option mode=fast`` a string, the way a caller writing the dict by hand would have; ``NaN`` and
    ``Infinity`` stay text. Without it every value is text, as a live data source's id must stay
    ``"12345"`` rather than become a number.
    """
    if not values:
        return None
    parsed: dict[str, Any] = {}
    for value in values:
        key, sep, text = value.partition("=")
        if not sep or not key:
            raise CliInputError(MSG_BAD_PAIR.format(value=value))
        if not typed:
            parsed[key] = text
            continue
        try:
            parsed[key] = _loads(text)
        except ValueError:  # not JSON, or not finite: the text as given
            parsed[key] = text
    return parsed


def json_value(text: str | None, what: str) -> Any:
    """A JSON argument, inline or read from the file an ``@path`` names; ``None`` when it was not given."""
    if text is None:
        return None
    if text.startswith(CLI_FILE_PREFIX):
        path = Path(text[len(CLI_FILE_PREFIX) :]).expanduser()
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise CliInputError(MSG_UNREADABLE.format(path=path, error=exc.strerror or exc)) from None
    try:
        return _loads(text)
    except _NotFinite as exc:
        raise CliInputError(MSG_NOT_FINITE.format(what=what, value=exc)) from None
    except json.JSONDecodeError as exc:
        raise CliInputError(MSG_BAD_JSON.format(what=what, error=exc)) from None


def json_object(text: str | None, what: str) -> dict[str, Any] | None:
    """A JSON argument that must be an object, such as a JSON Schema."""
    value = json_value(text, what)
    if value is not None and not isinstance(value, dict):
        raise CliInputError(MSG_NOT_OBJECT.format(what=what))
    return value


def json_records(text: str | None, what: str) -> list[dict[str, Any]] | None:
    """A JSON argument that must be a list of objects, such as the rows research enriches."""
    value = json_value(text, what)
    if value is not None and not (isinstance(value, list) and all(isinstance(row, dict) for row in value)):
        raise CliInputError(MSG_NOT_LIST.format(what=what))
    return value
