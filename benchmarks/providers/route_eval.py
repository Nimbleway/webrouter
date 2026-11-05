"""route-eval: replay a router over the frozen results and score it against the static order, offline.

Each question of a frozen set goes to a router, which ranks the backends that
set has rows for -- or, on a mixed set such as ``tools-effort-60``, the paths:
a method, a backend and a mode, as ``research:parallel:low``. The replay walks
that ranking as ``auto`` walks its chain: the first backend whose row did not
fail serves the question, and its recorded grade, latency and cost are what
the router scores. Nothing is called; the numbers are the committed rows in
``frozen/``.

    uv run --all-extras python benchmarks/providers/route_eval.py
    uv run --all-extras python benchmarks/providers/route_eval.py --set sealqa-hard-254 --router always:nimble
    uv run --all-extras python benchmarks/providers/route_eval.py --router mypkg.routers:pick \
        --joined benchmarks/providers/results/frozen-joined

A router is ``static`` (today's ``auto`` order; on a mixed set, search then
read), ``oracle`` (the best backend or path for each question, known after the
fact), ``always:<backend>`` (``always:<path>`` on a mixed set), or your own
callable named ``module:attr`` or ``path/to/file.py:attr``. Yours takes
``(prompt, candidates)`` and returns the candidate ids best first, or a
mapping ``{"ranked": [...], "cost_usd": 0.0001}`` when it bills for itself;
``prompt`` is ``None`` unless ``--joined`` names the folder ``frozen.py join``
wrote. If it has a ``fit`` method, it is called with the training questions
before scoring.

Experimental until 1.0: the router interface and the report may change.
"""

from __future__ import annotations

import argparse
import json
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
from route_eval_paths import DEFAULT_PATH_ROUTERS, MIXED, research_by_effort
from route_eval_rank import KEYLESS as KEYLESS
from route_eval_rank import backend_stats, cost_of, is_served, noise_floor, recommended
from route_eval_rank import rank_backends as rank_backends
from route_eval_report import markdown
from route_eval_routers import ALWAYS, ORACLE, STATIC, STATIC_ORDER, Router, load_router, static_router
from route_eval_routers import adapt as adapt
from route_eval_routers import always_router as always_router
from route_eval_routers import oracle_router as oracle_router
from route_eval_sets import TWIN_SETS as TWIN_SETS
from route_eval_sets import Question, load_set

DEFAULT_SETS = ("sealqa-hard-254", "simpleqa-300-v2", "tools-effort-60")
DEFAULT_ROUTERS = ("always:webiq",)
"""The example router; ``static`` and ``oracle`` are always scored, as the yardsticks. A mixed set has its own."""
DEFAULT_SEED = 0
DEFAULT_TEST_FRACTION = 0.5
BASELINE = "baseline"
PARTS = ("in_sample", "held_out")

MSG_ROUTER_RAISED = "{spec} raised on {set_name}, first time shown, scored as a router failure: {error}"
MSG_EMPTY_SPLIT = (
    "{name}: --test-fraction {test_fraction} splits {questions} questions into {train} in-sample and {test} held "
    "out; the fraction must be between 0 and 1 and both parts non-empty"
)
MSG_BAD_COST = "the router stated a cost of {cost}; a cost is a finite number of dollars, 0 or more"
MSG_SKIPPED = "{spec} is not a candidate in {set_name}; skipped there"
MSG_NOWHERE = "{spec!r} is a candidate in none of the sets: {sets}"
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


def split(questions: Sequence[Question], seed: int, test_fraction: float) -> tuple[list[Question], list[Question]]:
    """One seeded train and held-out split, the same for every router."""
    shuffled = list(questions)
    random.Random(seed).shuffle(shuffled)
    cut = len(shuffled) - round(len(shuffled) * test_fraction)
    return shuffled[:cut], shuffled[cut:]


def _decision(decision: Any) -> tuple[list[str], float]:
    if isinstance(decision, Mapping):
        cost = float(decision.get("cost_usd") or 0.0)
        if not math.isfinite(cost) or cost < 0:
            raise ValueError(MSG_BAD_COST.format(cost=cost))
        return [str(p) for p in decision.get("ranked") or ()], cost
    if isinstance(decision, str):
        return [decision], 0.0
    return [str(p) for p in decision or ()], 0.0


