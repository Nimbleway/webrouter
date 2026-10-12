"""Score every usable search backend's accuracy on SimpleQA, or on a CSV of your own, with an LLM judge.

Needs the ``evals`` extra and ``OPENAI_API_KEY`` for the judge, plus a key for
each search backend you want scored::

    make setup   # in your webrouter clone: every extra
    export OPENAI_API_KEY=... NIMBLE_API_KEY=... TAVILY_API_KEY=...
    python examples/evaluate_accuracy.py --limit 20
    python examples/evaluate_accuracy.py --csv questions.csv --question-column question --answer-column expected
    python examples/evaluate_accuracy.py --limit 500 --providers nimble,tavily --search-depth lite \
        --max-results 10 --out rows.csv   # compare vendor tiers: nimble lite against tavily basic

Providers with no key are skipped; with no search key at all it still runs,
against the keyless tier. This one bills: every question is one search per
provider and two judge calls per provider that answered, so start small --
``--limit 20`` -- before ``--limit 500``, the canonical SimpleQA slice.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys

from web_router import WebRouter
from web_router.evals import Dataset, OpenAIJudge


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--csv", help="questions with a header row; SimpleQA is downloaded when omitted")
    parser.add_argument("--question-column", default="problem", help="the CSV column holding the question")
    parser.add_argument("--answer-column", default="answer", help="the CSV column holding the gold answer")
    parser.add_argument("--limit", type=int, default=20, help="questions to sample (default: 20)")
    parser.add_argument("--seed", type=int, default=0, help="sampling seed; 0 is simple-evals' canonical slice")
    parser.add_argument("--providers", help="comma-separated backends; every configured one when omitted")
    parser.add_argument("--model", default="gpt-4o", help="the judge model (default: gpt-4o)")
    parser.add_argument("--max-results", type=int, help="results asked of each backend per question")
    parser.add_argument(
        "--search-depth",
        choices=["lite", "fast", "deep"],
        help="sent to every backend; see README's search-depth table for what each vendor makes of it",
    )
    parser.add_argument("--concurrency", type=int, default=4, help="questions in flight at once (default: 4)")
    parser.add_argument("--out", help="write every row -- grade, latency, answer, error -- to this CSV")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    # Checked before anything runs: evaluate() itself refuses to start without
    # the judge, but a clear sentence beats a traceback for a script.
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit(
            "OPENAI_API_KEY is not set. The judge that synthesizes and grades answers needs it; "
            "no search backend reads it. Set it and run this again."
        )

    if args.csv:
        dataset = Dataset.from_csv(
            args.csv,
            question_column=args.question_column,
            answer_column=args.answer_column,
            limit=args.limit,
            seed=args.seed,
        )
    else:
        dataset = Dataset.simpleqa(limit=args.limit, seed=args.seed)
    providers = [name.strip() for name in args.providers.split(",")] if args.providers else None

    print(f"{dataset.name}: {len(dataset)} questions, judged by {args.model}\n", file=sys.stderr)

    # With `providers` unset, every backend this environment can use is scored
    # -- the ones with a key plus the keyless tier -- less `auto`. A judge
    # passed in is the caller's to close, so it is closed here.
    judge = OpenAIJudge(args.model)
    try:
        with WebRouter() as router:
            report = router.evaluate(
                dataset,
                providers=providers,
                judge=judge,
                concurrency=args.concurrency,
                max_results=args.max_results,
                search_depth=args.search_depth,
            )
    finally:
        judge.close()

    if args.out:
        # Every row, so a surprising score can be read back to its question.
        with open(args.out, "w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["provider", "grade", "failed_at", "latency_ms", "question", "target", "answer", "error"])
            for row in report.rows:
                writer.writerow(
                    [
                        row.provider,
                        row.grade or "",
                        row.failed_at or "",
                        f"{row.latency * 1000:.0f}",
                        row.question,
                        row.target,
                        row.answer or "",
                        str(row.error) if row.error else "",
                    ]
                )
        print(f"rows written to {args.out}", file=sys.stderr)

    print(f"{'provider':<14} {'accuracy':>9} {'attempted':>10} {'failures':>9} {'p50':>8} {'n':>5}")
    for score in report.scores:
        # A backend that never answered has no accuracy to print -- an em dash,
        # never a zero the judge did not give.
        accuracy = f"{score.accuracy:.1%}" if score.accuracy is not None else "—"
        attempted = f"{score.accuracy_when_attempted:.1%}" if score.accuracy_when_attempted is not None else "—"
        p50 = f"{score.latency_p50 * 1000:.0f} ms" if score.latency_p50 is not None else "—"
        print(
            f"{score.provider:<14} {accuracy:>9} {attempted:>10} {score.failure_rate:>9.1%} {p50:>8} "
            f"{score.served:>2}/{score.questions}"
        )

    failed = [row for row in report.rows if row.error is not None]
    if failed:
        print(f"\n{len(failed)} rows failed; the first few:", file=sys.stderr)
        for row in failed[:5]:
            print(f"  {row.provider}  at {row.failed_at}: {str(row.error)[:100]}", file=sys.stderr)


if __name__ == "__main__":
    main()
