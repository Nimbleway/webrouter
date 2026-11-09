"""route-eval's cost-weighted tool confusion matrix, and where two routers part ways.

Each question is one choice: the path that served it, that path's tool,
whether it was right, and what it cost in dollars and seconds as the replay
charged it (:class:`~route_eval_replay.Outcome`). The **right tools** of a
question are every tool with a path that got it right; the **best tool** is
the tool of the oracle's choice -- the right path, cheapest then fastest -- or
:data:`NONE_RIGHT` when no path did. A router's matrix counts its choices by
(tool it ran, best tool) and weighs each cell by what its choices cost beyond
the oracle's on the same questions, so a dear ``research`` shows in dollars
where a dear ``search`` shows in cents.

Off the oracle, a choice is one of two things. **Wrong tool, wrong answer**:
some tool was right and the router's was not one of them, the accuracy its
tool choice lost. **Right, not the cheapest**: right on another tool than the
best, pure cost regret, charged against the cheapest right path. A wrong
answer from a right tool on another backend is neither: it is outside the
tool matrix. The oracle is one frozen sample per path, so it is an upper bound.

On a one-method set every path is the same tool, so the matrix is one row; it
is still the router's overspend over the oracle. Imported as a sibling, by
name, as ``_common`` is. Experimental until 1.0, like the rest of route-eval.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from _common import _mcnemar, _wilson
from route_eval_merge import TOOL_OF_PATH
from route_eval_paths import MIXED, SEP
from route_eval_replay import Outcome
from route_eval_sets import Question

UNSERVED = "unserved"
"""The tool of a question no path served: a row of the matrix, never a best tool."""
NONE_RIGHT = "none right"
"""The best tool of a question no path got right: a column, and never off the oracle."""
TOOL_ORDER = (*dict.fromkeys(TOOL_OF_PATH.values()), "browse")
"""The order the matrix lists tools in; any other tool follows, by name, then the two above."""
HELD_OUT_CHOICES = "choices"
EVERY_CHOICES = "choices_every_question"
"""A router not fit on the training split also keeps its choices on every question, under this key."""


def tool_of(path: str | None, method: str) -> str:
    """The tool a served path ran: its method on a mixed set (search then read is ``search``), else the set's."""
    if path is None:
        return UNSERVED
    if method != MIXED:
        return method
    kind = path.split(SEP)[0]
    return TOOL_OF_PATH.get(kind, kind)


def right_tools(q: Question, method: str) -> list[str]:
    """Every tool with a path that got ``q`` right, as the replay grades a served row."""
    return ordered(tool_of(p, method) for p, r in q.rows.items() if r["error"] is None and r["grade"] == "correct")


def choices(questions: Sequence[Question], outcomes: Sequence[Outcome], method: str) -> list[dict[str, Any]]:
    """Each question's choice as the matrix and the comparison read it, in the questions' order."""
    return [
        {
            "question_id": q.question_id,
            "path": o.served_by,
            "tool": tool_of(o.served_by, method),
            "right_tools": right_tools(q, method),
            "correct": o.correct,
            "cost_usd": o.cost_usd,
            "latency_s": o.latency_s,
        }
        for q, o in zip(questions, outcomes, strict=True)
    ]


def _cell() -> dict[str, Any]:
    return {
        "questions": 0,
        "correct": 0,
        "overspend_usd": 0.0,
        "extra_cost_usd": 0.0,
        "cost_unknown": 0,
        "overtime_s": 0.0,
        "extra_latency_s": 0.0,
        "latency_unknown": 0,
    }


def _extra(mine: float | None, best: float | None) -> float | None:
    return None if mine is None or best is None else mine - best


def _add(cell: dict[str, Any], choice: Mapping[str, Any], best: Mapping[str, Any]) -> None:
    """One question into ``cell``: counted, and its extra over the oracle summed, or counted unknown.

    ``overspend_usd`` and ``overtime_s`` sum only what was dearer or slower than the oracle; the signed
    ``extra_*`` sums, which net a cheaper wrong choice against a dearer one, are kept for ``--json`` only.
    """
    cell["questions"] += 1
    cell["correct"] += bool(choice["correct"])
    for key, over, unknown in (
        ("cost_usd", "overspend_usd", "cost_unknown"),
        ("latency_s", "overtime_s", "latency_unknown"),
    ):
        extra = _extra(choice[key], best[key])
        if extra is None:
            cell[unknown] += 1
            continue
        cell["extra_" + key] += extra
        cell[over] += max(extra, 0.0)


def ordered(tools: Iterable[str]) -> list[str]:
    """Tools in :data:`TOOL_ORDER`, any other by name, then :data:`UNSERVED` and :data:`NONE_RIGHT`."""
    found = set(tools)
    known = [t for t in TOOL_ORDER if t in found]
    rest = sorted(found - set(TOOL_ORDER) - {UNSERVED, NONE_RIGHT})
    return known + rest + [t for t in (UNSERVED, NONE_RIGHT) if t in found]


def confusion(router: Sequence[Mapping[str, Any]], oracle: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """A router's choices against the oracle's on the same questions: the matrix, and what its tool choice cost.

    ``cells[chosen][best]`` counts the questions, how many the router got right, and what the router spent and
    took beyond the oracle there. ``wrong_tool`` is the questions some tool got right and the router's tool did
    not; ``not_cheapest`` those it got right on another tool than the best, charged against the cheapest right
    path; ``all`` every question. ``accuracy_lost`` is the oracle's accuracy minus the router's, from every
    cause. The per-question figures divide by every question scored, so two routers on one split compare.
    """
    best_of = {c["question_id"]: c for c in oracle}
    cells: dict[str, dict[str, dict[str, Any]]] = {}
    wrong_tool, not_cheapest, everything = _cell(), _cell(), _cell()
    oracle_correct = 0
    for choice in router:
        best = best_of[choice["question_id"]]
        oracle_correct += bool(best["correct"])
        best_tool = best["tool"] if best["correct"] else NONE_RIGHT
        _add(cells.setdefault(choice["tool"], {}).setdefault(best_tool, _cell()), choice, best)
        _add(everything, choice, best)
        if choice["right_tools"] and choice["tool"] not in choice["right_tools"]:
            _add(wrong_tool, choice, best)
        elif choice["correct"] and choice["tool"] != best_tool:
            _add(not_cheapest, choice, best)
    n = everything["questions"]
    return {
        "chosen_tools": ordered(cells),
        "best_tools": ordered(b for row in cells.values() for b in row),
        "cells": {chosen: {b: cells[chosen][b] for b in ordered(row)} for chosen, row in cells.items()},
        "wrong_tool": wrong_tool,
        "not_cheapest": not_cheapest,
        "all": everything,
        "oracle_correct": oracle_correct,
        "accuracy_lost": (oracle_correct - everything["correct"]) / n if n else None,
        "overspend_per_question_usd": everything["overspend_usd"] / n if n else None,
        "overtime_per_question_s": everything["overtime_s"] / n if n else None,
    }


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


def matrices(routers: Mapping[str, Mapping[str, Any]], oracle: str) -> dict[str, dict[str, Any]]:
    """Every router's held-out matrix but the oracle's, against the oracle's choices."""
    best = routers[oracle][HELD_OUT_CHOICES]
    return {spec: confusion(r[HELD_OUT_CHOICES], best) for spec, r in routers.items() if spec != oracle}


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
