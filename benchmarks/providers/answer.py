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

Cost is shown only where the backend states it in its response -- Perplexity's
``usage.cost``, Exa's ``costDollars`` -- summed over the answers that stated
one. Nothing is estimated; list prices are in ``PRICING.md``.

Writes into ``--out`` what ``run.py`` writes: ``run.json``, ``rows.jsonl``,
``summary.json`` and ``report.md``. ``--combine`` merges finished answer scans
of the same questions and judge.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import run as search_scan
from _common import DEFAULT_OUT, MIN_GROUP, Sink, _p, _pct, _s, _wilson, combine_scans, scan_main

from web_router import AsyncWebRouter
from web_router.evals import Dataset, OpenAIJudge
from web_router.exceptions import WebRouterError
from web_router.registry import configured_answer_providers
from web_router.types import AnswerResponse

DEFAULT_LIMIT = search_scan.DEFAULT_LIMIT
DEFAULT_CONCURRENCY = 3
"""Questions at once; every backend is asked each question together, so this many calls reach each backend."""
OUT_PREFIX = "answer-"

STATED_COST_PATHS: tuple[tuple[str, ...], ...] = (
    ("usage", "cost", "total_cost"),
    ("costDollars", "total"),
)
"""Where a backend states what one answer cost, in its own response: Perplexity's, then Exa's."""

STATED_TOKEN_PATHS: dict[str, tuple[str, ...]] = {
    "input_tokens": ("usage", "input_tokens"),
    "output_tokens": ("usage", "output_tokens"),
}
"""Where a backend states the tokens one answer took, for reading its cost against a list price."""

COMBINE_KEYS = ("questions", "dataset_sha256", "question_column", "answer_column", "withheld", "limit", "seed", "judge")
"""What two answer scans must share for their rows to sit in one table."""

BASELINE_KEYS = ("questions", "dataset_sha256", "limit", "seed", "judge")
"""What a search scan must share with this one to stand beside it: the same questions and the same judge."""

MSG_OTHER_QUESTIONS = search_scan.MSG_OTHER_QUESTIONS
MSG_CANNOT_COMBINE = search_scan.MSG_CANNOT_COMBINE
MSG_BASELINE_MISMATCH = (
    "--baseline {folder} was a different run ({key}: {theirs!r}, not {ours!r}); "
    "only a search scan of the same questions and judge stands beside this one"
)
JUDGE_PROBE = ("What is 2 + 2?", "4", "4")
"""One question the judge must grade before any backend is asked, so a judge that cannot run costs no answer."""
MSG_JUDGE_FAILED = "The judge could not grade a test answer, so no backend was asked: {error}"
MSG_NO_PROVIDERS = "No answer backend is configured: set a key for one (see .env.example), or name one with --providers"


def _stated(payload: dict[str, Any], path: tuple[str, ...]) -> Any:
    value: Any = payload
    for key in path:
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return value


def stated_cost(response: AnswerResponse) -> float | None:
    """What the backend said this answer cost, in USD, or ``None`` when it did not say."""
    for payload in response.raw or []:
        for path in STATED_COST_PATHS:
            value = _stated(payload, path)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return float(value)
    return None


def stated_tokens(response: AnswerResponse) -> dict[str, int]:
    """The token counts the backend stated for this answer; empty when it stated none."""
    tokens: dict[str, int] = {}
    for payload in response.raw or []:
        for name, path in STATED_TOKEN_PATHS.items():
            value = _stated(payload, path)
            if isinstance(value, int) and not isinstance(value, bool):
                tokens[name] = value
    return tokens


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
        leaked=any(dataset.withholds(citation.url) for citation in response.citations),
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
    dataset = search_scan.load_questions(args)
    texts = [q.question for q in dataset.questions]
    if len(set(texts)) != len(texts):
        repeated = next(text for text in texts if texts.count(text) > 1)
        raise SystemExit(search_scan.MSG_REPEATED_QUESTION.format(dataset=setup["dataset"], question=repeated))
    setup["asked"] = len(dataset)
    if dataset.withheld_markers:
        setup["withheld"] = list(dataset.withheld_markers)
    providers = setup["providers"]
    print(f"{len(providers)} answer backends x {len(dataset)} questions -> {args.out}: {', '.join(providers)}")
    labels = [label for label, _ in setup["breakdowns"]]
    judge = OpenAIJudge()
    judge.check()
    await check_judge(judge)
    setup["judge"] = judge.label
    gate = asyncio.Semaphore(args.concurrency)
    rows: list[dict[str, Any]] = []

    async def one_question(question: Any) -> None:
        async with gate:
            batch = await asyncio.gather(
                *(answer_row(router, judge, dataset, p, question.question, question.answer) for p in providers)
            )
        tags = search_scan._tags(question.metadata)
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


