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

import random
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from frozen import FROZEN_DIR
from route_eval_cli import DEFAULT_ROUTERS, DEFAULT_SEED, DEFAULT_TEST_FRACTION, run, skip_absent_specs
from route_eval_cli import DEFAULT_SETS as DEFAULT_SETS
from route_eval_compare import comparisons
from route_eval_floor import Floor, floor_for
from route_eval_matrix import EVERY_CHOICES, matrices
from route_eval_paths import DEFAULT_PATH_ROUTERS, MIXED, research_by_effort
from route_eval_rank import KEYLESS as KEYLESS
from route_eval_rank import backend_stats, recommended, too_few
from route_eval_rank import noise_floor as noise_floor
from route_eval_rank import rank_backends as rank_backends
from route_eval_replay import Outcome as Outcome
from route_eval_replay import replay as replay
from route_eval_report import markdown as markdown
from route_eval_routers import ORACLE, STATIC, STATIC_ORDER, static_router
from route_eval_routers import adapt as adapt
from route_eval_routers import always_router as always_router
from route_eval_routers import load_router as load_router
from route_eval_routers import oracle_router as oracle_router
from route_eval_score import NO_GAIN_INTERVAL as NO_GAIN_INTERVAL
from route_eval_score import _add_regret, _score_router
from route_eval_score import score as score
from route_eval_score import verdict as verdict
from route_eval_sets import TWIN_SETS as TWIN_SETS
from route_eval_sets import Question, load_set

MSG_EMPTY_SPLIT = (
    "{name}: --test-fraction {test_fraction} splits {questions} questions into {train} in-sample and {test} held "
    "out; the fraction must be between 0 and 1 and both parts non-empty"
)
MSG_SKIPPED = "{spec} is not a candidate in {set_name}; skipped there"


def split(questions: Sequence[Question], seed: int, test_fraction: float) -> tuple[list[Question], list[Question]]:
    """One seeded train and held-out split, the same for every router."""
    shuffled = list(questions)
    random.Random(seed).shuffle(shuffled)
    cut = len(shuffled) - round(len(shuffled) * test_fraction)
    return shuffled[:cut], shuffled[cut:]


def _check_split(name: str, test_fraction: float, questions: int, train: int, test: int) -> None:
    if not 0 < test_fraction < 1 or not train or not test:
        raise SystemExit(
            MSG_EMPTY_SPLIT.format(name=name, test_fraction=test_fraction, questions=questions, train=train, test=test)
        )


def _routers_to_score(
    name: str, specs: Sequence[str], against: Sequence[str], candidates: Sequence[str], skip_absent: bool
) -> tuple[list[str], list[str], list[str]]:
    """The routers skipped here (each noted), every router to score, and the ones every other is compared with."""
    asked = [*specs, *against]
    skipped = skip_absent_specs(asked, candidates) if skip_absent else []
    for spec in skipped:
        print(MSG_SKIPPED.format(spec=spec, set_name=name), file=sys.stderr)
    names = list(dict.fromkeys((STATIC, ORACLE, *(s for s in asked if s not in skipped))))
    bases = [STATIC, *(s for s in against if s not in skipped)]
    return skipped, names, bases


def _ranking(questions: Sequence[Question], ranked_with: Floor) -> dict[str, Any]:
    """The set's backends, their recommended order and research by effort, ranked with ``ranked_with``."""
    stats = backend_stats(questions)  # nothing is fit to the order, so it ranks on every question
    return {
        "backends": stats,
        "noise_floor": ranked_with.as_json(),
        "recommended_order": recommended(stats, ranked_with.value),
        "too_few_questions": too_few(stats),
        "efforts": [{"backend": b, "effort": e, **row} for b, e, row in research_by_effort(stats, STATIC_ORDER)],
    }


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
    against: Sequence[str] = (),
) -> dict[str, Any]:
    """Every router in ``specs``, with ``static`` and ``oracle``, scored in-sample and held out on one set.

    ``specs`` of ``None`` scores the set's example routers: :data:`DEFAULT_ROUTERS`, or on a mixed set
    :data:`~route_eval_paths.DEFAULT_PATH_ROUTERS`. With ``skip_absent``, an ``always:`` router whose
    choice is not a candidate here is skipped, with a note, instead of refused: a backend is no path.
    ``floor`` of ``None`` ranks and judges with the method's own floor (:func:`~route_eval_floor.floor_for`).
    Each router is compared, held out, with ``static`` and every router in ``against``, which are scored too
    (:mod:`route_eval_compare`), and has its cost-weighted tool confusion matrix against the oracle. A router
    with no ``fit`` -- ``static``, ``always:``, a file router that learns nothing -- is also scored and
    compared on every question, where the held-out split only costs it questions.
    """
    method, questions = load_set(name, folder, joined)
    ranked_with = floor_for(method) if floor is None else Floor(method, floor, None)
    if specs is None:
        specs = DEFAULT_PATH_ROUTERS if method == MIXED else DEFAULT_ROUTERS
    train, test = split(questions, seed, test_fraction)
    _check_split(name, test_fraction, len(questions), len(train), len(test))
    candidates = sorted({p for q in questions for p in q.rows})
    skipped, names, bases = _routers_to_score(name, specs, against, candidates, skip_absent)
    routers = {spec: _score_router(spec, method, name, train, test, candidates) for spec in names}
    _add_regret(routers, ranked_with.value)
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
        "confusion": matrices(routers, ORACLE),
        "comparisons": comparisons(routers, bases, ORACLE),
        "comparisons_every_question": comparisons(routers, bases, ORACLE, EVERY_CHOICES),
        **_ranking(questions, ranked_with),
    }


def main(argv: list[str] | None = None) -> None:
    """The command line, in :mod:`route_eval_cli`."""
    run(sys.argv[1:] if argv is None else argv, evaluate_set, DEFAULT_SETS, (__doc__ or "").split("\n\n")[0])


if __name__ == "__main__":
    main()
