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
Nothing is estimated: accuracy is the judge's grade and
latency is timed around the search; there is no cost column, because no
backend here states one.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from _common import APART, DEFAULT_OUT, MIN_GROUP, Sink, _p, _pct, _s, _wilson, combine_scans, scan_main

from web_router import AsyncWebRouter
from web_router.constants import BENCHMARK_EXCLUDED, EVALS_CSV_SET, PROVIDER_AUTO
from web_router.evals import Dataset, OpenAIJudge
from web_router.providers.base import SearchProvider
from web_router.registry import configured_providers
from web_router.types import SearchDepth, SearchRequest, SearchResponse

MODES_FILE = Path(__file__).resolve().parent / "modes.json"
DEPTH_MARK = "@"
MODE_MARK = ":"
DEPTHS = ("lite", "fast", "deep")
MSG_UNKNOWN_DEPTH = "{setup!r}: depth must be one of {depths}"
MSG_UNKNOWN_MODE = "{setup!r}: no mode {mode!r} for {provider} in modes.json (it has: {known})"
DEFAULT_LIMIT = 300
DEFAULT_MAX_RESULTS = 5
DEFAULT_CONCURRENCY = 8
MSG_OTHER_QUESTIONS = "{folder} asked different questions from the first scan; only scans of the same questions combine"
MSG_CANNOT_COMBINE = (
    "{folder} was a different run ({key}: {theirs!r}, not {ours!r}); only scans of the same questions and judge combine"
)
COMBINE_KEYS = (
    "questions",
    "dataset_sha256",
    "question_column",
    "answer_column",
    "withheld",
    "limit",
    "seed",
    "max_results",
    "judge",
)
"""What two scans must share for their rows to sit in one table: the same questions, search size and judge."""
SEALQA_BREAKDOWNS = [["topic", "By topic"], ["freshness", "By freshness"], ["search_results", "By what search returns"]]
"""SealQA's labels: how fast the answer changes, and whether search turns up conflicting or unhelpful pages."""
QUESTION_SETS: dict[str, dict[str, Any]] = {
    "simpleqa": {
        "dataset": "SimpleQA (openai/simple-evals)",
        "short": "SimpleQA",
        "breakdowns": [["topic", "By topic"], ["answer_type", "By answer type"]],
    },
    "sealqa-0": {
        "dataset": "SealQA Seal-0 (vtllms/sealqa, pinned)",
        "short": "SealQA Seal-0",
        "breakdowns": SEALQA_BREAKDOWNS,
    },
    "sealqa-hard": {
        "dataset": "SealQA Seal-Hard (vtllms/sealqa, pinned)",
        "short": "SealQA Seal-Hard",
        "breakdowns": SEALQA_BREAKDOWNS,
    },
    "csv": {"dataset": "a CSV of your own", "short": "your questions", "breakdowns": [["topic", "By topic"]]},
}
"""``--questions``: what the report calls the set, and the labels it breaks accuracy down by."""
MSG_REPEATED_QUESTION = (
    "{dataset} asks {question!r} more than once; a scan labels each row by its question text, so each must be unique"
)


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
    spec, _, depth = setup.partition(DEPTH_MARK)
    provider, _, mode = spec.partition(MODE_MARK)
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


def _score(rows: list[dict[str, Any]]) -> dict[str, Any]:
    served = [r for r in rows if r["grade"] is not None]
    correct = sum(r["grade"] == "correct" for r in served)
    incorrect = sum(r["grade"] == "incorrect" for r in served)
    latencies = [r["latency"] for r in served]
    return {
        "questions": len(rows),
        "served": len(served),
        "correct": correct,
        "accuracy": correct / len(served) if served else None,
        "accuracy_ci95": _wilson(correct, len(served)),
        "accuracy_when_attempted": correct / (correct + incorrect) if correct + incorrect else None,
        "failed": len(rows) - len(served),
        "withheld": sum(r.get("withheld", 0) for r in rows),
        "latency_p50": statistics.median(latencies) if latencies else None,
        "latency_p95": _pct(latencies, 0.95),
    }


