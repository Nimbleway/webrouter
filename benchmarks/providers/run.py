"""Every configured search backend on the same questions, judged -- overall and by each label the questions carry.

The agent benchmark next door asks whether an agent does better on web-router;
this asks the question underneath it: which backend is better at what. Each
question is one search per backend through ``evaluate()`` -- the public
``search`` path, then one fixed judge that synthesizes an answer from each
backend's results and grades it. Each question set's labels break accuracy
down -- SimpleQA's topic and answer type, SealQA's topic, freshness and what
search returns for it: the evidence a routing rule or a routing guide should
rest on.

    uv run --all-extras python benchmarks/providers/run.py --limit 300
    uv run --all-extras python benchmarks/providers/run.py --questions sealqa-hard
    uv run --all-extras python benchmarks/providers/run.py --questions csv --dataset mine.csv \
        --question-column question --answer-column answer
    uv run --all-extras python benchmarks/providers/run.py --providers tavily,tavily@fast,tavily:ultra-fast

SealQA withholds results from its own pages (the dataset, the paper, copies)
before judging, so a backend is not scored on finding the answer key; each row
records how many results it withheld.

A setup is a backend (``tavily``), a backend at one of web-router's normalized
depths (``tavily@deep``), or a backend in a vendor mode the normalized surface
does not map (``tavily:ultra-fast``), sent through ``provider_options`` from
``modes.json`` beside this script. Each depth is its own ``evaluate()``, as
the library asks of a comparison of tiers, on the same questions.

Keys come from the repo's ``.env`` (or the environment): each backend's own,
and ``OPENAI_API_KEY`` for the default judge. ``auto`` is left out, as
``evaluate()`` leaves it out, because it would repeat a member under another
name.

Writes into ``--out``: ``run.json`` (the setup), ``rows.jsonl`` (one line per
question and backend) and ``report.md`` with ``summary.json``, the same
numbers for charts. ``--combine`` merges finished scans of the same questions
and judge -- backends added later -- into one report without searching again.
Accuracy is the judge's grade and latency is timed around the search; there
is no cost column, because no backend here states one. The recommended order
alone reads list prices (``web_router.prices``, estimates), to break ties
inside route-eval's noise floor.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from _common import (
    DEFAULT_OUT,
    MIN_GROUP,
    SEARCH_COMBINE_KEYS,
    Sink,
    _p,
    _s,
    combine_scans,
    scan_main,
    split_label,
)
from _questions import (
    MSG_CANNOT_COMBINE,
    MSG_OTHER_QUESTIONS,
    QUESTION_SETS,
    base_setup,
    breakdowns,
    load_checked_questions,
    question_args,
    question_tags,
)
from _recommended import ranking_stats, recommendation
from _recommended import recommended_order as recommended_order
from _report_tables import breakdown, breakdown_tables, search_score

from web_router import AsyncWebRouter
from web_router.constants import BENCHMARK_EXCLUDED
from web_router.evals import OpenAIJudge
from web_router.providers.base import SearchProvider
from web_router.registry import configured_providers
from web_router.types import SearchDepth, SearchRequest, SearchResponse

MODES_FILE = Path(__file__).resolve().parent / "modes.json"
DEPTHS = ("lite", "fast", "deep")
MSG_UNKNOWN_DEPTH = "{setup!r}: depth must be one of {depths}"
MSG_UNKNOWN_MODE = "{setup!r}: no mode {mode!r} for {provider} in modes.json (it has: {known})"
DEFAULT_MAX_RESULTS = 5
DEFAULT_CONCURRENCY = 8


class VendorMode(SearchProvider):
    """A backend in one of its own modes: the mode's ``provider_options`` added to every search, under its own name.

    The search still goes through the backend's public ``asearch`` -- this
    translates nothing -- so validation, wrapping and normalization are the
    backend's own, and the row's name says which mode answered.
    """

    def __init__(self, inner: SearchProvider, options: dict[str, Any]) -> None:
        super().__init__()
        self._inner = inner
        self._options = options

    def _with_options(self, request: SearchRequest) -> SearchRequest:
        return request.model_copy(update={"provider_options": {**request.provider_options, **self._options}})

    def _search(self, request: SearchRequest) -> SearchResponse:
        return self._inner.search(self._with_options(request))

    async def _asearch(self, request: SearchRequest) -> SearchResponse:
        return await self._inner.asearch(self._with_options(request))


def parse_setup(setup: str, modes: dict[str, dict[str, Any]]) -> tuple[str, str | None, dict[str, Any]]:
    """``provider[:mode][@depth]`` as the backend, the normalized depth and the vendor options."""
    provider, mode, depth = split_label(setup)
    if depth and depth not in DEPTHS:
        raise SystemExit(MSG_UNKNOWN_DEPTH.format(setup=setup, depths=", ".join(DEPTHS)))
    options: dict[str, Any] = {}
    if mode:
        known = modes.get(provider, {})
        if mode not in known:
            raise SystemExit(
                MSG_UNKNOWN_MODE.format(setup=setup, mode=mode, provider=provider, known=", ".join(known) or "none")
            )
        options = known[mode]["provider_options"]
    return provider, depth or None, options


def _modes() -> dict[str, dict[str, Any]]:
    if not MODES_FILE.is_file():
        return {}
    return {k: v for k, v in json.loads(MODES_FILE.read_text(encoding="utf-8")).items() if not k.startswith("_")}


def summarize(rows: list[dict[str, Any]], setup: dict[str, Any]) -> dict[str, Any]:
    providers = setup["providers"]
    overall = {p: search_score([r for r in rows if r["provider"] == p]) for p in providers}
    return {
        "setup": setup,
        "overall": overall,
        "ranking": ranking_stats(rows, setup),
        **{f"by_{key}": breakdown(rows, key, providers) for key, _ in breakdowns(setup)},
    }


def markdown(summary: dict[str, Any]) -> str:
    setup, overall = summary["setup"], summary["overall"]
    providers = sorted(setup["providers"], key=lambda p: -(overall[p]["accuracy"] or 0))
    out: list[str] = []
    w = out.append
    short = QUESTION_SETS[setup.get("questions", "simpleqa")]["short"]
    w(f"# Search backends on {short}: accuracy by backend and by question label\n")
    w(
        f"{setup.get('asked', setup['limit'])} {short} questions (seed {setup['seed']}), one search per backend with "
        f"`max_results={setup['max_results']}`, each answer synthesized from that backend's results and graded by "
        f"`{setup['judge']}` with the SimpleQA grader. Accuracy is comparable only with runs graded by the same judge. "
        f"Started {setup['started']}.\n"
    )
    asked = setup.get("asked")
    unfinished = [p for p in providers if asked and overall[p]["questions"] < asked]
    if unfinished:
        # A scan that died: a depth's rows reach the file together, so a backend has every question or none.
        w(
            "**The scan stopped early:** "
            + ", ".join(f"`{p}` has rows for {overall[p]['questions']}" for p in unfinished)
            + f" of the {asked} questions asked. Their numbers are over what the scan reached, not the set.\n"
        )
    w("## Overall\n")
    w("| Backend | Accuracy | 95% CI | Acc. when attempted | Failed | p50 latency | p95 latency |")
    w("|---|---|---|---|---|---|---|")
    for p in providers:
        s = overall[p]
        ci = s["accuracy_ci95"]
        w(
            f"| `{p}` | {_p(s['accuracy'])} ({s['correct']}/{s['served']}) "
            f"| {_p(ci[0]) + '-' + _p(ci[1]) if ci else 'n/a'} | {_p(s['accuracy_when_attempted'])} "
            f"| {s['failed']} | {_s(s['latency_p50'])} | {_s(s['latency_p95'])} |"
        )
    w("")
    withheld = {p: overall[p]["withheld"] for p in providers if overall[p]["withheld"]}
    if withheld:
        w(
            "Results withheld from the judge because their page may hold the set's own answers (the dataset, its "
            "paper or a copy): " + ", ".join(f"`{p}` {n}" for p, n in withheld.items()) + ".\n"
        )
    out.extend(recommendation(summary))
    out.extend(
        breakdown_tables(
            summary,
            breakdowns(setup),
            providers,
            f"Accuracy per backend over the searches it served; **bold** marks the most questions answered "
            f"correctly in the row, a failed search counting as a miss. Rows under {MIN_GROUP} questions name no "
            "best -- too few to tell backends apart.\n",
        )
    )
    w("## How to read this\n")
    w(
        "- **Intervals are wide.** Two backends whose 95% intervals overlap are not shown apart by this run; a topic "
        "row with a few dozen questions is wider still.\n"
        "- **This measures one search, not an agent.** A backend that ranks the right page second still helps an "
        "agent that reads further; here the judge sees only what the one search returned.\n"
        "- **Accuracy is a judgment**, one model's grade against the set's gold answer. Latency is measured around "
        "the search alone."
    )
    return "\n".join(out) + "\n"


async def run(args: argparse.Namespace, setup: dict[str, Any], sink: Sink) -> list[dict[str, Any]]:
    dataset = load_checked_questions(args, setup)
    providers = setup["providers"]
    print(f"{len(providers)} backends x {len(dataset)} questions -> {args.out}: {', '.join(providers)}")
    meta = {q.question: question_tags(q.metadata) for q in dataset.questions}
    modes = _modes()
    by_depth: dict[str | None, list[tuple[str, str, dict[str, Any]]]] = defaultdict(list)
    for label in setup["providers"]:
        provider, depth, options = parse_setup(label, modes)
        by_depth[depth].append((label, provider, options))
    judge = OpenAIJudge()
    labels = [label for label, _ in setup["breakdowns"]]
    rows: list[dict[str, Any]] = []
    try:
        async with AsyncWebRouter() as router:
            # One evaluate() per depth: the library sends one depth to every backend in a comparison.
            for depth, members in by_depth.items():
                wrapped = [
                    type(label, (VendorMode,), {"name": label, "supported_parameters": _supported(router, provider)})(
                        router.get_provider(provider), options
                    )
                    for label, provider, options in members
                ]
                report = await router.evaluate(
                    dataset,
                    providers=wrapped,
                    judge=judge,
                    concurrency=args.concurrency,
                    max_results=args.max_results,
                    search_depth=cast(SearchDepth | None, depth),
                )
                setup["judge"] = report.judge
                # A depth's rows reach the file once its evaluate() returns: a crash in the next depth keeps them.
                batch = [
                    {**row.model_dump(mode="json"), **{tag: meta.get(row.question, {}).get(tag) for tag in labels}}
                    for row in report.rows
                ]
                sink(batch)
                rows += batch
    finally:
        await judge.aclose()
    return rows


def _supported(router: AsyncWebRouter, provider: str) -> frozenset[str]:
    return router.get_provider(provider).supported_parameters


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    question_args(parser)
    parser.add_argument("--providers", default=None, help="comma-separated (default: every configured backend)")
    parser.add_argument("--max-results", type=int, default=DEFAULT_MAX_RESULTS, help="per search")
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY, help="questions at once")
    parser.add_argument("--out", type=Path, default=None, help=f"results folder (default: under {DEFAULT_OUT})")
    parser.add_argument(
        "--combine",
        type=Path,
        nargs="+",
        default=None,
        help="merge finished scans of the same questions and judge into --out, searching nothing",
    )
    return parser.parse_args(argv)


def combine(folders: list[Path]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Rows and setup of several scans as one -- say, backends added later on the same questions."""
    return combine_scans(
        folders,
        keys=SEARCH_COMBINE_KEYS,
        identity=lambda row: (row["question"], row["target"]),
        cannot_combine=MSG_CANNOT_COMBINE,
        other_rows=MSG_OTHER_QUESTIONS,
        # A scan from before --questions existed asked SimpleQA.
        defaults={"questions": "simpleqa"},
    )


def setup_of(args: argparse.Namespace) -> dict[str, Any]:
    """What this scan was asked to do, for ``run.json``: the set, the backends, the search size and when it started."""
    providers = (
        [p.strip() for p in args.providers.split(",") if p.strip()]
        if args.providers
        else [p for p in configured_providers() if p not in BENCHMARK_EXCLUDED]
    )
    return {
        **base_setup(args, providers),
        "max_results": args.max_results,
        "started": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def main(argv: list[str] | None = None) -> None:
    scan_main(
        argv,
        parse_args=parse_args,
        setup_of=setup_of,
        run=run,
        combine=combine,
        summarize=summarize,
        markdown=markdown,
    )


if __name__ == "__main__":
    main()
