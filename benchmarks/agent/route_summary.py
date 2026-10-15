"""Summarize a decision-gate run: accuracy with errors counted wrong, methods chosen, estimated cost, latency.

    uv run python benchmarks/agent/route_summary.py benchmarks/agent/results/<run>

Every dollar figure is an **estimate** from the list prices below, dated, not a
bill: OpenAI reports tokens but no cost, and backends report nothing.
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

from report import _wilson as wilson

# USD, list prices as known on 2026-10-13. Unverified ones are marked; rerun with ±2x before relying on cost.
PRICES = {
    "gpt-5": {"input": 1.25e-6, "cached": 0.125e-6, "output": 10e-6},  # per token, unverified for 2026
    "gpt-5-mini": {"input": 0.25e-6, "cached": 0.025e-6, "output": 2e-6},  # per token, 2025 list price, unverified
    "gpt-6-luna": {
        "input": 0.10e-6,
        "cached": 0.01e-6,
        "output": 0.50e-6,
    },  # per token, third-party trackers 2026-10-11
    "claude-haiku-5-5": {"input": 0.10e-6, "cached": 0.01e-6, "output": 0.50e-6},  # per token, up to 100K prompt
    "openai-web-search": 0.010,  # per call; Anthropic's web_search is the same $10 per 1,000
    "perplexity:search": 0.005,  # Search API, $5 per 1,000 requests, unverified
    "perplexity:research": 0.05,  # Agent API research preset, rough estimate, unverified
    "nimble:search": 0.005,
    "tavily:search": 0.008,
    "nimble:read": 0.001,
    "tavily:read": 0.0016,
    "nimble:research": 0.025,  # effort=low
    "browser-use:browse": 0.08,  # estimate: $0.01 per task + steps
    "jev:classify": 0.00003,  # one Jev decision: ~600 tokens at $0.042/M
    "jina:read": 0.003,  # ~$0.05 per 1M tokens (third-party trackers), a long page ~50K tokens, unverified
    "firecrawl:read": 0.001,  # one credit, unverified
}
FREE_CALLS = ("you-mcp:search", "exa-mcp:search", "parallel-mcp:search", "tavily:search-keyless")
"""Keyless calls: no bill. Every other call missing from PRICES is listed after the table, never silently $0."""


def cost(row: dict) -> float:
    model = row.get("model", "gpt-5")  # rows from before --model existed all ran on gpt-5
    if model not in PRICES:
        raise SystemExit(f"no price for model {model!r}: add it to PRICES in route_summary.py")
    p = PRICES[model]
    uncached = row["input_tokens"] - row["cached_input_tokens"]
    total = uncached * p["input"] + row["cached_input_tokens"] * p["cached"] + row["output_tokens"] * p["output"]
    total += row.get("web_searches", 0) * PRICES["openai-web-search"]
    total += sum(PRICES.get(call, 0.0) for call in row.get("calls", []))
    return total


def _rate(rs: list[dict]) -> str:
    """Correct out of all, with its 95% interval: an error or a timeout has no grade, so it counts as wrong."""
    if not rs:
        return "-"
    ok = sum(r["grade"] == "correct" for r in rs)
    lo, hi = wilson(ok, len(rs))
    return f"{ok}/{len(rs)} [{lo:.2f},{hi:.2f}]"


def main() -> None:
    """Per arm, the parts apart: they are graded differently and picked differently, so no blended number."""
    rows = [json.loads(line) for line in (Path(sys.argv[1]) / "rows.jsonl").read_text().splitlines() if line.strip()]
    by_arm: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_arm[row["arm"]].append(row)
    sources = sorted({r.get("source", "?") for r in rows if r["kind"] != "unclear"})
    heads = [f"clear {s}" for s in sources] + ["unclear asks back"]
    print(
        f"{'arm':<10}"
        + "".join(f"{h:>24}" for h in heads)
        + f" {'key cited':>9} {'errors':>6} {'est $':>7} {'$/clear ok':>10} {'p50 s':>6}  methods"
    )
    for arm, rs in by_arm.items():
        clear = [r for r in rs if r["kind"] != "unclear"]
        cells = [_rate([r for r in clear if r.get("source", "?") == s]) for s in sources]
        cells.append(_rate([r for r in rs if r["kind"] == "unclear"]))
        spend = sum(cost(r) for r in rs)
        # With an unpriced call in the arm the true spend is higher, so both figures are floors.
        p50 = statistics.median(r["latency"] for r in rs)
        floor = ">=" if any(c not in PRICES and c not in FREE_CALLS for r in rs for c in r.get("calls", [])) else ""
        ok = sum(r["grade"] == "correct" for r in clear)
        per = f"{floor}{spend / ok:.3f}" if ok else "n/a"
        methods = Counter(route["method"] for r in rs for route in r["routes"])
        print(
            f"{arm:<10}"
            + "".join(f"{c:>24}" for c in cells)
            + f" {sum(r.get('answer_key_cited', 0) for r in rs):>9} {sum(bool(r['error']) for r in rs):>6}"
            f" {floor + f'{spend:.3f}':>7} {per:>10} {p50:>6.0f}  {dict(methods)}"
        )
    unpriced = Counter(c for r in rows for c in r.get("calls", []) if c not in PRICES and c not in FREE_CALLS)
    if unpriced:
        print(f"\nunpriced calls, counted as $0 above: {dict(unpriced)}")
    print("\nkey cited: answer-key pages a reply or a research run cited (research's are dropped from its sources).")
    print("$/clear ok: the whole arm's estimated spend over its correct clear answers.")


if __name__ == "__main__":
    main()
