"""route-eval: replay a router over the frozen results and score it against the static order, offline.

Each question of a frozen set goes to a router, which ranks the backends that
set has rows for. The replay walks that ranking as ``auto`` walks its chain:
the first backend whose row did not fail serves the question, and its recorded
grade, latency and cost are what the router scores. Nothing is called; the
numbers are the committed rows in ``frozen/``.

    uv run --all-extras python benchmarks/providers/route_eval.py
    uv run --all-extras python benchmarks/providers/route_eval.py --set sealqa-hard-254 --router always:nimble
    uv run --all-extras python benchmarks/providers/route_eval.py --router mypkg.routers:pick \
        --joined benchmarks/providers/results/frozen-joined

A router is ``static`` (today's ``auto`` order), ``oracle`` (the best backend
for each question, known after the fact), ``always:<backend>``, or your own
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
import importlib
import importlib.util
import json
import random
import statistics
import sys
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from _common import _p, _pct, _s, _wilson
from _report_tables import grade_stats
from frozen import FROZEN_DIR, SUFFIX, read_rows

from web_router import prices
from web_router.constants import AUTO_APPENDED, AUTO_KEYLESS, AUTO_PREFERRED
from web_router.constants_answer import AUTO_ANSWER_PREFERRED
from web_router.providers.auto import AutoProvider
from web_router.providers.auto_answer import AutoAnswerProvider

DEFAULT_SETS = ("sealqa-hard-254", "simpleqa-300-v2")
DEFAULT_ROUTERS = ("always:webiq",)
"""The example router; ``static`` and ``oracle`` are always scored, as the yardsticks."""
TWIN_SETS = ("sealqa-hard-254", "sealqa-hard-254-twin")
"""The same questions run twice: their spread is the noise floor."""
DEFAULT_SEED = 0
DEFAULT_TEST_FRACTION = 0.5
DEFAULT_MODE = "default"
STATIC = "static"
ORACLE = "oracle"
ALWAYS = "always:"
BASELINE = "baseline"

STATIC_ORDER: dict[str, tuple[str, ...]] = {
    "search": tuple(name for name, _ in AutoProvider.default_chain(frozenset((*AUTO_PREFERRED, *AUTO_APPENDED)))),
    "answer": tuple(name for name, _ in AutoAnswerProvider.default_chain(frozenset(AUTO_ANSWER_PREFERRED))),
}
"""Today's ``auto`` order per method, built as ``auto`` builds it with a key for every backend: what a router must beat.

For search that is the preferred backends, then the keyless tier, then the
backends appended since the last reordering.
"""
KEYLESS = frozenset(name for name, options in AUTO_KEYLESS if not options.get("allow_keyless"))
"""Backends that are only ever keyless (``you-mcp``): a ranking keeps them last, as ``auto`` does."""

MSG_UNKNOWN_SET = "{name}: no frozen set by that name (there are: {known})"
MSG_NO_JOINED = "{path} not found: run frozen.py join first, or leave --joined out"
MSG_BAD_ROUTER = "{spec!r}: a router is static, oracle, always:<backend>, module:attr or path/file.py:attr"
MSG_ROUTER_RAISED = "{spec} raised on {set_name}, first time shown, scored as a router failure: {error}"
MSG_EMPTY_SPLIT = (
    "{name}: --test-fraction {test_fraction} splits {questions} questions into {train} in-sample and {test} held "
    "out; both must be non-empty"
)
MSG_NOT_A_CANDIDATE = "{spec!r}: {backend} has no rows in {set_name} (it has: {known})"


@dataclass(frozen=True)
class Question:
    """One question of a set: its id, its text when joined, and each candidate backend's frozen row."""

    question_id: str
    prompt: str | None
    rows: Mapping[str, Mapping[str, Any]]

    @property
    def candidates(self) -> list[str]:
        return sorted(self.rows)


@dataclass(frozen=True)
class Outcome:
    """What a router's choice came to on one question."""

    correct: bool
    served_by: str | None
    latency_s: float
    cost_usd: float | None
    fallback: bool
    """The router's first choice did not serve: it failed, returned nothing usable, or its backend's row failed."""
    router_failed: bool
    overhead_s: float
    router_error: str | None = None
    """The exception the router raised, as ``Type: message``; ``None`` when it raised nothing."""


Router = Callable[[Question], Any]
"""What the replay calls: a question in, a ranking (or ``{"ranked", "cost_usd"}``) out."""


