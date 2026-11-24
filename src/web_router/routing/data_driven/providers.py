"""The data-driven router's provider table: the search backends and modes a benchmark measured, read from the release.

Each row is one candidate (``search:parallel:parallel-turbo+fast``) with its measured quality, the half-width of
that quality's 95% confidence interval, its price per thousand requests and its median latency. The rows were
measured together, so the table's own prices and latencies draw its lines (:mod:`.lines`); a budget still filters
the candidates on the library's price list before the router sees them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from web_router.constants_data_driven import DATA_DRIVEN_MANIFEST_FILE, MSG_DATA_DRIVEN_PROVIDERS
from web_router.exceptions import ConfigurationError
from web_router.providers._pinning import split_pin
from web_router.routing.data_driven.table import read_release


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
    return type(value) in (int, float) and value >= 0  # type: ignore[operator]


def _point(row: Mapping[str, Any]) -> Point:
    numbers = [row.get(name) for name in ("quality", "ci95", "usd_per_1k", "p50_s")]
    if not isinstance(row.get("id"), str) or not all(_measure(number) for number in numbers):
        why = f"the row {dict(row)!r} needs an id and four measures"
        raise ConfigurationError(MSG_DATA_DRIVEN_PROVIDERS.format(why=why))
    quality, ci95, usd_per_1k, p50_s = (float(number) for number in numbers if number is not None)
    return Point(row["id"], quality, ci95, usd_per_1k, p50_s)


@lru_cache(maxsize=1)
def shipped_providers() -> ProviderTable:
    """The provider table of the release this package ships, checked once; empty when the release names none."""
    named = read_release(DATA_DRIVEN_MANIFEST_FILE).get("provider_table")
    if named is None:
        return ProviderTable()
    data = read_release(named["file"], sha256=named["sha256"])
    return ProviderTable(str(data["measured"]), tuple(_point(row) for row in data["search"]))
