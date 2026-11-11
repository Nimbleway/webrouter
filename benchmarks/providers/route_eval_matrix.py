"""route-eval's cost-weighted tool confusion matrix: what a router's tool choice cost against the oracle.

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
is still the router's overspend over the oracle. Where two routers part ways
is :mod:`route_eval_compare`. Imported as a sibling, by name, as ``_common``
is. Experimental until 1.0, like the rest of route-eval.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

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


def matrices(routers: Mapping[str, Mapping[str, Any]], oracle: str) -> dict[str, dict[str, Any]]:
    """Every router's held-out matrix but the oracle's, against the oracle's choices."""
    best = routers[oracle][HELD_OUT_CHOICES]
    return {spec: confusion(r[HELD_OUT_CHOICES], best) for spec, r in routers.items() if spec != oracle}
