"""Every configured answer backend on the same questions, judged -- beside the search scan on the same questions.

``run.py`` asks which search backend is better at what: one search per backend,
then one fixed judge writes an answer from its results and grades it. This asks
the question beside it: how good is the answer a backend writes itself, from
``answer()``, and how does it compare with that search-then-judge pipeline? Each
question is one ``answer()`` per backend through the public path, graded by the
same judge with the same SimpleQA grader -- nothing synthesized, the backend's
own answer is what is graded.

    uv run --all-extras python benchmarks/providers/answer.py --limit 300
    uv run --all-extras python benchmarks/providers/answer.py --baseline benchmarks/providers/results/simpleqa-300-v2
    uv run --all-extras python benchmarks/providers/answer.py --questions sealqa-hard --providers perplexity

``--baseline`` names a finished search scan of the same questions, seed and
judge. The report then sets the two pipelines side by side: the answer a
backend wrote, and the answer the judge wrote from a search backend's top
results. Both are graded by one judge, so their accuracies compare; their
latencies do not quite -- the search side's is the search alone, without the
judge writing its answer.

SealQA keeps its own pages (the dataset, the paper, copies) from the judge. An
answer backend has read them before it answers, so nothing can be withheld: a
row whose citations include one is marked ``leaked`` and left out of accuracy,
and the report counts them.

Writes into ``--out`` what ``run.py`` writes: ``run.json``, ``rows.jsonl``,
``summary.json`` and ``report.md``. ``--combine`` merges finished answer scans
of the same questions and judge.
"""

from __future__ import annotations

import argparse
import asyncio
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from _common import DEFAULT_OUT, Sink, combine_scans, scan_main
from _questions import (
    MSG_CANNOT_COMBINE,
    MSG_OTHER_QUESTIONS,
    base_setup,
    load_checked_questions,
    question_args,
    question_tags,
)
from answer_report import BASELINE_KEYS, check_baseline, markdown, stated_cost, stated_tokens, summarize

from web_router import AsyncWebRouter
from web_router.evals import Dataset, OpenAIJudge
from web_router.exceptions import WebRouterError
from web_router.registry import configured_answer_providers
from web_router.types_answer import AnswerResponse

DEFAULT_CONCURRENCY = 3
"""Questions at once; every backend is asked each question together, so this many calls reach each backend."""
OUT_PREFIX = "answer-"

COMBINE_KEYS = ("questions", "dataset_sha256", "question_column", "answer_column", "withheld", "limit", "seed", "judge")
"""What two answer scans must share for their rows to sit in one table."""

JUDGE_PROBE = ("What is 2 + 2?", "4", "4")
"""One question the judge must grade before any backend is asked, so a judge that cannot run costs no answer."""
MSG_JUDGE_FAILED = "The judge could not grade a test answer, so no backend was asked: {error}"
MSG_NO_PROVIDERS = "No answer backend is configured: set a key for one (see .env.example), or name one with --providers"


def _backing(response: AnswerResponse) -> list[Any]:
    """The citations the answer rests on: the ones it marks, when it marks any, else every one it lists.

    A backend that lists every page it read beside the few it cites would
    otherwise count as leaked for a page it read and never used.
    """
    marked = [citation for citation in response.citations if citation.field]
    return marked or list(response.citations)


async def answer_row(
    router: AsyncWebRouter, judge: OpenAIJudge, dataset: Dataset, provider: str, question: str, target: str
) -> dict[str, Any]:
    """One backend's answer to one question, graded -- or why it was not.

    Only the ``answer()`` call is timed. A row whose citations include one of
    the set's own pages is ``leaked`` and not graded: its accuracy would count
    finding the answer key.
    """
    row: dict[str, Any] = {
        "provider": provider,
        "question": question,
        "target": target,
        "latency": None,
        "answer": None,
        "grade": None,
        "error": None,
        "failed_at": None,
        "citations": 0,
        "leaked": False,
        "cost_usd": None,
    }
    started = time.perf_counter()
    try:
        response = await router.answer(question, provider=provider)
    except WebRouterError as exc:
        row.update(latency=time.perf_counter() - started, error=str(exc), failed_at="answer")
        return row
    row.update(
        latency=time.perf_counter() - started,
        answer=response.answer,
        citations=len(response.citations),
        leaked=any(dataset.withholds(citation.url) for citation in _backing(response)),
        cost_usd=stated_cost(response),
        **stated_tokens(response),
    )
    if row["leaked"]:
        return row
    try:
        row["grade"] = await judge.agrade(question, target, response.answer or "")
    except Exception as exc:  # the judge's own failure is the row's, never the scan's
        row.update(error=str(exc), failed_at="judge")
    return row


