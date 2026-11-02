"""The answer scan's report: the numbers ``summary.json`` holds, and ``report.md`` written from them.

Beside them, the ``--baseline`` search scan, refused unless it asked the same
questions with the same judge, and scored on the questions this scan asked.

Cost is shown only where the backend states it in its response -- Perplexity's
``usage.cost``, Exa's ``costDollars`` -- summed over the answers that stated
one. Nothing is estimated; list prices are in ``PRICING.md``.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any

from _common import MIN_GROUP, _p, _s
from _questions import QUESTION_SETS, breakdowns
from _report_tables import breakdown, breakdown_tables, grade_stats, search_score

from web_router.types_answer import AnswerResponse

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

BASELINE_KEYS = ("questions", "dataset_sha256", "question_column", "answer_column", "limit", "seed", "judge")
"""What a search scan must share with this one to stand beside it: the same questions and the same judge."""

MSG_BASELINE_MISMATCH = (
    "--baseline {folder} was a different run ({key}: {theirs!r}, not {ours!r}); "
    "only a search scan of the same questions and judge stands beside this one"
)


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


def _score(rows: list[dict[str, Any]]) -> dict[str, Any]:
    answered = [r for r in rows if r["failed_at"] != "answer"]
    stats = grade_stats(rows, [r["latency"] for r in answered if r["latency"] is not None])
    leaked = sum(bool(r.get("leaked")) for r in rows)
    costs = [r["cost_usd"] for r in rows if r.get("cost_usd") is not None]
    return {
        **stats,
        "failed": len(rows) - stats["served"] - leaked,
        "leaked": leaked,
        "citations_mean": statistics.mean(r["citations"] for r in answered) if answered else None,
        "stated_cost_answers": len(costs),
        "stated_cost_total": sum(costs) if costs else None,
        "stated_cost_mean": statistics.mean(costs) if costs else None,
    }


def check_baseline(setup: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    """The ``--baseline`` scan's setup, after refusing it unless it shares ``keys`` with this one."""
    folder = setup["baseline"]
    theirs = {"questions": "simpleqa", **json.loads((Path(folder) / "run.json").read_text(encoding="utf-8"))}
    ours = {"questions": "simpleqa", **setup}
    for key in keys:
        if theirs.get(key) != ours.get(key):
            raise SystemExit(
                MSG_BASELINE_MISMATCH.format(folder=folder, key=key, theirs=theirs.get(key), ours=ours.get(key))
            )
    return theirs


def _baseline(setup: dict[str, Any], asked: set[str]) -> dict[str, Any] | None:
    """The search scan named by ``--baseline``, scored on the questions this scan asked."""
    folder = setup.get("baseline")
    if not folder:
        return None
    theirs = check_baseline(setup, BASELINE_KEYS)
    rows = [
        json.loads(line)
        for line in (Path(folder) / "rows.jsonl").read_text(encoding="utf-8").splitlines()
        if line and json.loads(line)["question"] in asked
    ]
    providers = [p for p in theirs["providers"] if any(r["provider"] == p for r in rows)]
    return {
        "folder": str(folder),
        "max_results": theirs.get("max_results"),
        "overall": {p: search_score([r for r in rows if r["provider"] == p]) for p in providers},
    }


def summarize(rows: list[dict[str, Any]], setup: dict[str, Any]) -> dict[str, Any]:
    providers = setup["providers"]
    graded = [r for r in rows if not r.get("leaked")]
    return {
        "setup": setup,
        "overall": {p: _score([r for r in rows if r["provider"] == p]) for p in providers},
        "baseline": _baseline(setup, {r["question"] for r in rows}),
        **{f"by_{key}": breakdown(graded, key, providers) for key, _ in breakdowns(setup)},
    }


def _cost(score: dict[str, Any]) -> str:
    if not score["stated_cost_answers"]:
        return "not stated"
    return f"${score['stated_cost_mean']:.4f} ({score['stated_cost_answers']} stated)"


def _header(setup: dict[str, Any], short: str) -> list[str]:
    return [
        f"# Answer backends on {short}: the answer each backend wrote, graded\n",
        f"{setup.get('asked', setup['limit'])} {short} questions (seed {setup['seed']}), one `answer()` per backend, "
        f"each backend's own answer graded by `{setup.get('judge', 'n/a')}` with the SimpleQA grader. Nothing here "
        f"writes or edits an answer. Accuracy compares only with runs graded by the same judge. "
        f"Started {setup['started']}.\n",
    ]