def load_set(name: str, folder: Path = FROZEN_DIR, joined: Path | None = None) -> tuple[str, list[Question]]:
    """A frozen set's method and questions; each question's text from ``joined`` when given, else ``None``.

    Only rows in the backend's default mode take part: v1 ranks backends, not modes.
    """
    path = folder / (name + SUFFIX)
    if not path.is_file():
        known = sorted(p.name.removesuffix(SUFFIX) for p in folder.glob("*" + SUFFIX))
        raise SystemExit(MSG_UNKNOWN_SET.format(name=name, known=", ".join(known)))
    prompts: dict[str, str] = {}
    if joined is not None:
        joined_file = joined / (name + ".jsonl")
        if not joined_file.is_file():
            raise SystemExit(MSG_NO_JOINED.format(path=joined_file))
        for line in joined_file.read_text(encoding="utf-8").splitlines():
            if line:
                row = json.loads(line)
                prompts[row["question_id"]] = row["question"]
    by_question: dict[str, dict[str, dict[str, Any]]] = {}
    methods = set()
    for row in read_rows(path):
        if row["mode"] != DEFAULT_MODE:
            continue
        methods.add(row["method"])
        by_question.setdefault(row["question_id"], {})[row["provider"]] = row
    (method,) = methods
    questions = [Question(qid, prompts.get(qid), rows) for qid, rows in sorted(by_question.items())]
    return method, questions


def split(questions: Sequence[Question], seed: int, test_fraction: float) -> tuple[list[Question], list[Question]]:
    """One seeded train and held-out split, the same for every router."""
    shuffled = list(questions)
    random.Random(seed).shuffle(shuffled)
    cut = len(shuffled) - round(len(shuffled) * test_fraction)
    return shuffled[:cut], shuffled[cut:]


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
        stats = grade_stats(graded)
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


def static_router(method: str) -> Router:
    order = STATIC_ORDER.get(method, ())

    def route(q: Question) -> list[str]:
        return [p for p in order if p in q.rows] + [p for p in q.candidates if p not in order]

    return route


def oracle_router(method: str) -> Router:
    """The backend that got each question right, cheapest then fastest; the static order when none did."""
    static = static_router(method)

    def route(q: Question) -> list[str]:
        right = [p for p, r in q.rows.items() if r["error"] is None and r["grade"] == "correct"]
        right.sort(key=lambda p: (_cost_key(cost_of(q.rows[p])), _latency_key(q.rows[p]["latency_s"]), p))
        return right + [p for p in static(q) if p not in right]

    return route


def always_router(backend: str) -> Router:
    return lambda q: [backend]


def adapt(user: Callable[..., Any]) -> Router:
    """A ``(prompt, candidates)`` callable as a replay router: the thin layer a later routing contract replaces."""

    def route(q: Question) -> Any:
        return user(q.prompt, q.candidates)

    if hasattr(user, "fit"):
        route.fit = user.fit  # type: ignore[attr-defined]
    return route


def load_router(spec: str, method: str) -> Router:
    """A router named on the command line."""
    if spec == STATIC:
        return static_router(method)
    if spec == ORACLE:
        return oracle_router(method)
    if spec.startswith(ALWAYS):
        return always_router(spec.removeprefix(ALWAYS))
    target, sep, attr = spec.rpartition(":")
    if not sep or not target or not attr:
        raise SystemExit(MSG_BAD_ROUTER.format(spec=spec))
    if target.endswith(".py"):
        module_spec = importlib.util.spec_from_file_location(Path(target).stem, target)
        if module_spec is None or module_spec.loader is None:
            raise SystemExit(MSG_BAD_ROUTER.format(spec=spec))
        module = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(module)
    else:
        module = importlib.import_module(target)
    return adapt(getattr(module, attr))


def _decision(decision: Any) -> tuple[list[str], float]:
    if isinstance(decision, Mapping):
        return [str(p) for p in decision.get("ranked") or ()], float(decision.get("cost_usd") or 0.0)
    if isinstance(decision, str):
        return [decision], 0.0
    return [str(p) for p in decision or ()], 0.0


