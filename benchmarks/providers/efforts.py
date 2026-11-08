"""The research effort table, generated: each research backend's accuracy and latency at each effort it was run at.

Writes ``src/web_router/data/efforts.json``, which the budget's effort mapping
(:mod:`web_router.routing.params`) reads: over every question of
``tools-effort-60``, each research path's accuracy (correct out of every
question, a failed row a miss, a rate-limit or quota failure left out, as
route-eval ranks), its median and 90th-percentile seconds over the rows it
served, and the noise floor route-eval reports for that set
(:func:`route_eval_rank.floor_for`). An effort counted on fewer than
:data:`~route_eval_rank.MIN_QUESTIONS` questions is listed apart as
``too_few_questions``, unmeasured, as is one that served nothing.

Cost is not copied here: the mapping reads it from the price list, the figure
a budget is held to, so an effort with no known price never fits a cost cap.

    make efforts                                                        # rewrite the file
    uv run --all-extras python benchmarks/providers/efforts.py --check  # exit 1 if it is stale

Experimental until 1.0: the file's shape may change.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from _common import _pct, _round
from _tools_effort_plan import RESEARCH
from frozen import FROZEN_DIR
from route_eval_floor import Floor, floor_for
from route_eval_paths import path_id, research_by_effort
from route_eval_rank import MIN_QUESTIONS, backend_stats, is_served, too_few
from route_eval_sets import Question, load_set

from web_router.constants_params import EFFORT_RULE

EFFORTS_FILE = Path(__file__).resolve().parents[2] / "src" / "web_router" / "data" / "efforts.json"
EFFORT_SET = "tools-effort-60"
"""The frozen set every research effort was asked on: the same questions at each effort."""
EFFORTS_COMMAND = "make efforts"
P90 = 0.9
MSG_EFFORTS_STALE = "{path} is stale: run `{command}` and commit the result"
MSG_EFFORTS_WRITTEN = "wrote {path}"


def _p90(questions: Sequence[Question], path: str) -> float | None:
    """The 90th-percentile seconds, nearest rank, over the rows ``path`` served, as ``make latency`` takes it."""
    rows = (q.rows[path] for q in questions if path in q.rows)
    return _pct([float(r["latency_s"]) for r in rows if is_served(r) and r["latency_s"] is not None], P90)


def _entries(questions: Sequence[Question]) -> dict[str, dict[str, Any]]:
    """Each research backend's efforts: the measured ones with their numbers, and the ones held back, by name."""
    stats = backend_stats(questions)
    few = set(too_few(stats))
    out: dict[str, dict[str, Any]] = {}
    for provider, effort, s in research_by_effort(stats, {}):
        path = path_id({"method": RESEARCH, "provider": provider, "mode": effort})
        entry = out.setdefault(provider, {"measured": {}, "too_few_questions": [], "no_row_served": []})
        if path in few:
            entry["too_few_questions"].append(effort)
        elif not s["served"]:
            entry["no_row_served"].append(effort)
        else:
            entry["measured"][effort] = {
                "accuracy": _round(s["accuracy_all"]),
                "correct": s["correct"],
                "questions": s["questions"],
                "rate_limit_or_quota_left_out": s["excluded"],
                "latency_p50_s": _round(s["latency_p50"]),
                "latency_p90_s": _round(_p90(questions, path)),
            }
    return dict(sorted(out.items()))


def effort_file(*, folder: Path = FROZEN_DIR, floor: Floor | None = None) -> dict[str, Any]:
    """What ``efforts.json`` should hold: every research path of :data:`EFFORT_SET`, with the set's noise floor."""
    method, questions = load_set(EFFORT_SET, folder)
    floor = floor_for(method, folder) if floor is None else floor
    return {
        "generated_by": f"{EFFORTS_COMMAND} (benchmarks/providers/efforts.py); do not edit by hand",
        "rule": EFFORT_RULE,
        "set": EFFORT_SET,
        "questions": len(questions),
        "min_questions": MIN_QUESTIONS,
        "noise_floor": floor.as_json(6),
        "research": _entries(questions),
    }


def emit_efforts(check: bool, path: Path = EFFORTS_FILE) -> int:
    """Rewrite ``efforts.json`` from the frozen results, or with ``check`` only say whether it is stale."""
    wanted = json.dumps(effort_file(), indent=2) + "\n"
    if check:
        if not path.is_file() or path.read_text(encoding="utf-8") != wanted:
            print(MSG_EFFORTS_STALE.format(path=path, command=EFFORTS_COMMAND), file=sys.stderr)
            return 1
        return 0
    path.write_text(wanted, encoding="utf-8")
    print(MSG_EFFORTS_WRITTEN.format(path=path))
    return 0


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="write nothing; exit 1 if the file is stale")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    raise SystemExit(emit_efforts(args.check))


if __name__ == "__main__":
    main()
