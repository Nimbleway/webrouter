"""The data-driven router's provider table: the search backends and modes a benchmark measured, read from the release.

Each row is one candidate (``search:parallel:parallel-turbo+fast``) with its measured quality, the half-width of
that quality's 95% confidence interval, its price per thousand requests and its median latency. The rows were
measured together, so the table's own prices and latencies draw its lines (:mod:`.lines`); a budget still filters
the candidates on the library's price list before the router sees them.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from web_router.constants_data_driven import (
    DATA_DRIVEN_MANIFEST_FILE,
    DATA_DRIVEN_SEARCH_ID,
    MSG_DATA_DRIVEN_POINT,
    MSG_DATA_DRIVEN_POINT_ID,
    MSG_DATA_DRIVEN_PROVIDERS,
    MSG_DATA_DRIVEN_PROVIDERS_READ,
)
from web_router.exceptions import ConfigurationError
from web_router.providers._pinning import split_pin
from web_router.routing.data_driven.table import read_release

MEASURES = ("quality", "ci95", "usd_per_1k", "p50_s")
"""A row's four measures, in a :class:`Point`'s order."""


@dataclass(frozen=True)
class Point:
    """One measured candidate."""

    id: str
    quality: float
    ci95: float
    usd_per_1k: float
    p50_s: float


@dataclass(frozen=True)
class ProviderTable:
    """One release's measured search candidates; empty for a release that ships none."""

    measured: str = ""
    search: tuple[Point, ...] = ()

    @property
    def extra_modes(self) -> dict[str, list[str]]:
        """The vendor modes the rows name, per backend: what the router asks to be shown."""
        asked: dict[str, list[str]] = {}
        for point in self.search:
            backend, mode = split_pin(point.id)
            if mode:
                asked.setdefault(backend, []).append(mode)
        return asked


def _measure(value: object) -> bool:
    """Whether ``value`` is a measure: a finite number of 0 or more, never a bool."""
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value) and value >= 0  # type: ignore[arg-type, operator]
    except OverflowError:  # an integer too large for a float: json reads 10**400 as one
        return False


def checked_point(row: Mapping[str, Any]) -> Point:
    """One row as a :class:`Point`: a search candidate's id with its four measures, each a finite number."""
    cid: object = row.get("id")
    numbers: list[Any] = [row.get(name) for name in MEASURES]
    if not isinstance(cid, str) or not cid.startswith(DATA_DRIVEN_SEARCH_ID) or not all(map(_measure, numbers)):
        raise ConfigurationError(MSG_DATA_DRIVEN_PROVIDERS.format(why=MSG_DATA_DRIVEN_POINT.format(row=dict(row))))
    return Point(cid, *(float(number) for number in numbers))


def checked_points(rows: Iterable[Mapping[str, Any]]) -> tuple[Point, ...]:
    """Every row as a :class:`Point`, an id named twice refused: an importer checks a staged table with this too."""
    points = tuple(checked_point(row) for row in rows)
    ids = [point.id for point in points]
    if repeated := sorted({cid for cid in ids if ids.count(cid) > 1}):
        raise ConfigurationError(MSG_DATA_DRIVEN_PROVIDERS.format(why=MSG_DATA_DRIVEN_POINT_ID.format(ids=repeated)))
    return points


@lru_cache(maxsize=1)
def shipped_providers() -> ProviderTable:
    """The provider table of the release this package ships, checked once; empty when the release names none."""
    try:
        named = read_release(DATA_DRIVEN_MANIFEST_FILE).get("provider_table")
        if named is None:
            return ProviderTable()
        data = read_release(named["file"], sha256=named["sha256"], message=MSG_DATA_DRIVEN_PROVIDERS)
        return ProviderTable(str(data["measured"]), checked_points(data["search"]))
    except (KeyError, TypeError, AttributeError, ValueError, OSError) as exc:
        why = MSG_DATA_DRIVEN_PROVIDERS_READ.format(error=type(exc).__name__)
        raise ConfigurationError(MSG_DATA_DRIVEN_PROVIDERS.format(why=why)) from exc