def replay(router: Router, q: Question, fallback_order: Router) -> Outcome:
    """One question through a router, walked as ``auto`` walks its chain.

    A router that raises, ranks nothing, or names any backend that is not a
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
    latency = overhead
    total: float | None = cost
    for i, provider in enumerate(ranked):
        row = q.rows[provider]
        latency += row["latency_s"] or 0.0
        if row["error"] is None:
            price = cost_of(row)
            total = None if price is None else cost + price
            fallback = router_failed or i > 0
            correct = row["grade"] == "correct"
            return Outcome(correct, provider, latency, total, fallback, router_failed, overhead, error)
    return Outcome(False, None, latency, total, True, router_failed, overhead, error)


def score(outcomes: Sequence[Outcome]) -> dict[str, Any]:
    """A router's numbers over some questions; accuracy is correct out of all of them."""
    n = len(outcomes)
    correct = sum(o.correct for o in outcomes)
    known = [o.cost_usd for o in outcomes if o.cost_usd is not None]
    latencies = [o.latency_s for o in outcomes]
    return {
        "questions": n,
        "correct": correct,
        "accuracy": correct / n if n else None,
        "accuracy_ci95": _wilson(correct, n),
        "mean_cost_usd": statistics.fmean(known) if known else None,
        "cost_unknown": n - len(known),
        "mean_latency_s": statistics.fmean(latencies) if latencies else None,
        "latency_p95_s": _pct(latencies, 0.95),
        "fallbacks": sum(o.fallback for o in outcomes),
        "router_failed": sum(o.router_failed for o in outcomes),
        "unserved": sum(o.served_by is None for o in outcomes),
        "mean_overhead_s": statistics.fmean(o.overhead_s for o in outcomes) if outcomes else None,
    }


def verdict(gain: float | None, floor: float) -> str:
    """A held-out margin over the static order, read against the noise floor."""
    if gain is None:
        return "n/a"
    if gain > floor:
        return "gain"
    if gain < -floor:
        return "loss"
    return "no gain (within noise floor)"


def evaluate_set(
    name: str,
    specs: Sequence[str],
    *,
    seed: int = DEFAULT_SEED,
    test_fraction: float = DEFAULT_TEST_FRACTION,
    floor: float,
    folder: Path = FROZEN_DIR,
    joined: Path | None = None,
) -> dict[str, Any]:
    """Every router in ``specs``, with ``static`` and ``oracle``, scored in-sample and held out on one set."""
    method, questions = load_set(name, folder, joined)
    train, test = split(questions, seed, test_fraction)
    if not train or not test:
        raise SystemExit(
            MSG_EMPTY_SPLIT.format(
                name=name, test_fraction=test_fraction, questions=len(questions), train=len(train), test=len(test)
            )
        )
    candidates = sorted({p for q in questions for p in q.rows})
    fallback_order = static_router(method)
    names = list(dict.fromkeys((STATIC, ORACLE, *specs)))
    routers: dict[str, dict[str, Any]] = {}
    for spec in names:
        if spec.startswith(ALWAYS) and spec.removeprefix(ALWAYS) not in candidates:
            backend = spec.removeprefix(ALWAYS)
            raise SystemExit(
                MSG_NOT_A_CANDIDATE.format(spec=spec, backend=backend, set_name=name, known=", ".join(candidates))
            )
        router = load_router(spec, method)
        fit = getattr(router, "fit", None)
        if callable(fit):
            fit(train)
        outcomes = {
            part: [replay(router, q, fallback_order) for q in qs]
            for part, qs in (("in_sample", train), ("held_out", test))
        }
        first = next((o.router_error for part in outcomes.values() for o in part if o.router_error), None)
        if first is not None:
            print(MSG_ROUTER_RAISED.format(spec=spec, set_name=name, error=first), file=sys.stderr)
        routers[spec] = {part: score(found) for part, found in outcomes.items()}
    for part in ("in_sample", "held_out"):
        best, base = routers[ORACLE][part]["accuracy"], routers[STATIC][part]["accuracy"]
        for result in routers.values():
            acc = result[part]["accuracy"]
            result[part]["regret_oracle"] = None if acc is None else best - acc
            result[part]["regret_static"] = None if acc is None else base - acc
    for spec, result in routers.items():
        regret = result["held_out"]["regret_static"]
        result["verdict"] = BASELINE if spec == STATIC else verdict(None if regret is None else -regret, floor)
    stats = backend_stats(test)
    return {
        "set": name,
        "method": method,
        "questions": len(questions),
        "train": len(train),
        "held_out": len(test),
        "seed": seed,
        "prompts": all(q.prompt is not None for q in questions),
        "static_order": fallback_order(Question("", None, {p: {} for p in candidates})),
        "routers": routers,
        "backends": stats,
        "recommended_order": recommended(stats, floor),
    }


def _usd(value: float | None, unknown: int = 0) -> str:
    if value is None:
        return "unknown"
    text = f"${value:.4f}"
    return text + (f" ({unknown} unknown)" if unknown else "")


