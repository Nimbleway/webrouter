"""route-eval: replay a router over the frozen results and score it against the static order, offline.

Each question of a frozen set goes to a router, which ranks the backends that
set has rows for -- or, on a mixed set such as ``tools-effort-60``, the paths:
a method, a backend and a mode, as ``research:parallel:low``. The replay walks
that ranking as ``auto`` walks its chain: the first backend whose row did not
fail serves the question, and its recorded grade, latency and cost are what
the router scores. Nothing is called; the numbers are the committed rows in
``frozen/``. The command line, with examples, is :mod:`route_eval_cli`.

A router is ``static`` (today's ``auto`` order; on a mixed set, search then
read), ``oracle`` (the best backend or path for each question, known after the
fact), ``always:<backend>`` (``always:<path>`` on a mixed set), or your own
callable named ``module:attr`` or ``path/to/file.py:attr``. Yours is a
router of the library's contract: it takes a ``RouteRequest`` and returns a
``RouteDecision``, or ``{"decision": ..., "cost_usd": 0.0001}`` when it bills
for itself, and the replay merges it as a routed call would. The request's
``prompt`` is empty unless ``--joined`` names the folder ``frozen.py join``
wrote. If it has a ``fit`` method, it is called with the training questions
before scoring.

Experimental until 1.0: the router interface and the report may change.
"""

from __future__ import annotations

import math
import random
import statistics
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from _common import _pct, _wilson
from frozen import FROZEN_DIR
from route_eval_cli import DEFAULT_ROUTERS, DEFAULT_SEED, DEFAULT_TEST_FRACTION, run, skip_absent_specs
from route_eval_cli import DEFAULT_SETS as DEFAULT_SETS
from route_eval_merge import walk
from route_eval_paths import DEFAULT_PATH_ROUTERS, MIXED, research_by_effort
from route_eval_rank import KEYLESS as KEYLESS
from route_eval_rank import Floor, backend_stats, cost_of, floor_for, is_served, recommended, too_few
from route_eval_rank import noise_floor as noise_floor
from route_eval_rank import rank_backends as rank_backends
from route_eval_report import markdown as markdown
from route_eval_routers import ALWAYS, ORACLE, STATIC, STATIC_ORDER, Router, load_router, static_router
from route_eval_routers import adapt as adapt
from route_eval_routers import always_router as always_router
from route_eval_routers import oracle_router as oracle_router
from route_eval_sets import TWIN_SETS as TWIN_SETS
from route_eval_sets import Question, load_set

from web_router.constants_merge import ROUTER_ERROR
from web_router.routing.merge import FallbackReason

BASELINE = "baseline"
NO_GAIN_FLOOR = "no gain (within noise floor)"
NO_GAIN_INTERVAL = "no gain (static inside the 95% interval)"
PARTS = ("in_sample", "held_out")

MSG_ROUTER_RAISED = "{spec} raised on {set_name}, first time shown, scored as a router failure: {error}"
MSG_EMPTY_SPLIT = (
    "{name}: --test-fraction {test_fraction} splits {questions} questions into {train} in-sample and {test} held "
    "out; the fraction must be between 0 and 1 and both parts non-empty"
)
MSG_BAD_COST = "the router stated a cost of {cost}; a cost is a finite number of dollars, 0 or more"
MSG_SKIPPED = "{spec} is not a candidate in {set_name}; skipped there"
MSG_NOT_A_CANDIDATE = "{spec!r}: {backend} has no rows in {set_name} (it has: {known})"


@dataclass(frozen=True)
class Outcome:
    """What a router's choice came to on one question."""

    correct: bool
    served_by: str | None
    latency_s: float | None
    """``None`` when any attempt's latency is unknown: the total is then unknown too."""
    cost_usd: float | None
    fallback: bool
    """The router's first choice did not serve: it failed, returned nothing usable, or its backend's row failed."""
    router_failed: bool
    overhead_s: float
    router_error: str | None = None
    """The exception the router raised, as ``Type: message``; ``None`` when it raised nothing."""
    ungraded: bool = False
    """The backend served, but the judge failed or the answer leaked the set's own pages: never correct."""
    unscorable: int = 0
    """Backends the merged order named that have no frozen row to score, such as research at an effort never run."""


def split(questions: Sequence[Question], seed: int, test_fraction: float) -> tuple[list[Question], list[Question]]:
    """One seeded train and held-out split, the same for every router."""
    shuffled = list(questions)
    random.Random(seed).shuffle(shuffled)
    cut = len(shuffled) - round(len(shuffled) * test_fraction)
    return shuffled[:cut], shuffled[cut:]


def _decision(decision: Any) -> tuple[Any, float]:
    """A router's answer -- a ``RouteDecision``, a ranking, or either with its stated cost -- and that cost."""
    if isinstance(decision, Mapping):
        cost = float(decision.get("cost_usd") or 0.0)
        if not math.isfinite(cost) or cost < 0:
            raise ValueError(MSG_BAD_COST.format(cost=cost))
        if "decision" in decision:
            return decision["decision"], cost
        return [str(p) for p in decision.get("ranked") or ()], cost
    if isinstance(decision, str):
        return [decision], 0.0
    if isinstance(decision, list | tuple):
        return [str(p) for p in decision], 0.0
    return decision, 0.0