def _score(rows: list[dict[str, Any]]) -> dict[str, Any]:
    graded = [r for r in rows if r["grade"] is not None]
    leaked = [r for r in rows if r.get("leaked")]
    correct = sum(r["grade"] == "correct" for r in graded)
    incorrect = sum(r["grade"] == "incorrect" for r in graded)
    latencies = [r["latency"] for r in rows if r["failed_at"] != "answer" and r["latency"] is not None]
    costs = [r["cost_usd"] for r in rows if r.get("cost_usd") is not None]
    return {
        "questions": len(rows),
        "served": len(graded),
        "correct": correct,
        "accuracy": correct / len(graded) if graded else None,
        "accuracy_ci95": _wilson(correct, len(graded)),
        "accuracy_when_attempted": correct / (correct + incorrect) if correct + incorrect else None,
        "failed": len(rows) - len(graded) - len(leaked),
        "leaked": len(leaked),
        "latency_p50": statistics.median(latencies) if latencies else None,
        "latency_p95": _pct(latencies, 0.95),
        "citations_mean": statistics.mean(r["citations"] for r in rows) if rows else None,
        "stated_cost_answers": len(costs),
        "stated_cost_total": sum(costs) if costs else None,
        "stated_cost_mean": statistics.mean(costs) if costs else None,
    }


def _baseline(setup: dict[str, Any], asked: set[str]) -> dict[str, Any] | None:
    """The search scan named by ``--baseline``, scored on the questions this scan asked."""
    folder = setup.get("baseline")
    if not folder:
        return None
    theirs = {"questions": "simpleqa", **json.loads((Path(folder) / "run.json").read_text(encoding="utf-8"))}
    ours = {"questions": "simpleqa", **setup}
    for key in BASELINE_KEYS:
        if theirs.get(key) != ours.get(key):
            raise SystemExit(
                MSG_BASELINE_MISMATCH.format(folder=folder, key=key, theirs=theirs.get(key), ours=ours.get(key))
            )
    rows = [
        json.loads(line)
        for line in (Path(folder) / "rows.jsonl").read_text(encoding="utf-8").splitlines()
        if line and json.loads(line)["question"] in asked
    ]
    providers = [p for p in theirs["providers"] if any(r["provider"] == p for r in rows)]
    return {
        "folder": str(folder),
        "max_results": theirs.get("max_results"),
        "overall": {p: search_scan._score([r for r in rows if r["provider"] == p]) for p in providers},
    }


def summarize(rows: list[dict[str, Any]], setup: dict[str, Any]) -> dict[str, Any]:
    providers = setup["providers"]
    graded = [r for r in rows if not r.get("leaked")]
    return {
        "setup": setup,
        "overall": {p: _score([r for r in rows if r["provider"] == p]) for p in providers},
        "baseline": _baseline(setup, {r["question"] for r in rows}),
        **{f"by_{key}": search_scan._breakdown(graded, key, providers) for key, _ in search_scan._breakdowns(setup)},
    }


def _cost(score: dict[str, Any]) -> str:
    if not score["stated_cost_answers"]:
        return "not stated"
    return f"${score['stated_cost_mean']:.4f} ({score['stated_cost_answers']} stated)"


