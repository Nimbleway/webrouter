"""route-eval's ranking: each backend's numbers over a set, the noise floor, and the one order the repo recommends.

``route_eval.py`` prints this order for each set and ``run.py``'s report
prints it for a scan, so the repository never suggests two orders. Imported as
a sibling, by name, as ``_common`` is.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable, Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

from _report_tables import grade_stats
from frozen import FROZEN_DIR, JUDGE_ERROR, LEAKED_ERROR
from route_eval_sets import TWIN_SETS, Question, load_set

from web_router import prices
from web_router.constants import AUTO_KEYLESS

SERVED_ERRORS = frozenset({JUDGE_ERROR, LEAKED_ERROR})
"""Error classes where the backend answered and only the grading did not happen: served, but ungraded."""
KEYLESS = frozenset(name for name, options in AUTO_KEYLESS if not options.get("allow_keyless"))
"""Backends that are only ever keyless (``you-mcp``): a ranking keeps them last, as ``auto`` does."""


def cost_of(row: Mapping[str, Any]) -> float | None:
    """What a served row cost: the cost the backend stated, else its list price; ``None`` when neither is known."""
    if row["cost_usd"] is not None:
        return float(row["cost_usd"])
    return prices.estimate(row["provider"], row["method"])


def _cost_key(cost: float | None) -> tuple[bool, float]:
    # Unknown and $0 sort after every known price: a free tier or a missing price never moves a backend up.
    return (cost is None or cost <= 0, cost or 0.0)


def _latency_key(latency: float | None) -> float:
    return float("inf") if latency is None else latency


def rank_backends(
    stats: Mapping[str, Mapping[str, Any]], noise_floor: float, keyless: Iterable[str] = KEYLESS
) -> list[str]:
    """Backends best first: by accuracy, and within the noise floor by known cost, then latency, then name.

    ``stats`` maps a backend to its ``accuracy`` (correct out of every
    question, a failed row counting as a miss), ``cost_usd`` (``None`` when
    unknown) and ``latency_p50``. Backends whose accuracy is within
    ``noise_floor`` of the most accurate one not yet placed form one tier,
    ordered cheapest first: an accuracy gap smaller than a rerun's spread does
    not tell two backends apart, so cost decides. An unknown or $0 cost sorts
    after every known one. The keyless backends go last, whatever they score.
    """
    keyless = set(keyless)
    remaining = sorted(stats, key=lambda p: (-stats[p]["accuracy"], p))
    order: list[str] = []
    while remaining:
        top = stats[remaining[0]]["accuracy"]
        tier = [p for p in remaining if top - stats[p]["accuracy"] <= noise_floor + 1e-12]
        tier.sort(key=lambda p: (_cost_key(stats[p]["cost_usd"]), _latency_key(stats[p]["latency_p50"]), p))
        order += tier
        remaining = [p for p in remaining if p not in tier]
    return [p for p in order if p not in keyless] + [p for p in order if p in keyless]


def backend_stats(questions: Sequence[Question]) -> dict[str, dict[str, Any]]:
    """Each backend's numbers over ``questions``, failed rows skipped for grades and latency.

    ``accuracy`` is correct out of every question, a failed row counting as a
    miss, as the recommended order has always ranked; ``cost_usd`` is the mean
    cost of the rows it served, ``None`` if any is unknown.
    """
    rows_by: dict[str, list[Mapping[str, Any]]] = {}
    for q in questions:
        for provider, row in q.rows.items():
            rows_by.setdefault(provider, []).append(row)
    out = {}
    for provider, rows in sorted(rows_by.items()):
        graded = [{"grade": r["grade"] if r["error"] is None else None, "latency": r["latency_s"]} for r in rows]
        known = [g["latency"] for g in graded if g["grade"] is not None and g["latency"] is not None]
        stats = grade_stats(graded, known)
        costs = [cost_of(r) for r in rows if r["error"] is None]
        out[provider] = {
            **stats,
            "failed": stats["questions"] - stats["served"],
            "accuracy_all": stats["correct"] / stats["questions"] if stats["questions"] else 0.0,
            "cost_usd": None if not costs or None in costs else statistics.fmean(c for c in costs if c is not None),
        }
    return out


def recommended(stats: Mapping[str, Mapping[str, Any]], noise_floor: float) -> list[str]:
    """The one recommended order: :func:`rank_backends` over the backends that served anything."""
    return rank_backends(
        {
            p: {"accuracy": s["accuracy_all"], "cost_usd": s["cost_usd"], "latency_p50": s["latency_p50"]}
            for p, s in stats.items()
            if s["served"]
        },
        noise_floor,
    )


@lru_cache(maxsize=4)
def noise_floor(folder: Path = FROZEN_DIR) -> tuple[float, dict[str, tuple[float, float]]]:
    """The rerun spread: the largest accuracy gap one backend shows between the twin runs, and each backend's pair."""
    accuracies = [
        {p: s["accuracy_all"] for p, s in backend_stats(load_set(name, folder)[1]).items()} for name in TWIN_SETS
    ]
    pairs = {p: (accuracies[0][p], accuracies[1][p]) for p in sorted(set(accuracies[0]) & set(accuracies[1]))}
    return max((abs(a - b) for a, b in pairs.values()), default=0.0), pairs
