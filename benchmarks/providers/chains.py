"""The default ``auto`` orders, generated: search's keyed members and answer's, ranked by route-eval's rule.

Writes ``src/web_router/data/chains.json``, which ``auto`` reads: each method's
recommended order (:func:`route_eval.recommended`) over every question of its
ranking set (:data:`CHAIN_SETS`), with the members that set has no rows for
after it in their present order.

    make chains                                                        # rewrite the file
    uv run --all-extras python benchmarks/providers/chains.py --check  # exit 1 if it is stale

Experimental until 1.0: the file's shape may change.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from frozen import FROZEN_DIR
from route_eval import backend_stats, load_set, noise_floor, recommended

CHAINS_FILE = Path(__file__).resolve().parents[2] / "src" / "web_router" / "data" / "chains.json"
CHAIN_SETS = {"search": "sealqa-hard-254", "answer": "answer-simpleqa-300"}
"""The frozen set that ranks each method's default order.

Search ranks on SealQA hard, not SimpleQA: on SimpleQA every keyed backend
scores 81-95% and today's order already came from it, while SealQA hard is
where they differ; the noise floor is measured on SealQA's own rerun; and a
pooled figure would need every member on both sets, which Keenable and DeepSeek
are not. Answer has one set.
"""
CHAINS_COMMAND = "make chains"
MSG_CHAINS_STALE = "{path} is stale: run `{command}` and commit the result"
MSG_CHAINS_WRITTEN = "wrote {path}"


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 6)


def chain_file(
    floor: float,
    current: Mapping[str, Mapping[str, Sequence[str]]],
    *,
    folder: Path = FROZEN_DIR,
) -> dict[str, Any]:
    """What ``chains.json`` should hold, given its present members: each method re-ranked on its set.

    The members a set ranks come first, in its recommended order over every
    question of the set -- not the held-out half routers are scored on: twice
    the data, and a baseline fit to the held-out half too, so slightly harder
    for a router to beat there, on purpose;
    every other member keeps its present place relative to the rest, in
    ``preferred`` or (search) ``appended``. Membership never changes here: a
    backend joins by hand, and a member the set does not rank stays put.
    """
    out: dict[str, Any] = {
        "generated_by": f"{CHAINS_COMMAND} (benchmarks/providers/chains.py); do not edit by hand",
        "rule": (
            "accuracy over every question of the set, account and quota failures left out; within "
            "the noise floor of the best not yet placed, the cheaper at list price (unknown or $0 never moves a "
            "member up), then the lower p50 latency, then the name"
        ),
        "noise_floor": _round(floor),
    }
    for method, set_name in CHAIN_SETS.items():
        _, questions = load_set(set_name, folder)
        stats = backend_stats(questions)
        parts = {part: list(members) for part, members in current[method].items()}
        members = [p for members in parts.values() for p in members]
        # Ranked among the chain's own members only: a backend outside the chain must not shift its tiers.
        ranked = recommended({p: s for p, s in stats.items() if p in members}, floor)
        entry: dict[str, Any] = {"set": set_name, "questions": len(questions)}
        for i, (part, present) in enumerate(parts.items()):
            entry[part] = (ranked if i == 0 else []) + [p for p in present if p not in ranked]
        entry["unmeasured"] = [p for p in members if p not in stats]
        entry["no_row_served"] = [p for p in members if p in stats and p not in ranked]
        entry["measured"] = {
            p: {
                "accuracy": _round(stats[p]["accuracy_all"]),
                "correct": stats[p]["correct"],
                "questions": stats[p]["questions"],
                "account_failures_left_out": stats[p]["excluded"],
                "cost_usd": _round(stats[p]["cost_usd"]),
                "latency_p50_s": _round(stats[p]["latency_p50"]),
            }
            for p in ranked
        }
        out[method] = entry
    return out


def emit_chains(check: bool, path: Path = CHAINS_FILE) -> int:
    """Rewrite ``chains.json`` from the frozen results, or with ``check`` only say whether it is stale."""
    floor, _ = noise_floor()
    current = json.loads(path.read_text(encoding="utf-8"))
    parts = {"search": ("preferred", "appended"), "answer": ("preferred",)}
    wanted = (
        json.dumps(chain_file(floor, {m: {p: current[m][p] for p in ps} for m, ps in parts.items()}), indent=2) + "\n"
    )
    if check:
        if path.read_text(encoding="utf-8") != wanted:
            print(MSG_CHAINS_STALE.format(path=path, command=CHAINS_COMMAND), file=sys.stderr)
            return 1
        return 0
    path.write_text(wanted, encoding="utf-8")
    print(MSG_CHAINS_WRITTEN.format(path=path))
    return 0


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="write nothing; exit 1 if the file is stale")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    raise SystemExit(emit_chains(args.check))


if __name__ == "__main__":
    main()