def _breakdown(rows: list[dict[str, Any]], key: str, providers: list[str]) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[row[key] or "untagged"].append(row)
    table = {}
    for group in sorted(groups, key=lambda g: -len(groups[g])):
        members = groups[group]
        scores = {p: _score([r for r in members if r["provider"] == p]) for p in providers}
        questions = len({r["question"] for r in members})
        accuracies = {p: s["accuracy"] for p, s in scores.items() if s["accuracy"] is not None}
        # Ranked on correct answers out of the group's questions, so a backend that
        # failed most of them cannot lead on the few it served.
        most = max((s["correct"] for s in scores.values()), default=0)
        leaders = [p for p, s in scores.items() if s["correct"] == most and most] if questions >= MIN_GROUP else []
        table[group] = {"questions": questions, "accuracy": accuracies, "leaders": leaders}
    return table


def _breakdowns(setup: dict[str, Any]) -> list[list[str]]:
    # A scan from before --questions existed was SimpleQA.
    return setup.get("breakdowns") or QUESTION_SETS["simpleqa"]["breakdowns"]


def summarize(rows: list[dict[str, Any]], setup: dict[str, Any]) -> dict[str, Any]:
    providers = setup["providers"]
    overall = {p: _score([r for r in rows if r["provider"] == p]) for p in providers}
    return {
        "setup": setup,
        "overall": overall,
        **{f"by_{key}": _breakdown(rows, key, providers) for key, _ in _breakdowns(setup)},
    }


def recommended_order(summary: dict[str, Any]) -> list[tuple[str, bool | None]]:
    """The plain backends by correct answers out of every question, each with whether this run shows it behind the best.

    Ranked on correct over all questions asked, a failed search counting as a
    miss, not on accuracy over the searches served: a chain member that fails
    most searches is no member to put first, however well it answered the few
    it served. A ``provider@depth`` or ``provider:mode`` setup is left out: an
    ``auto`` member is a backend, sent whatever depth the request asks for, not
    a mode. So is ``auto`` itself, if a scan named it: as a member it would be a
    second ``auto`` on the default order, reaching unranked backends first. So
    is a backend that served nothing; one that served searches and got every
    answer wrong is ranked, last. A backend is behind only when its 95%
    interval stops short of the best one's; ``None`` marks the best itself.
    """
    overall = summary["overall"]
    plain = [
        p
        for p in summary["setup"]["providers"]
        if DEPTH_MARK not in p and MODE_MARK not in p and p != PROVIDER_AUTO and overall[p]["served"]
    ]
    ranked = sorted(plain, key=lambda p: -_answered(overall[p]))
    if not ranked:
        return []
    floor = _answered_ci(overall[ranked[0]])[0]
    return [(ranked[0], None)] + [(p, _answered_ci(overall[p])[1] < floor) for p in ranked[1:]]


def _answered(score: dict[str, Any]) -> float:
    """Correct answers out of every question asked, a failed search counting as a miss."""
    return score["correct"] / score["questions"] if score["questions"] else 0.0


def _answered_ci(score: dict[str, Any]) -> tuple[float, float]:
    return _wilson(score["correct"], score["questions"]) or (0.0, 0.0)


def _recommendation(summary: dict[str, Any]) -> list[str]:
    order = recommended_order(summary)
    lines = ["## Recommended auto order\n"]
    if not order:
        lines.append("No backend in this scan served a search, so there is no order to suggest.\n")
        return lines
    overall = summary["overall"]
    short = QUESTION_SETS[summary["setup"].get("questions", "simpleqa")]["short"]
    lines.append(
        f"A suggestion from one judge (`{summary['setup']['judge']}`) on {short}, not the library's default: the "
        "plain backends in this scan by correct answers out of every question asked, a failed search counting as a "
        "miss -- so a backend that fails often ranks by what it got right, not by its accuracy on the few it served. "
        "A `provider@depth` or `provider:mode` setup is left out, "
        "because a chain member is a backend -- `auto` sends it whatever depth the request asks for -- not a mode. "
        "A backend is *behind* only when its 95% interval stops short of the best one's; the others are not shown "
        "apart by this run, and another could order them differently.\n"
    )
    lines.append("| Order | Backend | Correct of all questions | 95% CI | Failed | Against the best |")
    lines.append("|---|---|---|---|---|---|")
    for rank, (p, behind) in enumerate(order, 1):
        s = overall[p]
        ci = _answered_ci(s)
        lines.append(
            f"| {rank} | `{p}` | {_p(_answered(s))} ({s['correct']}/{s['questions']}) | {_p(ci[0])}-{_p(ci[1])} "
            f"| {s['failed']} | {APART[behind]} |"
        )
    chain = json.dumps({"auto": {"chain": {"search": [p for p, _ in order]}}})
    lines.append(
        "\nIt is opt-in: to try it, give your own router the order. The chain replaces the default one, keyless "
        "tail included, and each member still needs its key; keyed by `search`, it leaves the other methods' "
        "chains alone.\n"
    )
    lines.append(f"```python\nWebRouter(provider_config={chain})\n```\n")
    lines.append("The library's default order changes only by a pull request backed by a scan like this one.\n")
    return lines


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
    out.extend(_recommendation(summary))
    for label, title in _breakdowns(setup):
        key = f"by_{label}"
        w(f"## {title}\n")
        w(
            f"Accuracy per backend over the searches it served; **bold** marks the most questions answered "
            f"correctly in the row, a failed search counting as a miss. Rows under {MIN_GROUP} questions name no "
            "best -- too few to tell backends apart.\n"
        )
        w("| Group | Questions | " + " | ".join(f"`{p}`" for p in providers) + " |")
        w("|---|---|" + "---|" * len(providers))
        for group, entry in summary[key].items():
            cells = []
            for p in providers:
                cell = _p(entry["accuracy"].get(p))
                cells.append(f"**{cell}**" if p in entry["leaders"] else cell)
            w(f"| {group} | {entry['questions']} | " + " | ".join(cells) + " |")
        w("")
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


