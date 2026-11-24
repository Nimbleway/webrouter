"""The trained router's tool table: counts of labelled requests by feature values, read from the shipped release.

``tables[n]`` maps the first ``n`` feature values, joined with ``|``, to the count of training requests per
method and their total. A request is looked up by all its feature values; a row seen fewer than ``min_count``
times is passed over for the row one feature shorter, down to the empty key, which counts every request.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Any, get_args

from web_router.constants_trained import (
    MSG_TRAINED_SCHEMA,
    MSG_TRAINED_TABLE,
    TRAINED_DATA_DIR,
    TRAINED_MANIFEST_FILE,
    TRAINED_SCHEMA,
)
from web_router.exceptions import ConfigurationError
from web_router.routing.contract import ToolName
from web_router.routing.trained.features import FEATURES, featurize


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


def _read(name: str) -> Any:
    folder = resources.files("web_router").joinpath(*TRAINED_DATA_DIR)
    return json.loads((folder / name).read_text(encoding="utf-8"))


def _checked(version: str, data: Mapping[str, Any]) -> ToolTable:
    features, labels, tables = data["features"], data["labels"], data["tables"]
    rows = [row for table in tables for row in table.values()]
    checks = (
        (set(features) - set(FEATURES), "it names features this version cannot compute"),
        (set(labels) - set(get_args(ToolName)), "it names methods web-router does not route"),
        (len(tables) != len(features) + 1, "it needs one table per key length"),
        (any(len(row) != len(labels) + 1 for row in rows), "a row needs one count per method and their total"),
        (not tables or not sum(tables[0].get("", [0])[:-1]), "it has no row for every request"),
    )
    for problem, why in checks:
        if problem:
            raise ConfigurationError(MSG_TRAINED_TABLE.format(why=why))
    return ToolTable(version, tuple(features), tuple(labels), int(data["min_count"]), tuple(tables))


@lru_cache(maxsize=1)
def shipped_table() -> ToolTable:
    """The tool table of the release this package ships, checked once."""
    manifest = _read(TRAINED_MANIFEST_FILE)
    if manifest.get("schema") != TRAINED_SCHEMA:
        raise ConfigurationError(MSG_TRAINED_SCHEMA.format(got=manifest.get("schema"), want=TRAINED_SCHEMA))
    return _checked(manifest["version"], _read(manifest["tool_table"]["file"]))