def markdown(summary: dict[str, Any]) -> str:
    setup, overall = summary["setup"], summary["overall"]
    providers = sorted(setup["providers"], key=lambda p: -(overall[p]["accuracy"] or 0))
    short = search_scan.QUESTION_SETS[setup.get("questions", "simpleqa")]["short"]
    out: list[str] = []
    w = out.append
    w(f"# Answer backends on {short}: the answer each backend wrote, graded\n")
    w(
        f"{setup.get('asked', setup['limit'])} {short} questions (seed {setup['seed']}), one `answer()` per backend, "
        f"each backend's own answer graded by `{setup.get('judge', 'n/a')}` with the SimpleQA grader. Nothing here "
        f"writes or edits an answer. Accuracy compares only with runs graded by the same judge. "
        f"Started {setup['started']}.\n"
    )
    w("## Overall\n")
    w(
        "| Backend | Accuracy | 95% CI | Acc. when attempted | Failed | Leaked | p50 latency | p95 latency "
        "| Citations per answer | Stated cost per answer |"
    )
    w("|---|---|---|---|---|---|---|---|---|---|")
    for p in providers:
        s = overall[p]
        ci = s["accuracy_ci95"]
        citations = "n/a" if s["citations_mean"] is None else f"{s['citations_mean']:.1f}"
        w(
            f"| `{p}` | {_p(s['accuracy'])} ({s['correct']}/{s['served']}) "
            f"| {_p(ci[0]) + '-' + _p(ci[1]) if ci else 'n/a'} | {_p(s['accuracy_when_attempted'])} "
            f"| {s['failed']} | {s['leaked']} | {_s(s['latency_p50'])} | {_s(s['latency_p95'])} "
            f"| {citations} | {_cost(s)} |"
        )
    w("")
    if any(overall[p]["leaked"] for p in providers):
        w(
            "*Leaked* rows cite one of the set's own pages (the dataset, its paper or a copy), so their answer may "
            "come from the answer key; they are not graded and not in accuracy.\n"
        )
    baseline = summary.get("baseline")
    if baseline:
        w("## Against search, with the judge writing the answer\n")
        w(
            f"The search scan in `{baseline['folder']}` asked the same questions: one search per backend with "
            f"`max_results={baseline['max_results']}`, then the judge wrote an answer from those results. Both "
            "pipelines are graded by the same judge, so accuracy compares. Latency does not quite: the search "
            "side's is the search alone, without the judge writing its answer, and a caller would pay for both.\n"
        )
        w("| Pipeline | Backend | Accuracy | 95% CI | p50 latency | p95 latency |")
        w("|---|---|---|---|---|---|")
        rows_out = [("answer()", p, overall[p]) for p in providers]
        rows_out += [("search + judge", p, s) for p, s in baseline["overall"].items()]
        for pipeline, p, s in sorted(rows_out, key=lambda item: -(item[2]["accuracy"] or 0)):
            ci = s["accuracy_ci95"]
            w(
                f"| {pipeline} | `{p}` | {_p(s['accuracy'])} ({s['correct']}/{s['served']}) "
                f"| {_p(ci[0]) + '-' + _p(ci[1]) if ci else 'n/a'} | {_s(s['latency_p50'])} | {_s(s['latency_p95'])} |"
            )
        w("")
    for label, title in search_scan._breakdowns(setup):
        w(f"## {title}\n")
        w(
            f"Accuracy per backend over the answers graded; **bold** marks the most answered correctly in the row. "
            f"Rows under {MIN_GROUP} questions name no best.\n"
        )
        w("| Group | Questions | " + " | ".join(f"`{p}`" for p in providers) + " |")
        w("|---|---|" + "---|" * len(providers))
        for group, entry in summary[f"by_{label}"].items():
            cells = []
            for p in providers:
                cell = _p(entry["accuracy"].get(p))
                cells.append(f"**{cell}**" if p in entry["leaders"] else cell)
            w(f"| {group} | {entry['questions']} | " + " | ".join(cells) + " |")
        w("")
    w("## How to read this\n")
    w(
        "- **Intervals are wide.** Two backends whose 95% intervals overlap are not shown apart by this run.\n"
        "- **The answer is the backend's.** It is graded as it came; a backend that hedges or answers at length is "
        "graded on what it said.\n"
        "- **Cost is what the backend stated**, for the answers that stated it. A backend that states none shows "
        "none; read it against the list prices in `PRICING.md`.\n"
        "- **Accuracy is a judgment**, one model's grade against the set's gold answer. Latency is measured around "
        "the `answer()` call alone."
    )
    return "\n".join(out) + "\n"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument(
        "--questions",
        choices=sorted(search_scan.QUESTION_SETS),
        default="simpleqa",
        help="the question set (default: simpleqa)",
    )
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="questions to sample")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--providers", default=None, help="comma-separated (default: every configured answer backend)")
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY, help="questions at once")
    parser.add_argument("--dataset", type=Path, default=None, help="a local copy of the set's file")
    parser.add_argument("--question-column", default="problem", help="--questions csv: the question column")
    parser.add_argument("--answer-column", default="answer", help="--questions csv: the gold answer column")
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
    providers = (
        [p.strip() for p in args.providers.split(",") if p.strip()] if args.providers else configured_answer_providers()
    )
    if not providers:
        raise SystemExit(MSG_NO_PROVIDERS)
    chosen = search_scan.QUESTION_SETS[args.questions]
    return {
        "questions": args.questions,
        "dataset": chosen["dataset"] if args.questions != "csv" else str(args.dataset),
        "breakdowns": chosen["breakdowns"],
        **search_scan.csv_identity(args),
        "limit": args.limit,
        "seed": args.seed,
        "providers": providers,
        **({"baseline": str(args.baseline)} if args.baseline else {}),
        "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


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