def csv_identity(args: argparse.Namespace) -> dict[str, Any]:
    """A CSV run's file digest and columns, for ``run.json``; nothing for a named set."""
    if args.questions != EVALS_CSV_SET or args.dataset is None:
        return {}
    return Dataset.csv_identity(args.dataset, args.question_column, args.answer_column)


def load_questions(args: argparse.Namespace) -> Dataset:
    """``--questions``, sampled by ``--seed``: SimpleQA, a SealQA split, or a CSV of your own."""
    try:
        return Dataset.for_set(
            args.questions,
            path=args.dataset,
            question_column=args.question_column,
            answer_column=args.answer_column,
            limit=args.limit,
            seed=args.seed,
        )
    except ValueError as exc:
        raise SystemExit(f"--questions {args.questions}: {exc} A local file goes in --dataset.") from exc


async def run(args: argparse.Namespace, setup: dict[str, Any], sink: Sink) -> list[dict[str, Any]]:
    dataset = load_questions(args)
    texts = [q.question for q in dataset.questions]
    if len(set(texts)) != len(texts):
        repeated = next(text for text in texts if texts.count(text) > 1)
        raise SystemExit(MSG_REPEATED_QUESTION.format(dataset=setup["dataset"], question=repeated))
    setup["asked"] = len(dataset)
    if dataset.withheld_markers:
        # What was kept from the judge: scans that withheld different pages are not judged alike.
        setup["withheld"] = list(dataset.withheld_markers)
    providers = setup["providers"]
    print(f"{len(providers)} backends x {len(dataset)} questions -> {args.out}: {', '.join(providers)}")
    meta = {q.question: _tags(q.metadata) for q in dataset.questions}
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


def _tags(metadata: dict[str, Any]) -> dict[str, Any]:
    # SimpleQA's own column is named ``metadata``; a local CSV may hold anything there, or nothing.
    inner = metadata.get("metadata", metadata)
    return inner if isinstance(inner, dict) else {}


def _supported(router: AsyncWebRouter, provider: str) -> frozenset[str]:
    return router.get_provider(provider).supported_parameters


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument(
        "--questions", choices=sorted(QUESTION_SETS), default="simpleqa", help="the question set (default: simpleqa)"
    )
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="questions to sample")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--providers", default=None, help="comma-separated (default: every configured backend)")
    parser.add_argument("--max-results", type=int, default=DEFAULT_MAX_RESULTS, help="per search")
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY, help="questions at once")
    parser.add_argument(
        "--dataset", type=Path, default=None, help="a local copy of the set's file (required for --questions csv)"
    )
    parser.add_argument("--question-column", default="problem", help="--questions csv: the question column")
    parser.add_argument("--answer-column", default="answer", help="--questions csv: the gold answer column")
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
        keys=COMBINE_KEYS,
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
    chosen = QUESTION_SETS[args.questions]
    return {
        "questions": args.questions,
        "dataset": chosen["dataset"] if args.questions != "csv" else str(args.dataset),
        "breakdowns": chosen["breakdowns"],
        **csv_identity(args),
        "limit": args.limit,
        "seed": args.seed,
        "providers": providers,
        "max_results": args.max_results,
        "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
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
