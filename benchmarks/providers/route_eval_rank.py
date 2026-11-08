"""route-eval's ranking: each backend's numbers over a set, the noise floor, and the one order the repo recommends.

``route_eval.py`` prints this order for each set and ``run.py``'s report
prints it for a scan, so the repository never suggests two orders. Imported as
a sibling, by name, as ``_common`` is.
"""

from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

from _frozen_checks import JUDGE_ERROR, LEAKED_ERROR, RATE_LIMIT_OR_QUOTA_ERRORS
from _report_tables import correct_of_all, grade_stats
from frozen import FROZEN_DIR, price_mode
from route_eval_sets import TWIN_SETS, Question, load_set

from web_router import prices
from web_router.constants import AUTO_KEYLESS

SERVED_ERRORS = frozenset({JUDGE_ERROR, LEAKED_ERROR})
"""Error classes where the backend answered and only the grading did not happen: served, but ungraded."""
UNGRADED = "ungraded"
"""The grade :func:`backend_stats` gives a served row the judge did not grade."""
KEYLESS = frozenset(name for name, options in AUTO_KEYLESS if not options.get("allow_keyless"))
"""Backends that are only ever keyless (``you-mcp``): a ranking keeps them last, as ``auto`` does."""
RANKING_RULE = (
    "Backends best first by accuracy, correct out of every question, a failed row counting as a miss. Backends "
    "whose accuracy is within the noise floor of the most accurate one not yet placed form one tier, ordered "
    "cheapest first, then by latency, then by name: an accuracy gap smaller than a rerun's spread does not tell "
    "two backends apart, so cost decides. An unknown or $0 cost sorts after every known one. The keyless backends "
    "go last, whatever they score. A backend that served nothing is left out, and so is a row that failed during "
    "the run with a rate-limit or quota error: not counted a miss, nor in that backend's total."
)
"""The rule :func:`rank_backends` applies, in words, for a report or a reader that prints it."""


def cost_of(row: Mapping[str, Any]) -> float | None:
    """What a served row cost: the cost the backend stated, else its list price at the row's mode (a research
    row's effort); ``None`` when neither is known."""
    if row["cost_usd"] is not None:
        return float(row["cost_usd"])
    return prices.estimate(row["provider"], row["method"], price_mode(row["method"], row["mode"]))


def cost_key(cost: float | None) -> tuple[bool, float]:
    """A cost as a tie-break sorts it: unknown and $0 after every known price, so neither moves a backend up."""
    return (cost is None or cost <= 0, cost or 0.0)


def latency_key(latency: float | None) -> float:
    """A latency as a tie-break sorts it: unknown after every known one."""
    return float("inf") if latency is None else latency


def _within_floor(gap: float, noise_floor: float) -> bool:
    # The tolerance keeps a gap equal to the floor, computed from different counts, from falling outside it.
    return gap <= noise_floor + 1e-12


def rank_backends(stats: Mapping[str, Mapping[str, Any]], noise_floor: float) -> list[str]:
    """Backends best first, by :data:`RANKING_RULE`.

    ``stats`` maps a backend to its ``accuracy`` (correct out of every
    question, a failed row a miss, a rate-limit or quota failure left out),
    ``cost_usd`` (``None`` when unknown) and ``latency_p50``.
    """
    remaining = sorted(stats, key=lambda p: (-stats[p]["accuracy"], p))
    order: list[str] = []
    while remaining:
        top = stats[remaining[0]]["accuracy"]
        tier = [p for p in remaining if _within_floor(top - stats[p]["accuracy"], noise_floor)]
        tier.sort(key=lambda p: (cost_key(stats[p]["cost_usd"]), latency_key(stats[p]["latency_p50"]), p))
        order += tier
        remaining = [p for p in remaining if p not in tier]
    return [p for p in order if p not in KEYLESS] + [p for p in order if p in KEYLESS]


def ranked_with_best(stats: Mapping[str, Mapping[str, Any]], noise_floor: float) -> tuple[list[str], frozenset[str]]:
    """:func:`rank_backends`, and the backends within ``noise_floor`` of the most accurate one.

    Those are the best, as far as the floor tells, wherever the cost tie-break
    placed them: a report labels them by accuracy, not by position.
    """
    if not stats:
        return [], frozenset()
    top = max(s["accuracy"] for s in stats.values())
    best = frozenset(p for p, s in stats.items() if _within_floor(top - s["accuracy"], noise_floor))
    return rank_backends(stats, noise_floor), best


def is_served(row: Mapping[str, Any]) -> bool:
    return row["error"] is None or row["error"] in SERVED_ERRORS


def _grade(row: Mapping[str, Any]) -> str:
    # A served row the judge did not grade is neither correct nor incorrect, but it is not a failure either.
    return UNGRADED if row["error"] is not None else row["grade"]


def backend_stats(questions: Sequence[Question]) -> dict[str, dict[str, Any]]:
    """Each backend's numbers over ``questions``, failed rows skipped for grades and latency.

    ``accuracy`` is correct out of every question, a failed row a miss, but one
    failed on a rate-limit or quota error (:data:`RATE_LIMIT_OR_QUOTA_ERRORS`) left out and counted
    in ``excluded``; ``cost_usd`` is the mean
    cost of the rows it served, ``None`` if any is unknown. A row the judge did
    not grade, or that leaked the set's own pages, was served, as the replay
    counts it: not failed, never correct, its cost and latency counted.
    """
    rows_by: dict[str, list[Mapping[str, Any]]] = {}
    for q in questions:
        for provider, row in q.rows.items():
            rows_by.setdefault(provider, []).append(row)
    out = {}
    for provider, all_rows in sorted(rows_by.items()):
        rows = [r for r in all_rows if r["error"] not in RATE_LIMIT_OR_QUOTA_ERRORS]
        served = [r for r in rows if is_served(r)]
        graded = [{"grade": _grade(r) if is_served(r) else None, "latency": r["latency_s"]} for r in rows]
        known = [r["latency_s"] for r in served if r["latency_s"] is not None]
        stats = grade_stats(graded, known)
        costs = [cost_of(r) for r in served]
        out[provider] = {
            **stats,
            "failed": stats["questions"] - stats["served"],
            "excluded": len(all_rows) - len(rows),
            "accuracy_all": correct_of_all(stats),
            "cost_usd": None if not costs or None in costs else statistics.fmean(c for c in costs if c is not None),
        }
    return out


def recommended(stats: Mapping[str, Mapping[str, Any]], noise_floor: float) -> list[str]:
    """The one recommended order: :func:`rank_backends` over the backends that served anything."""
    return ranked_with_best(
        {
            p: {"accuracy": s["accuracy_all"], "cost_usd": s["cost_usd"], "latency_p50": s["latency_p50"]}
            for p, s in stats.items()
            if s["served"]
        },
        noise_floor,
    )[0]


@lru_cache(maxsize=4)
def noise_floor(folder: Path = FROZEN_DIR) -> tuple[float, dict[str, tuple[float, float]]]:
    """The rerun spread: the largest accuracy gap one backend shows between the twin runs, and each backend's pair."""
    accuracies = [
        {p: s["accuracy_all"] for p, s in backend_stats(load_set(name, folder)[1]).items()} for name in TWIN_SETS
    ]
    pairs = {p: (accuracies[0][p], accuracies[1][p]) for p in sorted(set(accuracies[0]) & set(accuracies[1]))}
    return max((abs(a - b) for a, b in pairs.values()), default=0.0), pairs
