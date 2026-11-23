"""The default ``auto`` orders that route-eval ranked, read from ``data/chains.json``.

Search's keyed members and answer's members are ranked from the frozen benchmark
results by ``make chains`` (``benchmarks/providers/chains.py``),
which writes the file; never edit it by hand. A new backend joins by hand at the
end of its method's ``appended`` (search) or ``preferred`` (answer) list, where
the next ``make chains`` keeps it until frozen results rank it. Experimental: the
file's shape may change before 1.0.

Imports nothing from ``web_router``: :mod:`web_router.constants` and
:mod:`web_router.constants_answer` read it while they load.
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from typing import Any, TypeGuard

CHAINS_DATA_DIR = "data"
CHAINS_DATA_FILE = "chains.json"


@lru_cache(maxsize=1)
def chains() -> dict[str, Any]:
    """The whole generated file."""
    text = (resources.files("web_router") / CHAINS_DATA_DIR / CHAINS_DATA_FILE).read_text(encoding="utf-8")
    data: dict[str, Any] = json.loads(text)
    return data


def chain(method: str, part: str) -> tuple[str, ...]:
    """One list of a method's generated order: ``preferred``, or search's ``appended``."""
    return tuple(chains()[method][part])


def measured_cost(method: str, provider: str) -> float | None:
    """The mean cost per call the benchmark behind ``method``'s order measured for ``provider``; ``None`` unmeasured.

    Each is the backend's own stated cost averaged over the scan's questions, at its default mode.
    """
    row = chains().get(method, {}).get("measured", {}).get(provider) or {}
    cost = row.get("cost_usd")
    return float(cost) if _number(cost) else None


def measured_accuracy(method: str) -> dict[str, float]:
    """Each backend's share of correct answers in the benchmark behind ``method``'s order, at its default mode;
    empty for a method the benchmark did not rank. A row with no accuracy from 0 to 1 is left out."""
    rows = chains().get(method, {}).get("measured", {})
    return {p: float(row["accuracy"]) for p, row in rows.items() if is_accuracy_share(row.get("accuracy"))}


def is_accuracy_share(value: object) -> TypeGuard[int | float]:
    """Whether a score is a real number from zero to one, excluding booleans."""
    return _number(value) and 0 <= value <= 1


def _number(value: object) -> TypeGuard[int | float]:
    return isinstance(value, int | float) and not isinstance(value, bool)
