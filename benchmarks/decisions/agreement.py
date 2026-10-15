"""Does the decider label queries the way a person would? Tool, freshness, coverage and scope, against labelled rows.

Rows are JSONL in the dataset-spec v0 shape: ``prompt``, ``asked_on``, and
``labels.tool_suggestion`` / ``recent`` / ``coverage`` / ``well_scoped``. Rows
with neither a tool nor a scope label are skipped. Prints tool accuracy against always answering ``search``, freshness
accuracy, accuracy by confidence, latency over every call (a failure counted
at the decider timeout), and how often a label fell back.

    uv run --all-extras python benchmarks/decisions/agreement.py rows.jsonl [--out results.jsonl]
    uv run --all-extras python benchmarks/decisions/agreement.py scoped   # a set from benchmarks/agent/questions
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dotenv import load_dotenv

from web_router.constants import ROUTE_DECIDER_TIMEOUT, ROUTE_MIN_CONFIDENCE, ROUTE_QUESTIONS
from web_router.decisions import JevDecider
from web_router.decisions.route import label_values
from web_router.exceptions import ProviderError


def wilson(hits: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = hits / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (centre - half, centre + half)


def pct(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(q * len(ordered)) - 1)]


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("rows", help="a JSONL file, or set names from benchmarks/agent/questions/sets.json")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    if Path(args.rows).is_file():
        rows = [json.loads(line) for line in Path(args.rows).read_text().splitlines() if line.strip()]
    else:
        sys.path.insert(0, str(Path(__file__).parents[1] / "agent"))
        from questions import load_set

        rows = load_set(args.rows)
    rows = [
        r for r in rows if (r.get("labels") or {}).get("tool_suggestion") or "well_scoped" in (r.get("labels") or {})
    ]
    decider = JevDecider()

    def label(row: dict) -> dict:
        state = {"query": row["prompt"], "asked_on": row.get("asked_on")}
        started = time.perf_counter()
        try:
            answers = decider.decide(state, ROUTE_QUESTIONS)
            latency, error = (time.perf_counter() - started) * 1000, None
        except ProviderError as exc:
            answers, latency, error = {}, ROUTE_DECIDER_TIMEOUT * 1000, str(exc)
        _, unsure = label_values(answers)
        result = {
            "id": row.get("id"),
            "prompt": row["prompt"],
            "latency_ms": latency,
            "error": error,
            "fell_back": error is not None or unsure,
        }
        scoped = answers.get("well_scoped")
        result["well_scoped"] = {
            "gold": row["labels"].get("well_scoped"),
            "pred": (scoped.choice == "clear") if scoped else None,
            "confidence": scoped.confidence if scoped else 0.0,
        }
        for name, gold_key in (("tool", "tool_suggestion"), ("recent", "recent"), ("coverage", "coverage")):
            answer = answers.get(name)
            result[name] = {
                "gold": row["labels"].get(gold_key),
                "pred": answer.choice if answer else None,
                "confidence": answer.confidence if answer else 0.0,
            }
        return result

    # A few calls at once: fast enough for hundreds of rows, few enough that latency still reads as one call's.
    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(label, rows))
    decider.close()

    scoped_rows = [r for r in results if r["well_scoped"]["gold"] is not None]
    unclear = [r for r in scoped_rows if r["well_scoped"]["gold"] is False]
    clear = [r for r in scoped_rows if r["well_scoped"]["gold"] is True]
    if scoped_rows:
        caught = sum(r["well_scoped"]["pred"] is False for r in unclear)
        flagged = sum(r["well_scoped"]["pred"] is False for r in clear)
        print(f"well_scoped: unclear caught {caught}/{len(unclear)}, clear wrongly flagged {flagged}/{len(clear)}")
        for r in scoped_rows:
            if r["well_scoped"]["pred"] != r["well_scoped"]["gold"]:
                print(
                    f"  scope miss: {r['prompt'][:70]!r} gold_clear={r['well_scoped']['gold']} "
                    f"pred_clear={r['well_scoped']['pred']} ({r['well_scoped']['confidence']:.2f})"
                )
    labelled = results  # --out keeps every labelled row, scope-only ones included
    results = [r for r in results if r["tool"]["gold"]]
    if not results:
        _write(args.out, labelled)
        return
    n = len(results)
    tool_hits = sum(r["tool"]["pred"] == r["tool"]["gold"] for r in results)
    base_hits = sum(r["tool"]["gold"] == "search" for r in results)
    recent_rows = [r for r in results if r["recent"]["gold"]]
    recent_hits = sum(r["recent"]["pred"] == r["recent"]["gold"] for r in recent_rows)
    lo, hi = wilson(tool_hits, n)
    latencies = [r["latency_ms"] for r in results]
    fell_back = sum(r["fell_back"] for r in results)

    print(f"rows: {n}  (decider errors: {sum(r['error'] is not None for r in results)})")
    print(f"tool accuracy:     {tool_hits}/{n} = {tool_hits / n:.2f}  95% CI [{lo:.2f}, {hi:.2f}]")
    print(f"always 'search':   {base_hits}/{n} = {base_hits / n:.2f}")
    print(f"recent accuracy:   {_ratio(recent_hits, len(recent_rows))}")
    cov_rows = [r for r in results if r["coverage"]["gold"]]
    cov_hits = sum(r["coverage"]["pred"] == r["coverage"]["gold"] for r in cov_rows)
    print(f"coverage accuracy: {_ratio(cov_hits, len(cov_rows))}")
    confident = [r for r in results if r["tool"]["confidence"] >= ROUTE_MIN_CONFIDENCE]
    print(
        f"tool, confident (>= {ROUTE_MIN_CONFIDENCE}): "
        f"{sum(r['tool']['pred'] == r['tool']['gold'] for r in confident)}/{len(confident)}"
    )
    print(f"fallback rate:     {fell_back}/{n} = {fell_back / n:.2f}")
    print(f"latency ms:        p50 {statistics.median(latencies):.0f}  p95 {pct(latencies, 0.95):.0f}")
    confusion = Counter((r["tool"]["gold"], r["tool"]["pred"]) for r in results)
    print("tool confusion (gold -> pred):")
    for (gold, pred), count in sorted(confusion.items(), key=lambda item: (item[0][0], str(item[0][1]))):
        print(f"  {gold:>9} -> {pred!s:<9} {count}{'' if gold == pred else '   <-- miss'}")
    for r in results:
        if r["tool"]["pred"] != r["tool"]["gold"]:
            print(
                f"  miss: {r['prompt'][:80]!r} gold={r['tool']['gold']} "
                f"pred={r['tool']['pred']} ({r['tool']['confidence']:.2f})"
            )
    _write(args.out, labelled)


def _ratio(hits: int, n: int) -> str:
    """Hits over rows, or n/a when no row carries that gold label (our sets label tool and scope only)."""
    return f"{hits}/{n} = {hits / n:.2f}" if n else "n/a (no gold labels in these rows)"


def _write(path: Path | None, rows: list[dict]) -> None:
    if path:
        path.write_text("".join(json.dumps(r) + "\n" for r in rows))


if __name__ == "__main__":
    main()
