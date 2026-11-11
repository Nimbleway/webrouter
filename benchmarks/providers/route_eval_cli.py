"""route-eval's command line: the default sets and routers, the arguments, and the run over every set.

``route_eval.py`` stays the entry point and passes its ``evaluate_set`` in, so
this module never imports the replay. Imported as a sibling, by name, as
``_common`` is.

    uv run --all-extras python benchmarks/providers/route_eval.py
    uv run --all-extras python benchmarks/providers/route_eval.py --set sealqa-hard-254 --router always:nimble
    uv run --all-extras python benchmarks/providers/route_eval.py --router mypkg.routers:pick \
        --joined benchmarks/providers/results/frozen-joined
    uv run --all-extras python benchmarks/providers/route_eval.py --set tools-effort-60 \
        --router mypkg.routers:pick --against examples/router_rules.py:route \
        --joined benchmarks/providers/results/frozen-joined

Experimental until 1.0, like the rest of route-eval.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from route_eval_paths import DEFAULT_PATH_ROUTERS
from route_eval_rank import noise_floor
from route_eval_report import markdown
from route_eval_routers import ALWAYS, ORACLE

DEFAULT_SETS = ("sealqa-hard-254", "simpleqa-300-v2", "tools-effort-60")
DEFAULT_ROUTERS = ("always:webiq",)
"""The example router; ``static`` and ``oracle`` are always scored, as the yardsticks. A mixed set has its own."""
DEFAULT_SEED = 0
DEFAULT_TEST_FRACTION = 0.5
DEFAULTS_HELP = f"{', '.join(DEFAULT_ROUTERS)}; on a mixed set {', '.join(DEFAULT_PATH_ROUTERS)}"

MSG_NOWHERE = "{spec!r} is a candidate in none of the sets: {sets}"
MSG_AGAINST_ORACLE = (
    "--against oracle: the oracle is the yardstick, not a router to compare with; "
    "each router's tool confusion matrix already sets it against the oracle"
)


def skip_absent_specs(specs: Sequence[str], candidates: Sequence[str]) -> list[str]:
    """The ``always:`` routers whose choice is not a candidate here: a default set skips them, with a note."""
    return [s for s in specs if s.startswith(ALWAYS) and s.removeprefix(ALWAYS) not in candidates]


def parse_args(argv: list[str], description: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--set", dest="sets", action="append", help=f"frozen set (default: {', '.join(DEFAULT_SETS)})")
    parser.add_argument(
        "--router",
        dest="routers",
        action="append",
        help=f"router to score (default: {DEFAULTS_HELP})",
    )
    parser.add_argument(
        "--against",
        action="append",
        default=[],
        help="router every other one is compared with, where they disagree, held out (static always is)",
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="the train and held-out split's seed")
    parser.add_argument("--test-fraction", type=float, default=DEFAULT_TEST_FRACTION, help="share held out")
    parser.add_argument("--joined", type=Path, default=None, help="frozen.py join's output, for the question text")
    parser.add_argument("--json", type=Path, default=None, help="also write the numbers here")
    args = parser.parse_args(argv)
    if ORACLE in args.against:
        parser.error(MSG_AGAINST_ORACLE)
    return args


def run(
    argv: list[str], evaluate_set: Callable[..., dict[str, Any]], default_sets: Sequence[str], description: str
) -> None:
    """Every set scored by ``evaluate_set``, the report printed and, with ``--json``, the numbers written."""
    args = parse_args(argv, description)
    floor, pairs = noise_floor()
    summaries = [
        evaluate_set(
            name,
            args.routers,
            seed=args.seed,
            test_fraction=args.test_fraction,
            joined=args.joined,
            skip_absent=not args.sets,  # the default sets mix backends and paths; a named set is held to its own
            against=args.against,
        )
        for name in args.sets or default_sets
    ]
    for spec in (*(args.routers or ()), *args.against):
        if all(spec in s["skipped"] for s in summaries):
            raise SystemExit(MSG_NOWHERE.format(spec=spec, sets=", ".join(s["set"] for s in summaries)))
    if args.json:
        args.json.write_text(json.dumps({"noise_floor": floor, "sets": summaries}, indent=2), encoding="utf-8")
    print(markdown(summaries, floor, pairs))
