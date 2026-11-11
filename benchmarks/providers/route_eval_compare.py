"""route-eval's router comparison: where two routers part ways on the same questions, and who was right.

Each router's choices are :mod:`route_eval_matrix`'s, one per question. Two
routers disagree on a question when their served paths differ, the same tool on
another backend included. The discordant counts -- only the first right, only
the second -- give an exact two-sided McNemar p-value, and each side's accuracy
carries its Wilson interval. Imported as a sibling, by name, as ``_common`` is.
Experimental until 1.0, like the rest of route-eval.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from typing import Any

from _common import _wilson
from route_eval_matrix import HELD_OUT_CHOICES, _extra, ordered


def _mcnemar(only_first: int, only_second: int) -> float:
    """The exact two-sided McNemar p-value of two paired scores, from their discordant counts alone."""
    n = only_first + only_second
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, k) for k in range(min(only_first, only_second) + 1))
    return min(1.0, 2 * tail / 2**n)


def _side(picked: Sequence[Mapping[str, Any]], known: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """One router's accuracy over the questions both were scored on, and its mean cost where both costs are known."""
    correct = sum(bool(c["correct"]) for c in picked)
    return {
        "correct": correct,
        "accuracy": correct / len(picked) if picked else None,
        "accuracy_ci95": _wilson(correct, len(picked)),
        "mean_cost_usd": statistics.fmean(c["cost_usd"] for c in known) if known else None,
    }


def _pattern(pairs: Sequence[tuple[Mapping[str, Any], Mapping[str, Any]]]) -> list[dict[str, Any]]:
    """The disagreements by (first's tool, second's tool): how many, who was right, the first's extra cost."""
    groups: dict[tuple[str, str], list[tuple[Mapping[str, Any], Mapping[str, Any]]]] = {}
    for a, b in pairs:
        groups.setdefault((a["tool"], b["tool"]), []).append((a, b))
    names = ordered(tool for key in groups for tool in key)
    rows = []
    for first, second in sorted(groups, key=lambda k: (names.index(k[0]), names.index(k[1]))):
        group = groups[(first, second)]
        extras = [_extra(a["cost_usd"], b["cost_usd"]) for a, b in group]
        rows.append(
            {
                "first_tool": first,
                "second_tool": second,
                "questions": len(group),
                "only_first_right": sum(a["correct"] and not b["correct"] for a, b in group),
                "only_second_right": sum(b["correct"] and not a["correct"] for a, b in group),
                "extra_cost_usd": sum(e for e in extras if e is not None),
                "cost_unknown": sum(e is None for e in extras),
            }
        )
    return rows


def compare(first: Sequence[Mapping[str, Any]], second: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Two routers on the questions both were scored on: where their served paths differ, and who was right.

    A disagreement is a different path served, the same tool on another backend included; ``tool_disagree``
    counts those on another tool. ``only_first_right`` and ``only_second_right`` are the discordant counts,
    and ``mcnemar_p`` their exact two-sided McNemar p-value. Each side's mean cost is over the questions where
    both costs are known, so the two means cover the same questions; ``cost_unknown`` counts the rest, once.
    ``extra_cost_usd`` is the first's cost minus the second's.
    """
    other = {c["question_id"]: c for c in second}
    pairs = [(a, other[a["question_id"]]) for a in first if a["question_id"] in other]
    apart = [(a, b) for a, b in pairs if a["path"] != b["path"]]
    known = [(a, b) for a, b in pairs if a["cost_usd"] is not None and b["cost_usd"] is not None]
    only_first = sum(a["correct"] and not b["correct"] for a, b in apart)
    only_second = sum(b["correct"] and not a["correct"] for a, b in apart)
    return {
        "questions": len(pairs),
        "disagree": len(apart),
        "tool_disagree": sum(a["tool"] != b["tool"] for a, b in apart),
        "only_first_right": only_first,
        "only_second_right": only_second,
        "mcnemar_p": _mcnemar(only_first, only_second),
        "cost_unknown": len(pairs) - len(known),
        "first": _side([a for a, _ in pairs], [a for a, _ in known]),
        "second": _side([b for _, b in pairs], [b for _, b in known]),
        "patterns": _pattern(apart),
        "disagreements": [
            {
                "question_id": a["question_id"],
                "first": a["path"],
                "second": b["path"],
                "first_correct": a["correct"],
                "second_correct": b["correct"],
            }
            for a, b in apart
        ],
    }


def comparisons(
    routers: Mapping[str, Mapping[str, Any]], against: Sequence[str], oracle: str, key: str = HELD_OUT_CHOICES
) -> list[dict[str, Any]]:
    """Every router but the oracle compared with each router in ``against``, each pair once, on ``key``'s choices.

    A router without ``key`` -- on :data:`EVERY_CHOICES`, one fit on the training split -- is left out.
    """
    done: set[frozenset[str]] = set()
    out = []
    for base in against:
        for spec, result in routers.items():
            pair = frozenset((spec, base))
            if spec in (oracle, base) or base not in routers or pair in done:
                continue
            if key not in result or key not in routers[base]:
                continue
            done.add(pair)
            out.append({"router": spec, "against": base, **compare(result[key], routers[base][key])})
    return out