async def check_judge(judge: Any) -> None:
    """Grade one known answer, so an expired key or a missing model stops the scan before anything is billed."""
    try:
        await judge.agrade(*JUDGE_PROBE)
    except Exception as exc:  # whatever the judge's SDK raises, the scan must stop on it
        await judge.aclose()
        raise SystemExit(MSG_JUDGE_FAILED.format(error=exc)) from exc


async def run(args: argparse.Namespace, setup: dict[str, Any], sink: Sink) -> list[dict[str, Any]]:
    dataset = load_checked_questions(args, setup)
    providers = setup["providers"]
    print(f"{len(providers)} answer backends x {len(dataset)} questions -> {args.out}: {', '.join(providers)}")
    labels = [label for label, _ in setup["breakdowns"]]
    judge = OpenAIJudge()
    judge.check()
    await check_judge(judge)
    setup["judge"] = judge.label
    if setup.get("baseline"):
        check_baseline(setup, ("judge",))
    gate = asyncio.Semaphore(args.concurrency)
    rows: list[dict[str, Any]] = []

    async def one_question(question: Any) -> None:
        async with gate:
            batch = await asyncio.gather(
                *(answer_row(router, judge, dataset, p, question.question, question.answer) for p in providers)
            )
        tags = question_tags(question.metadata)
        batch = [{**row, **{label: tags.get(label) for label in labels}} for row in batch]
        # A question's rows reach the file together, so a scan that dies keeps every question it finished.
        sink(batch)
        rows.extend(batch)

    try:
        async with AsyncWebRouter() as router:
            await asyncio.gather(*(one_question(q) for q in dataset.questions))
    finally:
        await judge.aclose()
    return rows


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    question_args(parser)
    parser.add_argument("--providers", default=None, help="comma-separated (default: every configured answer backend)")
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY, help="questions at once")
    parser.add_argument(
        "--baseline", type=Path, default=None, help="a finished search scan of the same questions, to set beside"
    )
    parser.add_argument("--out", type=Path, default=None, help=f"results folder (default: under {DEFAULT_OUT})")
    parser.add_argument(
        "--combine",
        type=Path,
        nargs="+",
        default=None,
        help="merge finished answer scans of the same questions and judge into --out, asking nothing",
    )
    return parser.parse_args(argv)


def setup_of(args: argparse.Namespace) -> dict[str, Any]:
    """What this scan was asked to do, for ``run.json``."""
    named = [p.strip() for p in args.providers.split(",") if p.strip()] if args.providers else None
    # Each backend once: a name repeated would be asked, and paid for, twice per question.
    providers = list(dict.fromkeys(named)) if named else configured_answer_providers()
    if not providers:
        raise SystemExit(MSG_NO_PROVIDERS)
    setup = {
        **base_setup(args, providers),
        **({"baseline": str(args.baseline)} if args.baseline else {}),
        "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if args.baseline:
        # Before anything is paid for; the judge is checked once it is built, still before the first answer.
        check_baseline(setup, tuple(key for key in BASELINE_KEYS if key != "judge"))
    return setup


def combine(folders: list[Path]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Rows and setup of several answer scans as one -- say, backends added later on the same questions."""
    return combine_scans(
        folders,
        keys=COMBINE_KEYS,
        identity=lambda row: (row["question"], row["target"]),
        cannot_combine=MSG_CANNOT_COMBINE,
        other_rows=MSG_OTHER_QUESTIONS,
        defaults={"questions": "simpleqa"},
    )


def main(argv: list[str] | None = None) -> None:
    scan_main(
        argv,
        parse_args=parse_args,
        setup_of=setup_of,
        run=run,
        combine=combine,
        summarize=summarize,
        markdown=markdown,
        out_prefix=OUT_PREFIX,
    )


if __name__ == "__main__":
    main()
