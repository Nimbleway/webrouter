"""The default ``auto`` orders, generated: search's keyed members and answer's, ranked by route-eval's rule.

Writes ``src/web_router/data/chains.json``, which ``auto`` reads: each method's
recommended order (:func:`route_eval_rank.recommended`) over every question of its
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
from route_eval_rank import RANKING_RULE, backend_stats, noise_floor, recommended
from route_eval_sets import load_set

CHAINS_FILE = Path(__file__).resolve().parents[2] / "src" / "web_router" / "data" / "chains.json"
CHAIN_SETS = {"search": "sealqa-hard-254", "answer": "answer-simpleqa-300"}
"""The frozen set that ranks each method's default order.

Search ranks on SealQA hard, not SimpleQA: on SimpleQA every keyed backend
scores 81-95% and today's order already came from it, while SealQA hard is
where they differ; the noise floor is measured on SealQA's own rerun; and a
pooled figure would need every member on both sets, which Keenable and DeepSeek
are not. Answer has one set.
"""
CHAIN_PARTS = {"search": ("preferred", "appended"), "answer": ("preferred",)}
"""The lists each method's entry holds, in chain order: the members a set ranks lead the first."""
CHAINS_COMMAND = "make chains"
MSG_CHAINS_STALE = "{path} is stale: run `{command}` and commit the result"
MSG_CHAINS_WRITTEN = "wrote {path}"


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 6)


def _place(parts: Mapping[str, Sequence[str]], ranked: Sequence[str]) -> dict[str, list[str]]:
    """The ranked members lead the first part, in rank; every other member keeps its place in its own part."""
    first, *rest = parts
    placed = {first: [*ranked, *(p for p in parts[first] if p not in ranked)]}
    return placed | {part: [p for p in parts[part] if p not in ranked] for part in rest}


def _measured(stats: Mapping[str, Mapping[str, Any]], ranked: Sequence[str]) -> dict[str, dict[str, Any]]:
    """The numbers each ranked member was placed on, in rank."""
    return {
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


def chain_file(
    floor: float,
    current: Mapping[str, Mapping[str, Sequence[str]]],
    *,
    folder: Path = FROZEN_DIR,
) -> dict[str, Any]:
    """What ``chains.json`` should hold, given its present members: each method ranked on every question of its set.

    Not the held-out half routers are scored on: twice the data, and a
    baseline slightly harder for a router to beat there, on purpose.
    Membership never changes here: a backend joins by hand.
    """
    out: dict[str, Any] = {
        "generated_by": f"{CHAINS_COMMAND} (benchmarks/providers/chains.py); do not edit by hand",
        "rule": RANKING_RULE,
        "noise_floor": _round(floor),
    }
    for method, set_name in CHAIN_SETS.items():
        _, questions = load_set(set_name, folder)
        stats = backend_stats(questions)
        parts = {part: current[method][part] for part in CHAIN_PARTS[method]}
        members = [p for present in parts.values() for p in present]
        # Ranked among the chain's own members only: a backend outside the chain must not shift its tiers.
        ranked = recommended({p: s for p, s in stats.items() if p in members}, floor)
        out[method] = {
            "set": set_name,
            "questions": len(questions),
            **_place(parts, ranked),
            "unmeasured": [p for p in members if p not in stats],
            "no_row_served": [p for p in members if p in stats and p not in ranked],
            "measured": _measured(stats, ranked),
        }
    return out


def emit_chains(check: bool, path: Path = CHAINS_FILE) -> int:
    """Rewrite ``chains.json`` from the frozen results, or with ``check`` only say whether it is stale."""
    floor, _ = noise_floor()
    current = json.loads(path.read_text(encoding="utf-8"))
    wanted = json.dumps(chain_file(floor, current), indent=2) + "\n"
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