def _ms(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 1000:.2f} ms"


def _pts(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:+.1f}"


def markdown(summaries: Sequence[dict[str, Any]], floor: float, pairs: Mapping[str, tuple[float, float]]) -> str:
    out = ["# route-eval: routers replayed over the frozen results\n"]
    spread = ", ".join(f"`{p}` {a:.1%} vs {b:.1%}" for p, (a, b) in pairs.items())
    out.append(
        f"**Noise floor: {floor * 100:.1f} points**, the largest accuracy gap one backend shows between "
        f"`{TWIN_SETS[0]}` and `{TWIN_SETS[1]}`, the same questions run twice ({spread}). A margin no larger is "
        "*no gain*. It is measured on SealQA and used for every set, the only twin run there is.\n"
    )
    out.append(
        "Accuracy is correct out of every question, a question no backend served counting as a miss. Regret is "
        "in accuracy points (positive: behind). Cost is the backend's stated cost, else its list price from "
        "`web_router.prices` (an estimate); the mean is over the questions whose cost is known, and the count "
        "unknown is shown. Latency adds every failed attempt before the one that served, and the router's own time.\n"
    )
    for s in summaries:
        out.append(
            f"## `{s['set']}` ({s['method']}): {s['questions']} questions, {s['train']} in-sample and "
            f"{s['held_out']} held out (seed {s['seed']})\n"
        )
        if not s["prompts"]:
            out.append(
                "Prompt: unavailable -- routers were given `None` for the question text. Run `frozen.py join` and "
                "pass `--joined` for routers that read it; the baselines below need none.\n"
            )
        out.append("Static order: " + ", ".join(f"`{p}`" for p in s["static_order"]) + "\n")
        out.append(
            "| Router | Accuracy in-sample | Accuracy held out | 95% CI held out | Regret vs oracle (in / out) "
            "| Regret vs static (in / out) | Mean cost held out | Mean latency held out | Fallbacks held out "
            "| Router failed held out | Router overhead | Held out vs static |"
        )
        out.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
        for spec, r in s["routers"].items():
            i, h = r["in_sample"], r["held_out"]
            ci = h["accuracy_ci95"]
            out.append(
                f"| `{spec}` | {_p(i['accuracy'])} | {_p(h['accuracy'])} ({h['correct']}/{h['questions']}) "
                f"| {_p(ci[0]) + '-' + _p(ci[1]) if ci else 'n/a'} "
                f"| {_pts(i['regret_oracle'])} / {_pts(h['regret_oracle'])} "
                f"| {_pts(i['regret_static'])} / {_pts(h['regret_static'])} "
                f"| {_usd(h['mean_cost_usd'], h['cost_unknown'])} | {_s(h['mean_latency_s'])} "
                f"| {h['fallbacks']} | {h['router_failed']} | {_ms(h['mean_overhead_s'])} | {r['verdict']} |"
            )
        out.append("")
        out.append(
            "### Recommended order, from the held-out questions\n\n"
            "By correct out of every held-out question; within the noise floor of the best not yet placed, the "
            "cheaper backend first (an unknown or $0 cost never moves one up), then the faster, then the name. "
            "Keyless backends go last. A backend that served nothing is left out.\n"
        )
        out.append("| Order | Backend | Correct of all questions | Cost per call | p50 latency | Failed |")
        out.append("|---|---|---|---|---|---|")
        for rank, p in enumerate(s["recommended_order"], 1):
            b = s["backends"][p]
            out.append(
                f"| {rank} | `{p}` | {_p(b['accuracy_all'])} ({b['correct']}/{b['questions']}) "
                f"| {_usd(b['cost_usd'])} | {_s(b['latency_p50'])} | {b['failed']} |"
            )
        out.append("")
    return "\n".join(out)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--set", dest="sets", action="append", help=f"frozen set (default: {', '.join(DEFAULT_SETS)})")
    parser.add_argument(
        "--router", dest="routers", action="append", help=f"router to score (default: {', '.join(DEFAULT_ROUTERS)})"
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
            args.routers or DEFAULT_ROUTERS,
            seed=args.seed,
            test_fraction=args.test_fraction,
            floor=floor,
            joined=args.joined,
        )
        for name in args.sets or DEFAULT_SETS
    ]
    if args.json:
        args.json.write_text(json.dumps({"noise_floor": floor, "sets": summaries}, indent=2), encoding="utf-8")
    print(markdown(summaries, floor, pairs))


if __name__ == "__main__":
    main()
