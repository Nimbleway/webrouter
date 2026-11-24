"""The data-driven router's tool table: counts of labelled requests by feature values, read from the shipped release.

``tables[n]`` maps the first ``n`` feature values, joined with ``|``, to the count of training requests per
method and their total. A request is looked up by all its feature values; a row seen fewer than ``min_count``
times is passed over for the row one feature shorter, down to the empty key, which counts every request.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Any, get_args

from web_router.constants_data_driven import (
    DATA_DRIVEN_DATA_DIR,
    DATA_DRIVEN_MANIFEST_FILE,
    DATA_DRIVEN_SCHEMA,
    MSG_DATA_DRIVEN_CHECKSUM,
    MSG_DATA_DRIVEN_FEATURES,
    MSG_DATA_DRIVEN_METHODS,
    MSG_DATA_DRIVEN_MIN_COUNT,
    MSG_DATA_DRIVEN_ROOT,
    MSG_DATA_DRIVEN_ROW_TOTAL,
    MSG_DATA_DRIVEN_ROW_WIDTH,
    MSG_DATA_DRIVEN_SCHEMA,
    MSG_DATA_DRIVEN_TABLE,
    MSG_DATA_DRIVEN_TABLE_DEPTH,
    MSG_DATA_DRIVEN_UNREADABLE,
)
from web_router.exceptions import ConfigurationError
from web_router.routing.contract import ToolName
from web_router.routing.data_driven.features import FEATURES, featurize


@dataclass(frozen=True)
class Row:
    """What the table holds for one request."""

    values: tuple[str, ...]
    """The feature values the row is keyed on: all of the request's, or fewer when the table backed off."""

    counts: Mapping[ToolName, int]
    """Training requests in this row, per method."""


@dataclass(frozen=True)
class ToolTable:
    """One release's table."""

    version: str
    features: tuple[str, ...]
    labels: tuple[ToolName, ...]
    min_count: int
    tables: tuple[Mapping[str, Sequence[int]], ...]

    def row(self, text: str) -> Row:
        """The longest-keyed row for ``text`` that training saw at least ``min_count`` times."""
        values = featurize(text, self.features)
        for depth in range(len(values), 0, -1):
            hit = self.tables[depth].get("|".join(values[:depth]))
            if hit and hit[-1] >= self.min_count:
                return Row(tuple(values[:depth]), dict(zip(self.labels, hit, strict=False)))
        return Row((), dict(zip(self.labels, self.tables[0][""], strict=False)))


def _read(name: str, *, sha256: str | None = None) -> Any:
    folder = resources.files("web_router").joinpath(*DATA_DRIVEN_DATA_DIR)
    content = (folder / name).read_bytes()
    if sha256 is not None and hashlib.sha256(content).hexdigest() != sha256:
        raise ConfigurationError(MSG_DATA_DRIVEN_TABLE.format(why=MSG_DATA_DRIVEN_CHECKSUM))
    return json.loads(content)


def checked_table(version: str, data: Mapping[str, Any]) -> ToolTable:
    """Validate a table before using it, including an importer checking a staged release."""
    features, labels, tables = data["features"], data["labels"], data["tables"]
    min_count = data["min_count"]
    rows = [row for table in tables for row in table.values()]
    checks = (
        (type(min_count) is not int or min_count <= 0, MSG_DATA_DRIVEN_MIN_COUNT),
        (set(features) - set(FEATURES), MSG_DATA_DRIVEN_FEATURES),
        (set(labels) - set(get_args(ToolName)), MSG_DATA_DRIVEN_METHODS),
        (len(tables) != len(features) + 1, MSG_DATA_DRIVEN_TABLE_DEPTH),
        (any(len(row) != len(labels) + 1 for row in rows), MSG_DATA_DRIVEN_ROW_WIDTH),
        (not tables or not sum(tables[0].get("", [0])[:-1]), MSG_DATA_DRIVEN_ROOT),
    )
    for problem, why in checks:
        if problem:
            raise ConfigurationError(MSG_DATA_DRIVEN_TABLE.format(why=why))
    if any(row[-1] != sum(row[:-1]) for row in rows):
        raise ConfigurationError(MSG_DATA_DRIVEN_TABLE.format(why=MSG_DATA_DRIVEN_ROW_TOTAL))
    return ToolTable(version, tuple(features), tuple(labels), min_count, tuple(tables))


@lru_cache(maxsize=1)
def shipped_table() -> ToolTable:
    """The tool table of the release this package ships, checked once."""
    try:
        manifest = _read(DATA_DRIVEN_MANIFEST_FILE)
        if manifest.get("schema") != DATA_DRIVEN_SCHEMA:
            raise ConfigurationError(MSG_DATA_DRIVEN_SCHEMA.format(got=manifest.get("schema"), want=DATA_DRIVEN_SCHEMA))
        table = manifest["tool_table"]
        return checked_table(manifest["version"], _read(table["file"], sha256=table["sha256"]))
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        # The default router reads these on every construction: a stripped or damaged install says so plainly.
        why = MSG_DATA_DRIVEN_UNREADABLE.format(error=type(exc).__name__)
        raise ConfigurationError(MSG_DATA_DRIVEN_TABLE.format(why=why)) from exc
