"""How a browser provider's field table says where each option goes in its API's body.

Apart from :mod:`web_router.types` so that file stops growing; it re-exports
these, and imports nothing of ours.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal

FieldPath = tuple[str, ...]
"""Where a field sits in a JSON body: one key for a top-level field, one per level for a nested one."""


class _RequestValue(Enum):
    REQUEST_VALUE = "request-value"


REQUEST_VALUE = _RequestValue.REQUEST_VALUE
"""Stands for the request's own value inside a :class:`BrowserField`, wherever in the field it goes."""


@dataclass(frozen=True)
class BrowserField:
    """Every field one option sets in a browser API's body, for an option a single path cannot carry.

    A browser provider's field table maps each option it sends to one of three
    rows, and ``build_browser_payload`` places what each produces:

    - a path, ``("session", "timeout")``: the request's value goes there;
    - a lookup, ``{"low": {("useStealth",): True}, ...}``: the value picks the
      fields to set, and a value the lookup does not list is never sent;
    - a :class:`BrowserField`, which sets every field in :attr:`sets` --
      ``{("useProxy",): True, ("proxyCountry",): REQUEST_VALUE}`` turns a proxy
      on and names its country in one row.
    """

    sets: Mapping[FieldPath, Any]
    """Path -> what goes there: a literal, or :data:`REQUEST_VALUE`, alone or at any depth in a dict, list or tuple.

    Each value is placed whole, so no two paths may meet: one path inside
    another, or the same path twice across rows, is a table bug and raises.
    """

    case: Literal["upper", "lower"] | None = None
    """The case a text value is sent in -- a country code, say, for an API that takes one case only.

    Only for an option whose value is text; set on any other, it raises rather than being skipped.
    """


BrowserFieldRow = FieldPath | Mapping[Any, Mapping[FieldPath, Any]] | BrowserField
"""One option's row in a browser provider's field table: a path, a lookup, or a :class:`BrowserField`."""
