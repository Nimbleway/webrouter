"""The research effort table: each backend's measured efforts, the noise floor, and an effort's p90.

Reads ``data/efforts.json``, written by ``make efforts`` from the
frozen ``tools-effort-60`` results. A p90 comes from the table's measured
figure, and from the price list's latency prior
(:func:`web_router.prices.estimate_latency`) only for an effort the table has no
p90 for. :mod:`web_router.routing.params` maps a budget to an effort with these.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from functools import lru_cache
from importlib import resources
from typing import Any

from web_router import prices
from web_router.constants_params import EFFORTS_DATA_DIR, EFFORTS_DATA_FILE
from web_router.routing.contract import ToolName
from web_router.types import Provider, provider_name

RESEARCH: ToolName = "research"


@lru_cache(maxsize=1)
def effort_table() -> dict[str, Any]:
    """The whole generated research effort table."""
    text = (resources.files("web_router") / EFFORTS_DATA_DIR / EFFORTS_DATA_FILE).read_text(encoding="utf-8")
    data: dict[str, Any] = json.loads(text)
    return data


def noise_floor() -> float:
    """The accuracy gap, as a fraction, two efforts must exceed to be told apart: route-eval's floor for the set."""
    return float(effort_table()["noise_floor"]["value"])


def table_set() -> str:
    """The name of the route-eval set the table was measured on."""
    return str(effort_table()["set"])


def measured_efforts(provider: str | Provider) -> Mapping[str, Mapping[str, Any]]:
    """``provider``'s measured research efforts, by name, each with its ``accuracy`` and latency; empty if none."""
    entry = effort_table()["research"].get(provider_name(provider), {})
    measured: Mapping[str, Mapping[str, Any]] = entry.get("measured", {})
    return measured


def effort_of(provider: str, effort: str | None) -> str | None:
    """``effort``, or for ``None`` the effort the price list marks as the backend's default, when it marks one."""
    if effort is not None:
        return effort
    row = prices.price(provider, RESEARCH, None)
    return None if row is None else row.mode


def effort_p90(provider: str, effort: str | None) -> float | None:
    """The p90 seconds: the effort table's measured figure, else the price list's latency prior, else ``None``."""
    p90 = measured_efforts(provider).get(effort_of(provider, effort) or "", {}).get("latency_p90_s")
    if p90 is not None:
        return float(p90)
    prior = prices.estimate_latency(provider, RESEARCH, effort)
    return None if prior is None else prior[1]