def _overall_table(overall: dict[str, Any], providers: list[str]) -> list[str]:
    out = [
        "## Overall\n",
        "| Backend | Accuracy | 95% CI | Acc. when attempted | Failed | Leaked | p50 latency | p95 latency "
        "| Citations per answer | Stated cost per answer |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for p in providers:
        s = overall[p]
        ci = s["accuracy_ci95"]
        citations = "n/a" if s["citations_mean"] is None else f"{s['citations_mean']:.1f}"
        out.append(
            f"| `{p}` | {_p(s['accuracy'])} ({s['correct']}/{s['served']}) "
            f"| {_p(ci[0]) + '-' + _p(ci[1]) if ci else 'n/a'} | {_p(s['accuracy_when_attempted'])} "
            f"| {s['failed']} | {s['leaked']} | {_s(s['latency_p50'])} | {_s(s['latency_p95'])} "
            f"| {citations} | {_cost(s)} |"
        )
    out.append("")
    if any(overall[p]["leaked"] for p in providers):
        out.append(
            "*Leaked* rows cite one of the set's own pages (the dataset, its paper or a copy), so their answer may "
            "come from the answer key; they are not graded and not in accuracy.\n"
        )
    return out


def _baseline_table(baseline: dict[str, Any] | None, overall: dict[str, Any], providers: list[str]) -> list[str]:
    if not baseline:
        return []
    out = [
        "## Against search, with the judge writing the answer\n",
        f"The search scan in `{baseline['folder']}` asked the same questions: one search per backend with "
        f"`max_results={baseline['max_results']}`, then the judge wrote an answer from those results. Both "
        "pipelines are graded by the same judge, so accuracy compares. Latency does not quite: the search "
        "side's is the search alone, without the judge writing its answer, and a caller would pay for both.\n",
        "| Pipeline | Backend | Accuracy | 95% CI | p50 latency | p95 latency |",
        "|---|---|---|---|---|---|",
    ]
    rows_out = [("answer()", p, overall[p]) for p in providers]
    rows_out += [("search + judge", p, s) for p, s in baseline["overall"].items()]
    for pipeline, p, s in sorted(rows_out, key=lambda item: -(item[2]["accuracy"] or 0)):
        ci = s["accuracy_ci95"]
        out.append(
            f"| {pipeline} | `{p}` | {_p(s['accuracy'])} ({s['correct']}/{s['served']}) "
            f"| {_p(ci[0]) + '-' + _p(ci[1]) if ci else 'n/a'} | {_s(s['latency_p50'])} | {_s(s['latency_p95'])} |"
        )
    out.append("")
    return out


HOW_TO_READ = (
    "## How to read this\n",
    "- **Intervals are wide.** Two backends whose 95% intervals overlap are not shown apart by this run.\n"
    "- **The answer is the backend's.** It is graded as it came; a backend that hedges or answers at length is "
    "graded on what it said.\n"
    "- **Cost is what the backend stated**, for the answers that stated it. A backend that states none shows "
    "none; read it against the list prices in `PRICING.md`.\n"
    "- **Accuracy is a judgment**, one model's grade against the set's gold answer. Latency is measured around "
    "the `answer()` call alone.",
)


def markdown(summary: dict[str, Any]) -> str:
    setup, overall = summary["setup"], summary["overall"]
    providers = sorted(setup["providers"], key=lambda p: -(overall[p]["accuracy"] or 0))
    short = QUESTION_SETS[setup.get("questions", "simpleqa")]["short"]
    intro = (
        f"Accuracy per backend over the answers graded; **bold** marks the most answered correctly in the row. "
        f"Rows under {MIN_GROUP} questions name no best.\n"
    )
    out = [
        *_header(setup, short),
        *_overall_table(overall, providers),
        *_baseline_table(summary.get("baseline"), overall, providers),
        *breakdown_tables(summary, breakdowns(setup), providers, intro),
        *HOW_TO_READ,
    ]
    return "\n".join(out) + "\n"