def replay(router: Router, q: Question, fallback_order: Router) -> Outcome:
    """One question through a router, walked as ``auto`` walks its chain.

    The router's answer goes through the library's merge rule
    (:func:`route_eval_merge.walk`): its ranking first, an id that is not a
    candidate dropped, then every other candidate in the static order. A row
    whose backend answered but went ungraded (the judge failed, or the answer
    leaked the set's own pages) serves the question: no fallback, not correct,
    counted as ungraded. A router that raises, answers with no well-formed
    decision, or ranks no candidate at all counts as failed, and the static
    order is walked. A failed row is passed over -- its time to failure and its
    estimated cost charged, as a routed call's budget charges a failed attempt
    -- and the next one tried; the first choice not serving is a fallback. A
    question nobody served is a miss. The router's own time and stated cost
    are charged on every question.
    """
    started = time.perf_counter()
    error: str | None = None
    failure: FallbackReason | None = None
    try:
        answer, cost = _decision(router(q))
    except Exception as exc:  # a router that raises is scored as failed, not fatal to the replay
        answer, cost, failure = None, 0.0, ROUTER_ERROR
        error = f"{type(exc).__name__}: {exc}"
    overhead = time.perf_counter() - started
    ranked, router_failed, unscorable = walk(answer, q, fallback_order, failure)
    latency: float | None = overhead
    total: float | None = cost
    for i, provider in enumerate(ranked):
        row = q.rows[provider]
        latency = None if latency is None or row["latency_s"] is None else latency + row["latency_s"]
        price = cost_of(row)
        total = None if total is None or price is None else total + price
        if is_served(row):
            fallback = router_failed or i > 0
            ungraded = row["error"] is not None
            correct = not ungraded and row["grade"] == "correct"
            return Outcome(
                correct, provider, latency, total, fallback, router_failed, overhead, error, ungraded, unscorable
            )
    return Outcome(False, None, latency, total, True, router_failed, overhead, error, unscorable=unscorable)


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


def _check_split(name: str, test_fraction: float, questions: int, train: int, test: int) -> None:
    if not 0 < test_fraction < 1 or not train or not test:
        raise SystemExit(
            MSG_EMPTY_SPLIT.format(name=name, test_fraction=test_fraction, questions=questions, train=train, test=test)
        )


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
    return {part: score(found) for part, found in outcomes.items()}


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


def evaluate_set(
    name: str,
    specs: Sequence[str] | None,
    *,
    seed: int = DEFAULT_SEED,
    test_fraction: float = DEFAULT_TEST_FRACTION,
    floor: float | None = None,
    folder: Path = FROZEN_DIR,
    joined: Path | None = None,
    skip_absent: bool = False,
) -> dict[str, Any]:
    """Every router in ``specs``, with ``static`` and ``oracle``, scored in-sample and held out on one set.

    ``specs`` of ``None`` scores the set's example routers: :data:`DEFAULT_ROUTERS`, or on a mixed set
    :data:`~route_eval_paths.DEFAULT_PATH_ROUTERS`. With ``skip_absent``, an ``always:`` router whose
    choice is not a candidate here is skipped, with a note, instead of refused: a backend is no path.
    ``floor`` of ``None`` ranks and judges with the method's own floor (:func:`~route_eval_rank.floor_for`).
    """
    method, questions = load_set(name, folder, joined)
    ranked_with = floor_for(method) if floor is None else Floor(method, floor, None)
    if specs is None:
        specs = DEFAULT_PATH_ROUTERS if method == MIXED else DEFAULT_ROUTERS
    train, test = split(questions, seed, test_fraction)
    _check_split(name, test_fraction, len(questions), len(train), len(test))
    candidates = sorted({p for q in questions for p in q.rows})
    skipped = skip_absent_specs(specs, candidates) if skip_absent else []
    for spec in skipped:
        print(MSG_SKIPPED.format(spec=spec, set_name=name), file=sys.stderr)
    names = list(dict.fromkeys((STATIC, ORACLE, *(s for s in specs if s not in skipped))))
    routers = {spec: _score_router(spec, method, name, train, test, candidates) for spec in names}
    _add_regret(routers, ranked_with.value)
    stats = backend_stats(questions)  # nothing is fit to the order, so it ranks on every question
    return {
        "set": name,
        "method": method,
        "skipped": skipped,
        "questions": len(questions),
        "train": len(train),
        "held_out": len(test),
        "seed": seed,
        "prompts": all(q.prompt is not None for q in questions),
        "static_order": static_router(method)(Question("", None, {p: {} for p in candidates})),
        "routers": routers,
        "backends": stats,
        "noise_floor": ranked_with.as_json(),
        "recommended_order": recommended(stats, ranked_with.value),
        "too_few_questions": too_few(stats),
        "efforts": [{"backend": b, "effort": e, **row} for b, e, row in research_by_effort(stats, STATIC_ORDER)],
    }


def main(argv: list[str] | None = None) -> None:
    """The command line, in :mod:`route_eval_cli`."""
    run(sys.argv[1:] if argv is None else argv, evaluate_set, DEFAULT_SETS, (__doc__ or "").split("\n\n")[0])


if __name__ == "__main__":
    main()