def replay(router: Router, q: Question, fallback_order: Router) -> Outcome:
    """One question through a router, walked as ``auto`` walks its chain.

    A row whose backend answered but went ungraded (the judge failed, or the
    answer leaked the set's own pages) serves the question: no fallback, not
    correct, counted as ungraded. A router that raises, ranks nothing, or names any backend that is not a
    candidate falls back to the static order and counts as failed. A failed row is passed over -- its time to failure
    charged, its cost not, since a failed call is not billed by most backends --
    and the next one tried; the first choice not serving is a fallback. A
    question nobody served is a miss. The router's own time and stated cost
    are charged on every question.
    """
    started = time.perf_counter()
    error: str | None = None
    try:
        ranked, cost = _decision(router(q))
        router_failed = False
    except Exception as exc:  # a router that raises is scored as failed, not fatal to the replay
        ranked, cost, router_failed = [], 0.0, True
        error = f"{type(exc).__name__}: {exc}"
    overhead = time.perf_counter() - started
    ranked = list(dict.fromkeys(ranked))
    if not ranked or any(p not in q.rows for p in ranked):
        router_failed = True
        ranked = fallback_order(q)
    latency: float | None = overhead
    total: float | None = cost
    for i, provider in enumerate(ranked):
        row = q.rows[provider]
        latency = None if latency is None or row["latency_s"] is None else latency + row["latency_s"]
        if is_served(row):
            price = cost_of(row)
            total = None if price is None else cost + price
            fallback = router_failed or i > 0
            ungraded = row["error"] is not None
            correct = not ungraded and row["grade"] == "correct"
            return Outcome(correct, provider, latency, total, fallback, router_failed, overhead, error, ungraded)
    return Outcome(False, None, latency, total, True, router_failed, overhead, error)


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
        "mean_overhead_s": statistics.fmean(o.overhead_s for o in outcomes) if outcomes else None,
    }


def verdict(gain: float | None, floor: float, static: float | None, ci95: Sequence[float] | None) -> str:
    """A held-out margin over the static order, read against the noise floor and the router's 95% interval.

    A gain needs both: the margin over the floor and the static order's accuracy below the interval. A loss
    needs the margin under minus the floor and the static order above it. On 30 held-out questions one question
    is 3.3 points, wider than the floor, so the floor alone would call a single question a gain or a loss.
    """
    if gain is None or static is None or ci95 is None:
        return "n/a"
    low, high = ci95
    if gain > floor and static < low:
        return "gain"
    if gain < -floor and static > high:
        return "loss"
    return "no gain (within noise floor)"


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
    floor: float,
    folder: Path = FROZEN_DIR,
    joined: Path | None = None,
    skip_absent: bool = False,
) -> dict[str, Any]:
    """Every router in ``specs``, with ``static`` and ``oracle``, scored in-sample and held out on one set.

    ``specs`` of ``None`` scores the set's example routers: :data:`DEFAULT_ROUTERS`, or on a mixed set
    :data:`~route_eval_paths.DEFAULT_PATH_ROUTERS`. With ``skip_absent``, an ``always:`` router whose
    choice is not a candidate here is skipped, with a note, instead of refused: a backend is no path.
    """
    method, questions = load_set(name, folder, joined)
    if specs is None:
        specs = DEFAULT_PATH_ROUTERS if method == MIXED else DEFAULT_ROUTERS
    train, test = split(questions, seed, test_fraction)
    _check_split(name, test_fraction, len(questions), len(train), len(test))
    candidates = sorted({p for q in questions for p in q.rows})
    skipped = [s for s in specs if skip_absent and s.startswith(ALWAYS) and s.removeprefix(ALWAYS) not in candidates]
    for spec in skipped:
        print(MSG_SKIPPED.format(spec=spec, set_name=name), file=sys.stderr)
    names = list(dict.fromkeys((STATIC, ORACLE, *(s for s in specs if s not in skipped))))
    routers = {spec: _score_router(spec, method, name, train, test, candidates) for spec in names}
    _add_regret(routers, floor)
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
        "recommended_order": recommended(stats, floor),
        "efforts": [{"backend": b, "effort": e, **row} for b, e, row in research_by_effort(stats, STATIC_ORDER)],
    }


DEFAULTS_HELP = f"{', '.join(DEFAULT_ROUTERS)}; on a mixed set {', '.join(DEFAULT_PATH_ROUTERS)}"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--set", dest="sets", action="append", help=f"frozen set (default: {', '.join(DEFAULT_SETS)})")
    parser.add_argument(
        "--router",
        dest="routers",
        action="append",
        help=f"router to score (default: {DEFAULTS_HELP})",
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="the train and held-out split's seed")
    parser.add_argument("--test-fraction", type=float, default=DEFAULT_TEST_FRACTION, help="share held out")
    parser.add_argument("--joined", type=Path, default=None, help="frozen.py join's output, for the question text")
    parser.add_argument("--json", type=Path, default=None, help="also write the numbers here")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    floor, pairs = noise_floor()
    summaries = [
        evaluate_set(
            name,
            args.routers,
            seed=args.seed,
            test_fraction=args.test_fraction,
            floor=floor,
            joined=args.joined,
            skip_absent=not args.sets,  # the default sets mix backends and paths; a named set is held to its own
        )
        for name in args.sets or DEFAULT_SETS
    ]
    for spec in args.routers or ():
        if all(spec in s["skipped"] for s in summaries):
            raise SystemExit(MSG_NOWHERE.format(spec=spec, sets=", ".join(s["set"] for s in summaries)))
    if args.json:
        args.json.write_text(json.dumps({"noise_floor": floor, "sets": summaries}, indent=2), encoding="utf-8")
    print(markdown(summaries, floor, pairs))


if __name__ == "__main__":
    main()
