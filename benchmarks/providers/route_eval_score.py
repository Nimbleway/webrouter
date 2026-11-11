"""route-eval's scoring: a router replayed on both parts of the split, its numbers, its regret and its verdict.

:func:`_score_router` fits a router on the training questions and replays it
on both parts; :func:`score` turns the outcomes into accuracy, cost and
latency; :func:`_add_regret` sets each router against the oracle and the
static order, and :func:`verdict` judges the held-out margin. Imported as a
sibling, by name, as ``_common`` is. Experimental until 1.0, like the rest of
route-eval.
"""

from __future__ import annotations

import statistics
import sys
from collections.abc import Sequence
from typing import Any

from _common import _pct, _wilson
from route_eval_matrix import EVERY_CHOICES, HELD_OUT_CHOICES, choices
from route_eval_replay import Outcome, replay
from route_eval_routers import ALWAYS, ORACLE, STATIC, load_router, static_router
from route_eval_sets import Question

BASELINE = "baseline"
NO_GAIN_FLOOR = "no gain (within noise floor)"
NO_GAIN_INTERVAL = "no gain (static inside the 95% interval)"
PARTS = ("in_sample", "held_out")
EVERY_QUESTION = "every_question"
"""A router not fit on the training split is scored on every question too, under this key."""

MSG_ROUTER_RAISED = "{spec} raised on {set_name}, first time shown, scored as a router failure: {error}"
MSG_NOT_A_CANDIDATE = "{spec!r}: {backend} has no rows in {set_name} (it has: {known})"


def score(outcomes: Sequence[Outcome]) -> dict[str, Any]:
    """A router's numbers over some questions; accuracy is correct out of all of them."""
    n = len(outcomes)
    correct = sum(o.correct for o in outcomes)
    known = [o.cost_usd for o in outcomes if o.cost_usd is not None]
    latencies = [o.latency_s for o in outcomes if o.latency_s is not None]
    return {
        "questions": n,
        "correct": correct,
        "accuracy": correct / n if n else None,
        "accuracy_ci95": _wilson(correct, n),
        "mean_cost_usd": statistics.fmean(known) if known else None,
        "cost_unknown": n - len(known),
        "mean_latency_s": statistics.fmean(latencies) if latencies else None,
        "latency_p95_s": _pct(latencies, 0.95),
        "latency_unknown": n - len(latencies),
        "fallbacks": sum(o.fallback for o in outcomes),
        "router_failed": sum(o.router_failed for o in outcomes),
        "unserved": sum(o.served_by is None for o in outcomes),
        "ungraded": sum(o.ungraded for o in outcomes),
        "unscorable": sum(o.unscorable for o in outcomes),
        "mean_overhead_s": statistics.fmean(o.overhead_s for o in outcomes) if outcomes else None,
    }


def verdict(gain: float | None, floor: float, static: float | None, ci95: Sequence[float] | None) -> str:
    """A held-out margin over the static order: a gain or a loss only past both the noise floor and the router's
    95% interval, which on 30 held-out questions is wider than the floor (one question is 3.3 points)."""
    if gain is None or static is None or ci95 is None:
        return "n/a"
    low, high = ci95
    if gain > floor and static < low:
        return "gain"
    if gain < -floor and static > high:
        return "loss"
    if abs(gain) <= floor:
        return NO_GAIN_FLOOR
    return NO_GAIN_INTERVAL


def _score_router(
    spec: str, method: str, name: str, train: list[Question], test: list[Question], candidates: list[str]
) -> dict[str, Any]:
    """One router fit on ``train`` and scored on both parts; its first exception, if any, printed once."""
    if spec.startswith(ALWAYS) and spec.removeprefix(ALWAYS) not in candidates:
        backend = spec.removeprefix(ALWAYS)
        raise SystemExit(
            MSG_NOT_A_CANDIDATE.format(spec=spec, backend=backend, set_name=name, known=", ".join(candidates))
        )
    router = load_router(spec, method)
    fit = getattr(router, "fit", None)
    if callable(fit):
        fit(train)
    fallback_order = static_router(method)
    outcomes = {
        part: [replay(router, q, fallback_order) for q in qs] for part, qs in (("in_sample", train), ("held_out", test))
    }
    first = next((o.router_error for part in outcomes.values() for o in part if o.router_error), None)
    if first is not None:
        print(MSG_ROUTER_RAISED.format(spec=spec, set_name=name, error=first), file=sys.stderr)
    result: dict[str, Any] = {
        **{part: score(found) for part, found in outcomes.items()},
        "fit": callable(fit),
        HELD_OUT_CHOICES: choices(test, outcomes["held_out"], method),
    }
    if not callable(fit):  # nothing learned from the training split, so every question is fair to score it on
        every = [*outcomes["in_sample"], *outcomes["held_out"]]
        result[EVERY_QUESTION] = score(every)
        result[EVERY_CHOICES] = choices([*train, *test], every, method)
    return result


def _add_regret(routers: dict[str, dict[str, Any]], floor: float) -> None:
    """Each router's regret against the oracle and the static order, and its held-out verdict."""
    for part in PARTS:
        best, base = routers[ORACLE][part]["accuracy"], routers[STATIC][part]["accuracy"]
        for result in routers.values():
            acc = result[part]["accuracy"]
            result[part]["regret_oracle"] = None if acc is None else best - acc
            result[part]["regret_static"] = None if acc is None else base - acc
    static = routers[STATIC]["held_out"]["accuracy"]
    for spec, result in routers.items():
        held_out = result["held_out"]
        gain = None if held_out["regret_static"] is None else -held_out["regret_static"]
        result["verdict"] = BASELINE if spec == STATIC else verdict(gain, floor, static, held_out["accuracy_ci95"])
