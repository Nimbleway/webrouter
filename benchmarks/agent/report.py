"""Turn an agent benchmark's rows into ``summary.json`` and an explained ``report.md``.

``run.py`` calls this when a run ends. It also runs on its own, to rebuild the
report from a finished run without paying for it again:

    uv run python benchmarks/agent/report.py benchmarks/agent/results/<run>

``--combine`` merges runs made separately -- a Claude run and an OpenAI run on
the same questions, say -- into one folder and one report, as if they had been
one run of both platforms. It refuses runs whose questions, repeats, search
size or judge differ, because their figures would not compare:

    uv run python benchmarks/agent/report.py --combine <claude-run> <openai-run> --out <folder>

Every figure is computed from ``rows.jsonl``. Accuracy is the judge's grade;
everything else is counted from the agent's messages or timed around the run.
Nothing is estimated: the one cost shown is the list cost Anthropic reports for
a Claude session, and an OpenAI run, which reports none, shows none.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
from collections import Counter, defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

import page  # benchmarks/agent/page.py, beside this module

CLAUDE, OPENAI = "claude", "openai"
PLATFORMS = (CLAUDE, OPENAI)
NATIVE = {CLAUDE: "claude-native", OPENAI: "openai-native"}
BASELINES = tuple(NATIVE.values())
PLATFORM_SEPARATOR = "/"
BOTH = "both"
"""The key of :data:`PLATFORM_TEXT`'s entry for a report of both platforms."""
NO_GUIDE = ":no-guide"
ROWS_FILE, RUN_FILE = "rows.jsonl", "run.json"
MATCHED_SETUP = (
    "questions",
    "dataset_sha256",
    "question_column",
    "answer_column",
    "withheld",
    "limit",
    "seed",
    "repeats",
    "max_results",
    "judge",
    "outage",
)
"""What runs must share for ``--combine`` to merge them: the same questions, graded the same way."""
MATCHED_PLATFORM_SETUP = ("model", "surface")
MSG_COMBINE_MISMATCH = "cannot combine: {key} differs ({values}) -- these runs' figures would not compare"
MSG_COMBINE_QUESTIONS = "cannot combine: the runs asked different questions (question {index} differs)"
MSG_COMBINE_GOLD = "cannot combine: the runs graded question {index} against different answers"
MSG_COMBINE_INCOMPLETE = (
    "cannot combine: {arm!r} in {folder} has {got} of the {expected} runs its setup asks for "
    "({questions} questions x {repeats} repeats) -- an unfinished run would be compared as if it were whole"
)
MSG_COMBINE_ARM = "cannot combine: arm {arm!r} is in more than one run"
MSG_COMBINE_PLATFORM = "cannot combine: both runs have {platform} agents on a different {key} ({values})"
MSG_COMBINE_OUT = "{path} already exists; pass a new --out folder"
MSG_SELECT_UNKNOWN = "cannot keep {arm!r}: the combined runs have {known}"

NO_NATIVE = "— (no native arm in this run)"
WEB_ROUTER = "web-router:"
MCP = "mcp:"
DIRECT = "direct:"
SHUFFLED = ":shuffled"
NO_SEARCH = "no-search"
MCP_TOOLS_WITH_READ = 2
"""A server recorded with a search and a page-read tool; one with a single tool searches only."""
TOOL_ERROR_ALERT_SHARE = 0.5
"""At least this share of an arm's tool calls erroring, and its figures describe a failing tool."""
MSG_ALERT_NO_SEARCH = "no search succeeded in any of its {runs} runs ({errors} tool errors)"
MSG_ALERT_ERRORS = "{share:.0%} of its {calls} tool calls failed"
FAILURE_ALERT_SHARE = 0.2
"""At least this share of an arm's runs ending without an answer, and its figures describe an outage, not the arm."""
MSG_ALERT_FAILURES = "{failed} of its {runs} runs ended without an answer ({share:.0%})"
FAILED_AT_JUDGE = "judge"
"""A row's ``failed_at`` when the agent answered and grading it failed -- the judge's outage, not the arm's."""
BOOTSTRAP_RESAMPLES = 1000
BOOTSTRAP_SEED = 0
"""Fixed, so rebuilding a report from the same rows prints the same verdicts."""
MIN_PAIRED = 5
"""Fewer distinct questions served by both arms than this, and a margin gets no verdict at all."""
CLEAR, NOISE, TOO_FEW, TIED = "clear", "within noise", "too few runs", "tied"
REASONING_SEPARATOR = " | "
Z_95 = 1.96
QUESTION_CHARS = 90
PIPE_ESCAPED = "\\|"

GRADE_MARK = {"correct": "✅ correct", "incorrect": "❌ incorrect", "not_attempted": "⚪ not attempted"}

GLOSSARY = [
    (
        "accuracy",
        "Share of served runs the judge graded correct: correct / served. A run whose answer "
        'did not commit to one ("not attempted") counts as a miss.',
    ),
    (
        "95% CI",
        "Wilson interval around accuracy. With few questions it is wide: two arms whose intervals "
        "overlap are not shown apart by this run.",
    ),
    ("acc. when attempted", "correct / (correct + incorrect): accuracy over the runs that committed to an answer."),
    (
        "failure rate",
        "Runs that produced no gradable answer -- the agent raised, hit the step limit, ran past "
        "the timeout, or the judge call failed. Left out of accuracy.",
    ),
    ("tool calls", "Web tool calls per run: how many searches (and page reads) the agent made."),
    ("model calls", "LLM turns per run. Each one resends the conversation so far, tool results included."),
    (
        "input tokens",
        "Tokens the model read over the whole run, as the model provider reported them -- the "
        "prompt, every tool result, and the tools' own schemas, which are sent on every turn.",
    ),
    ("output tokens", "Tokens the model wrote over the whole run."),
    (
        "tool chars",
        "Characters of tool output put into the conversation: how much text the web tool fed the model. "
        "n/a where the platform keeps it hidden, as OpenAI's built-in web_search does.",
    ),
    ("latency", "Wall-clock seconds for one whole agent run, model and tools together. p50 is the median."),
    (
        "built-in web searches",
        "Searches the platform's own web search tool made -- from the session's usage on Claude, from the "
        "response's web_search_call items on OpenAI. The platform bills these per search; web-router's searches "
        "are billed by their own backend instead.",
    ),
]

# What differs between the two platforms' reports: who the agent is, what its
# built-in tools are, and whether the platform states a cost.
PLATFORM_TEXT: dict[str, dict[str, str]] = {
    CLAUDE: {
        "agent": "Claude",
        "builtin": "Claude's built-in `web_search` and `web_fetch`",
        "native": "Claude's built-in `web_search` and `web_fetch`, run by Anthropic (baseline)",
        "cost": "Anthropic list cost",
        "cost_meaning": (
            "What Anthropic reports the session cost at public list rates: model tokens, Claude's web searches "
            "and session runtime. It does not include a web-router backend's own charges, which no backend here "
            "states -- so it is the Anthropic side of the bill, not the whole of it."
        ),
        "cost_note": (
            "- **The list cost is Anthropic's side only.** It includes Claude's own web searches for the baseline "
            "but not a web-router backend's charges for its searches, which no backend here states. Compare it "
            "knowing that the web-router arms have a second bill."
        ),
        "model_calls": "LLM turns per run. Each one resends the conversation so far, tool results included.",
    },
    OPENAI: {
        "agent": "OpenAI",
        "builtin": "OpenAI's built-in `web_search`",
        "native": "OpenAI's built-in `web_search`, run by OpenAI (baseline)",
        "cost": "List cost",
        "cost_meaning": (
            "n/a throughout: OpenAI reports tokens but no cost, and this report never estimates one. Price the "
            "token and search counts at OpenAI's published rates to compare cost."
        ),
        "cost_note": (
            "- **No cost is shown.** OpenAI reports tokens and its own web searches, not what they cost, and this "
            "report states only what a provider reported. The token and search columns are what to price."
        ),
        "model_calls": (
            "Responses API requests per run. OpenAI's built-in web_search and an `mcp:` arm's MCP tool run their "
            "own turns inside one request, so those arms' counts are lower than the turns they took."
        ),
    },
}

PLATFORM_TEXT[BOTH] = {
    "agent": "Claude and OpenAI",
    "builtin": "the platform's built-in web search",
    "native": "",
    "cost": PLATFORM_TEXT[CLAUDE]["cost"],
    "cost_meaning": (
        PLATFORM_TEXT[CLAUDE]["cost_meaning"] + " OpenAI rows show n/a: OpenAI reports tokens but no cost, and this "
        "report never estimates one."
    ),
    "cost_note": PLATFORM_TEXT[CLAUDE]["cost_note"] + "\n" + PLATFORM_TEXT[OPENAI]["cost_note"],
    "model_calls": (
        "LLM turns per run: on Claude, each one resends the conversation so far; on OpenAI, Responses API "
        "requests, where the built-in web_search and an `mcp:` arm's MCP tool run their own turns inside one "
        "request."
    ),
}

AXES: list[dict[str, Any]] = [
    {"key": "accuracy", "label": "Accuracy", "better": "higher", "unit": "pp", "field": "correct", "stat": "mean"},
    {
        "key": "failure_rate",
        "label": "Failure rate",
        "better": "lower",
        "unit": "pp",
        "field": "failed",
        "stat": "mean",
        "all_runs": True,
    },
    {
        "key": "input_tokens_p50",
        "label": "Input tokens (median)",
        "better": "lower",
        "unit": "pct",
        "field": "input_tokens",
        "stat": "median",
    },
    {
        "key": "tool_chars_p50",
        "label": "Tool chars (median)",
        "better": "lower",
        "unit": "pct",
        "field": "tool_chars",
        "stat": "median",
    },
    {
        "key": "tool_calls",
        "label": "Tool calls",
        "better": "lower",
        "unit": "abs",
        "field": "tool_calls",
        "stat": "mean",
    },
    {
        "key": "latency_p50",
        "label": "p50 latency",
        "better": "lower",
        "unit": "s",
        "field": "latency",
        "stat": "median",
    },
    {"key": "latency_p95", "label": "p95 latency", "better": "lower", "unit": "s", "field": "latency", "stat": "p95"},
    {
        "key": "list_cost_p50",
        "label": "Anthropic list cost (median)",
        "better": "lower",
        "unit": "pct",
        "field": "list_cost_usd",
        "stat": "median",
    },
]
"""The axes the winners are called on: the summary field ranked, which way is better, and how a margin reads.

``field`` and ``stat`` recompute the figure from rows, for the bootstrap. An arm
with no figure on an axis -- OpenAI's native tool chars, any OpenAI cost -- is
left out of that axis rather than ranked on a zero.
"""


def build(rows: list[dict[str, Any]], setup: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """The machine-readable summary and the markdown report, from the same numbers."""
    arms = setup.get("arms") or list(dict.fromkeys(r["arm"] for r in rows))
    platforms = platforms_of(setup)
    arm_platform = {arm: _platform_of(arm, platforms) for arm in arms}
    per_arm = {arm: {**_arm(arm, [r for r in rows if r["arm"] == arm]), "platform": arm_platform[arm]} for arm in arms}
    baselines = {
        platform: next((a for a in mine if a in BASELINES), mine[0])
        for platform in platforms
        if (mine := [a for a in arms if arm_platform[a] == platform])
    }
    own = {arm: baselines[arm_platform[arm]] for arm in arms}
    # The no-search arm is its platform's floor: what the model knew without any web tool.
    no_search = {arm_platform[arm]: arm for arm in arms if _spec(arm) == NO_SEARCH}
    challengers = [arm for arm in arms if arm not in baselines.values()]
    summary = {
        "setup": setup,
        "platforms": platforms,
        "baseline": next(iter(baselines.values())),
        "baselines": baselines,
        "arms": per_arm,
        "vs_baseline": {arm: {**_versus(per_arm[own[arm]], per_arm[arm]), "baseline": own[arm]} for arm in challengers},
        "head_to_head": {arm: _head_to_head(rows, own[arm], arm) for arm in challengers},
        "vs_no_search": {
            arm: {
                **_versus(per_arm[none], per_arm[arm]),
                **_per_correct(per_arm[none], per_arm[arm]),
                "no_search": none,
            }
            for arm in arms
            if (none := no_search.get(arm_platform[arm])) and arm != none
        },
        "guide_effect": {
            arm: _versus(per_arm[arm + NO_GUIDE], per_arm[arm])
            for arm in arms
            if not arm.endswith(NO_GUIDE) and arm + NO_GUIDE in per_arm
        },
        "by_reasoning_type": _by_reasoning(rows, arms),
        "questions": _questions(rows, arms),
        "failures": [
            {k: r[k] for k in ("arm", "index", "repeat", "failed_at", "error")} for r in rows if r.get("error")
        ],
        "alerts": (alerts := tool_alerts(per_arm)),
        # A broken arm's figures are what a failing tool produced, not what the backend does: never a winner.
        "headline": _headlines(_without(per_arm, alerts), baselines),
        "winners": _winners(rows, _without(per_arm, alerts), baselines),
    }
    return summary, _markdown(summary)


def tool_alerts(per_arm: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """The arms whose runs or tools mostly failed: their figures measured an outage, not a backend.

    Any arm is flagged when at least :data:`FAILURE_ALERT_SHARE` of its runs
    ended without an answer -- a vendor's server that cannot be reached fails
    the whole request on OpenAI, so no tool call is ever counted. A run the
    agent answered and the judge failed to grade is the judge's outage, not the
    arm's, and does not count. A web-router
    or MCP arm is also flagged when no search succeeded in any run, or when at
    least :data:`TOOL_ERROR_ALERT_SHARE` of its tool calls came back as errors;
    a native arm's tools run on the platform's side, so only its runs are checked.
    """
    alerts = []
    for arm, a in per_arm.items():
        calls, errors = a.get("tool_calls_total") or 0, a.get("tool_errors_total") or 0
        share = errors / calls if calls else None
        unanswered = a["unanswered"]
        if a["runs"] and unanswered / a["runs"] >= FAILURE_ALERT_SHARE:
            reason = MSG_ALERT_FAILURES.format(failed=unanswered, runs=a["runs"], share=unanswered / a["runs"])
        elif arm in BASELINES or not _spec(arm).startswith((WEB_ROUTER, MCP, DIRECT)):
            continue
        elif a.get("searches_total") == 0 and calls:
            reason = MSG_ALERT_NO_SEARCH.format(runs=a["runs"], errors=errors)
        elif share is not None and share >= TOOL_ERROR_ALERT_SHARE:
            reason = MSG_ALERT_ERRORS.format(share=share, calls=calls)
        else:
            continue
        alerts.append({"arm": arm, "reason": reason, "tool_error_share": share})
    return alerts


def _without(per_arm: dict[str, dict[str, Any]], alerts: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    broken = {alert["arm"] for alert in alerts}
    return {arm: a for arm, a in per_arm.items() if arm not in broken}


def platforms_of(setup: dict[str, Any]) -> list[str]:
    """The platforms a run's agents ran on. A run from before runs of both names one, or none for Claude."""
    return list(setup.get("platforms") or [setup.get("platform", CLAUDE)])


def platform_setup(setup: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Model, surface and limits per platform, from either shape of ``run.json``."""
    if "platform_setup" in setup:
        return dict(setup["platform_setup"])
    keys = ("model", "surface", "timeout", "max_turns", "session_budget_usd")
    return {platforms_of(setup)[0]: {k: setup[k] for k in keys if k in setup}}


def own_family(setup: dict[str, Any]) -> list[str]:
    """The platforms whose agents the judge shares a model family with -- a list in a run of both, a flag before."""
    flag = setup.get("judge_same_family")
    if isinstance(flag, list):
        return flag
    return platforms_of(setup)[:1] if flag else []


def _platform_of(arm: str, platforms: list[str]) -> str:
    native = next((p for p, name in NATIVE.items() if name == arm), None)
    prefix, sep, _ = arm.partition(PLATFORM_SEPARATOR)
    if native:
        return native
    return prefix if sep and prefix in PLATFORMS else platforms[0]


def _spec(arm: str) -> str:
    """The arm without the platform a run of both puts in front of it."""
    prefix, sep, rest = arm.partition(PLATFORM_SEPARATOR)
    return rest if sep and prefix in PLATFORMS else arm


def _arm(arm: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    served = [r for r in rows if not r.get("error")]
    grades = Counter(r.get("grade") for r in served)
    correct, incorrect = grades["correct"], grades["incorrect"]
    latencies = sorted(r["latency"] for r in served)
    low, high = _wilson(correct, len(served))
    providers = Counter(p for r in rows for p in r.get("providers", []))
    return {
        "arm": arm,
        "runs": len(rows),
        "served": len(served),
        "failed": len(rows) - len(served),
        "unanswered": sum(1 for r in rows if r.get("error") and r.get("failed_at") != FAILED_AT_JUDGE),
        "correct": correct,
        "incorrect": incorrect,
        "not_attempted": grades["not_attempted"],
        "accuracy": correct / len(served) if served else None,
        "accuracy_ci95": [low, high] if served else None,
        "accuracy_when_attempted": correct / (correct + incorrect) if correct + incorrect else None,
        "accuracy_by_repeat": _by_repeat(served),
        "failure_rate": (len(rows) - len(served)) / len(rows) if rows else None,
        "tool_calls": _mean(served, "tool_calls"),
        "tool_errors": _mean(served, "tool_errors"),
        "model_calls": _mean(served, "model_calls"),
        "input_tokens": _mean(served, "input_tokens"),
        "input_tokens_p50": _median(served, "input_tokens"),
        "input_tokens_max": max((r["input_tokens"] for r in served), default=None),
        "output_tokens": _mean(served, "output_tokens"),
        "tool_chars": _mean_known(served, "tool_chars"),
        "tool_chars_p50": _median_known(served, "tool_chars"),
        "web_searches": _mean(served, "web_searches"),
        "list_cost_p50": _median_known(served, "list_cost_usd"),
        "list_cost_total": _total_known(rows, "list_cost_usd"),
        "latency_mean": statistics.mean(latencies) if latencies else None,
        "latency_p50": statistics.median(latencies) if latencies else None,
        "latency_p95": _pct(latencies, 0.95),
        "served_by": dict(providers.most_common()),
        "searches_total": _total_known(rows, "searches"),
        "tool_calls_total": sum(r.get("tool_calls", 0) for r in rows),
        "tool_errors_total": sum(r.get("tool_errors", 0) for r in rows),
        "extract_urls_total": _total_known(rows, "extract_urls"),
        "withheld_total": sum(r.get("withheld", 0) for r in rows),
        "answer_key_seen_runs": sum(1 for r in rows if r.get("answer_key_seen")),
        "answer_key_seen_correct": sum(1 for r in rows if r.get("answer_key_seen") and r.get("grade") == "correct"),
    }


def _versus(base: dict[str, Any], arm: dict[str, Any]) -> dict[str, Any]:
    def diff(key: str) -> float | None:
        return None if base[key] is None or arm[key] is None else arm[key] - base[key]

    def ratio(key: str) -> float | None:
        return None if not base[key] or arm[key] is None else arm[key] / base[key] - 1

    return {
        "accuracy_pp": None if diff("accuracy") is None else diff("accuracy") * 100,  # type: ignore[operator]
        "input_tokens_p50_pct": ratio("input_tokens_p50"),
        "tool_chars_p50_pct": ratio("tool_chars_p50"),
        "list_cost_p50_pct": ratio("list_cost_p50"),
        "tool_calls_diff": diff("tool_calls"),
        "latency_p50_diff": diff("latency_p50"),
    }


def _per_correct(base: dict[str, Any], arm: dict[str, Any]) -> dict[str, Any]:
    """Anthropic list cost per correct answer, an arm's and the base's -- ``None`` where the platform states no cost."""

    def per(a: dict[str, Any]) -> float | None:
        return a["list_cost_total"] / a["correct"] if a.get("list_cost_total") is not None and a["correct"] else None

    return {"list_cost_per_correct": per(arm), "base_list_cost_per_correct": per(base)}


def _head_to_head(rows: list[dict[str, Any]], base: str, arm: str) -> dict[str, int]:
    """Per question, over the repeats both arms served: which arm was right more often."""
    right: dict[tuple[str, int], list[bool]] = defaultdict(list)
    for r in rows:
        if r["arm"] in (base, arm) and not r.get("error"):
            right[(r["arm"], r["index"])].append(r.get("grade") == "correct")
    counts = Counter()
    for index in sorted({i for (_, i) in right}):
        if (base, index) not in right or (arm, index) not in right:
            continue
        a = statistics.mean(right[(base, index)])
        b = statistics.mean(right[(arm, index)])
        both_right = a == b == 1
        both_wrong = a == b == 0
        key = (
            "both_right"
            if both_right
            else "both_wrong"
            if both_wrong
            else "arm_better"
            if b > a
            else ("baseline_better" if a > b else "tie")
        )
        counts[key] += 1
    return {k: counts[k] for k in ("both_right", "arm_better", "baseline_better", "both_wrong", "tie")}


def _by_reasoning(rows: list[dict[str, Any]], arms: list[str]) -> dict[str, dict[str, Any]]:
    """Accuracy per question category: FRAMES' reasoning types, SimpleQA's topic. One in several counts in each."""
    table: dict[str, dict[str, list[bool]]] = defaultdict(lambda: defaultdict(list))
    # Distinct questions, not runs: with repeats one question is several rows.
    questions: dict[str, set[int]] = defaultdict(set)
    for r in rows:
        if r.get("error"):
            continue
        for kind in (r.get("reasoning_types") or "untagged").split(REASONING_SEPARATOR):
            table[kind.strip()][r["arm"]].append(r.get("grade") == "correct")
            questions[kind.strip()].add(r["index"])
    result = {}
    for kind in sorted(table, key=lambda k: -len(questions[k])):
        counts = {arm: table[kind][arm] for arm in arms}
        result[kind] = {
            "questions": len(questions[kind]),
            "accuracy": {arm: (statistics.mean(v) if v else None) for arm, v in counts.items()},
        }
    return result


def _questions(rows: list[dict[str, Any]], arms: list[str]) -> list[dict[str, Any]]:
    by_index: dict[int, dict[str, Any]] = {}
    for r in sorted(rows, key=lambda r: (r["index"], r["repeat"])):
        entry = by_index.setdefault(
            r["index"],
            {
                "index": r["index"],
                "question": r["question"],
                "gold": r["gold"],
                "reasoning_types": r.get("reasoning_types"),
                "arms": {},
            },
        )
        entry["arms"].setdefault(r["arm"], []).append(
            {"repeat": r["repeat"], "grade": r.get("grade") or r.get("failed_at"), "answer": r.get("answer")}
        )
    return [by_index[i] for i in sorted(by_index)]


def _headlines(per_arm: dict[str, dict[str, Any]], baselines: dict[str, str]) -> list[str]:
    """One set of statements per platform, each read against that platform's own baseline."""
    if len(baselines) == 1:
        return _headline(per_arm, next(iter(baselines.values())))
    lines = []
    for platform, baseline in baselines.items():
        mine = {arm: a for arm, a in per_arm.items() if a["platform"] == platform}
        agent = PLATFORM_TEXT[platform]["agent"]
        lines += [f"**{agent}:** {line}" for line in _headline(mine, baseline)]
    best = {
        platform: _spec(max(scored, key=lambda a: a["accuracy"])["arm"])
        for platform in baselines
        if (
            scored := [
                a
                for a in per_arm.values()
                # Only web-router's own arms: an mcp: or direct: arm is the vendor without it.
                if a["platform"] == platform and a["accuracy"] is not None and _spec(a["arm"]).startswith(WEB_ROUTER)
            ]
        )
    }
    if len(best) > 1:
        names = [f"`{spec}` on {PLATFORM_TEXT[p]['agent']}" for p, spec in best.items()]
        same = len(set(best.values())) == 1
        lines.append(
            "Most accurate web-router arm: "
            + " and ".join(names)
            + (" -- the same on both platforms." if same else " -- a different one on each platform.")
        )
    return lines


def _headline(per_arm: dict[str, dict[str, Any]], baseline: str) -> list[str]:
    """Plain statements of what the table shows -- each one read straight off a column."""
    scored = [a for a in per_arm.values() if a["accuracy"] is not None]
    if not scored:
        return ["No arm produced a gradable answer."]
    lines = []
    best = max(scored, key=lambda a: a["accuracy"])
    tied = [a["arm"] for a in scored if a["accuracy"] == best["accuracy"]]
    who = ", ".join(f"`{arm}`" for arm in tied) + (" (tied)" if len(tied) > 1 else "")
    lines.append(
        f"Highest accuracy: {who} at {best['accuracy']:.0%} "
        f"({best['correct']}/{best['served']}, 95% CI {best['accuracy_ci95'][0]:.0%}-{best['accuracy_ci95'][1]:.0%})."
    )
    # A flagged baseline is left out of per_arm, so nothing is read against it.
    base = per_arm.get(baseline)
    if base is None:
        lines.append(f"Baseline `{baseline}` is flagged above, so no figure here is read against it.")
    elif base["accuracy"] is not None:
        lines.append(f"Baseline `{baseline}`: {base['accuracy']:.0%} ({base['correct']}/{base['served']}).")
    leanest = min(scored, key=lambda a: a["input_tokens_p50"] or math.inf)
    if base is not None and base["input_tokens_p50"] and leanest["arm"] != baseline:
        change = leanest["input_tokens_p50"] / base["input_tokens_p50"] - 1
        lines.append(
            f"Fewest input tokens per run (median): `{leanest['arm']}`, {leanest['input_tokens_p50']:,.0f} "
            f"({change:+.0%} vs the baseline's {base['input_tokens_p50']:,.0f})."
        )
    fastest = min(scored, key=lambda a: a["latency_p50"] or math.inf)
    lines.append(f"Fastest median run: `{fastest['arm']}`, {fastest['latency_p50']:.1f}s.")
    overlapping = all(
        a["accuracy_ci95"][0] <= best["accuracy_ci95"][1] and best["accuracy_ci95"][0] <= a["accuracy_ci95"][1]
        for a in scored
    )
    if overlapping and len(scored) > 1:
        lines.append(
            "Every arm's accuracy interval overlaps the best one's: at this sample size the arms are not "
            "separated on accuracy."
        )
    return lines


def _winners(
    rows: list[dict[str, Any]], per_arm: dict[str, dict[str, Any]], baselines: dict[str, str]
) -> list[dict[str, Any]]:
    """Per platform and axis: the best arm, and its margin over the runner-up and over the platform's native.

    Arms are compared within a platform only: Claude and OpenAI arms run
    different models and count tokens with different tokenizers, so a winner
    across the two would mostly name a model. Each margin carries a verdict from
    a paired bootstrap over the runs both arms served -- ``clear`` when its 95%
    interval leaves out zero, ``within noise`` when it does not.
    """
    by_arm: dict[str, dict[tuple[int, int], dict[str, Any]]] = defaultdict(dict)
    for r in rows:
        by_arm[r["arm"]][(r["index"], r["repeat"])] = r
    result = []
    for platform, baseline in baselines.items():
        # The first arm stands in as a platform's baseline elsewhere, but only a native arm is called native here.
        # A flagged native arm is left out of per_arm, so no margin is measured against it.
        native = baseline if baseline in BASELINES and baseline in per_arm else None
        for axis in AXES:
            ranked = [a for a in per_arm.values() if a["platform"] == platform and a.get(axis["key"]) is not None]
            if not ranked:
                continue
            sign = 1 if axis["better"] == "higher" else -1
            ranked.sort(key=lambda a: -sign * a[axis["key"]])
            best = ranked[0]
            entry: dict[str, Any] = {
                "axis": axis["key"],
                "label": axis["label"],
                "better": axis["better"],
                "unit": axis["unit"],
                "platform": platform,
                "winner": best["arm"],
                "value": best[axis["key"]],
                "tied_with": [a["arm"] for a in ranked[1:] if a[axis["key"]] == best[axis["key"]]],
                "arms_ranked": len(ranked),
                "runner_up": None,
                "vs_native": None,
                "native": native,
                "native_has_figure": native is not None and per_arm[native].get(axis["key"]) is not None,
            }
            if len(ranked) > 1:
                entry["runner_up"] = _margin(axis, by_arm, best, ranked[1])
            if native is not None and best["arm"] != native and per_arm[native].get(axis["key"]) is not None:
                entry["vs_native"] = _margin(axis, by_arm, best, per_arm[native])
            result.append(entry)
    return result


def _margin(
    axis: dict[str, Any], by_arm: dict[str, dict[tuple[int, int], dict[str, Any]]], a: dict[str, Any], b: dict[str, Any]
) -> dict[str, Any]:
    """``a``'s lead over ``b`` on one axis, and whether the run can tell it from noise.

    The margin and its interval come from the same runs -- those both arms
    served -- so a margin never points one way while its interval says the
    other. The bootstrap resamples whole questions, because the repeats of one
    question are not independent of each other.
    """
    keys = sorted(
        k
        for k in by_arm[a["arm"]].keys() & by_arm[b["arm"]].keys()
        if axis.get("all_runs") or not (by_arm[a["arm"]][k].get("error") or by_arm[b["arm"]][k].get("error"))
    )
    values = [(_field(axis, by_arm[a["arm"]][k]), _field(axis, by_arm[b["arm"]][k])) for k in keys]
    stat = _STATS[axis["stat"]]
    groups: dict[int, list[tuple[float | None, float | None]]] = defaultdict(list)
    for key, value in zip(keys, values, strict=True):
        groups[key[0]].append(value)
    clusters = list(groups.values())
    entry: dict[str, Any] = {"arm": b["arm"], "value": b[axis["key"]], "paired": len(keys), "questions": len(clusters)}
    if len(clusters) < MIN_PAIRED:
        return {**entry, "margin": _paired(axis, stat, values), "margin_ci95": None, "verdict": TOO_FEW}
    if _both_zero(stat, values):
        return {**entry, "margin": 0.0, "margin_ci95": [0.0, 0.0], "verdict": TIED}
    margin = _paired(axis, stat, values)
    if margin is None:
        return {**entry, "margin": None, "margin_ci95": None, "verdict": TOO_FEW}
    rng = random.Random(BOOTSTRAP_SEED)
    draws = []
    for _ in range(BOOTSTRAP_RESAMPLES):
        sample = [value for _ in clusters for value in clusters[rng.randrange(len(clusters))]]
        drawn = _paired(axis, stat, sample)
        if drawn is not None:
            draws.append(drawn)
    if len(draws) < BOOTSTRAP_RESAMPLES // 2:
        return {**entry, "margin": margin, "margin_ci95": None, "verdict": TOO_FEW}
    draws.sort()
    low, high = draws[int(0.025 * len(draws))], draws[int(0.975 * len(draws)) - 1]
    return {**entry, "margin": margin, "margin_ci95": [low, high], "verdict": NOISE if low <= 0 <= high else CLEAR}


def _paired(
    axis: dict[str, Any], stat: Callable[[list[float]], float | None], values: list[tuple[float | None, float | None]]
) -> float | None:
    xs = [x for x, _ in values if x is not None]
    ys = [y for _, y in values if y is not None]
    return _margin_of(axis, stat(xs), stat(ys)) if xs and ys else None


def _both_zero(stat: Callable[[list[float]], float | None], values: list[tuple[float | None, float | None]]) -> bool:
    """Both arms at zero: a relative change is undefined, but the two are plainly level."""
    xs = [x for x, _ in values if x is not None]
    ys = [y for _, y in values if y is not None]
    return bool(xs and ys) and stat(xs) == 0 and stat(ys) == 0


def _margin_of(axis: dict[str, Any], a: float | None, b: float | None) -> float | None:
    """How far ``a`` is from ``b`` in the axis's unit: points, a relative change, seconds, or a count."""
    if a is None or b is None:
        return None
    if axis["unit"] == "pp":
        return (a - b) * 100
    if axis["unit"] == "pct":
        return a / b - 1 if b else None
    return a - b


def _field(axis: dict[str, Any], row: dict[str, Any]) -> float | None:
    if axis["field"] == "correct":
        return float(row.get("grade") == "correct")
    if axis["field"] == "failed":
        return float(bool(row.get("error")))
    value = row.get(axis["field"])
    return None if value is None else float(value)


_STATS: dict[str, Callable[[list[float]], float | None]] = {
    "mean": lambda v: statistics.mean(v) if v else None,
    "median": lambda v: statistics.median(v) if v else None,
    "p95": lambda v: _pct(sorted(v), 0.95),
}


def _markdown(summary: dict[str, Any]) -> str:
    setup, arms = summary["setup"], summary["arms"]
    platforms = summary.get("platforms") or platforms_of(setup)
    both = len(platforms) > 1
    text = PLATFORM_TEXT[BOTH if both else platforms[0]]
    per_platform = platform_setup(setup)
    out: list[str] = []
    w = out.append

    if both:
        w(f"# Agent benchmark: Claude and OpenAI agents, each {_title_tools(arms)}\n")
        w(
            f"A Claude agent and an OpenAI agent answered the same {page.question_set(setup)['name']} questions once "
            "per **arm**. On each "
            "platform the model and the task prompt were identical in every arm; **only the web tools changed** — "
            f"{page.tool_kinds(list(arms))} — and, for the guided web-router arms, the routing guide added to the "
            "system prompt. Every arm is compared with its own platform's baseline: the two platforms run different "
            "models, so a Claude row against an OpenAI row is mostly a comparison of models.\n"
        )
    else:
        w(f"# Agent benchmark: the same {text['agent']} agent, {_title_tools(arms)}\n")
        w(
            f"One {text['agent']} agent answered {page.question_set(setup)['name']} questions once per **arm**. The "
            "model and the task prompt "
            f"were identical in every arm; **only the web tools changed** — {page.tool_kinds(list(arms))} — and, for "
            "the guided web-router arms, the routing guide added to the system prompt. So a difference between two "
            "rows is the tools' and the guide's.\n"
        )

    w("## Setup\n")
    w("| | |\n|---|---|")
    agents = [
        (
            "Agent" if not both else f"{PLATFORM_TEXT[p]['agent']} agent",
            f"{ps.get('surface', 'Claude Managed Agents')}, model `{ps.get('model')}`, one session per run",
        )
        for p, ps in per_platform.items()
    ]
    limits = [
        ("Timeout / budget" if not both else f"{PLATFORM_TEXT[p]['agent']} timeout / budget", _limits(ps, setup))
        for p, ps in per_platform.items()
    ]
    for label, value in (
        (
            "Dataset",
            f"{page.question_set(setup)['name']} ({page.question_set(setup)['source']}), "
            f"{setup.get('asked') or setup.get('limit')} questions, seed {setup.get('seed')}",
        ),
        ("Repeats", f"{setup.get('repeats')} per question per arm"),
        *(
            [
                (
                    "Outage injected",
                    f"`{setup['outage']['vendor']}` {setup['outage']['kind']} on every call -- web-router side: "
                    f"{setup['outage']['web_router']}; direct side: {setup['outage']['direct']}",
                )
            ]
            if setup.get("outage")
            else []
        ),
        *agents,
        *limits,
        ("Results per web-router search", str(setup.get("max_results"))),
        ("Judge", _judge(setup)),
        ("Started", str(setup.get("started"))),
        *(
            (("Combined from", ", ".join(f"`{f}`" for f in setup["combined_from"])),)
            if setup.get("combined_from")
            else ()
        ),
    ):
        w(f"| {label} | {value} |")
    w("")

    w("### Arms\n")
    w("| Arm | Web tool |\n|---|---|")
    for arm, a in arms.items():
        w(f"| `{arm}` | {_describe(_spec(arm), PLATFORM_TEXT[a.get('platform', platforms[0])], setup)} |")
    w("")

    if setup.get("backends"):
        _write_backends(w, setup["backends"])

    if summary.get("alerts"):
        w("## ⚠️ Arms whose tools or runs failed\n")
        w(
            "These arms' tools or runs mostly failed, so their figures measure the failure, not the backend. They "
            "stay in the tables below, flagged, but are left out of the headline and of winners by axis. Check the "
            "backend's key and status, then rerun them.\n"
        )
        for alert in summary["alerts"]:
            w(f"- `{alert['arm']}`: {alert['reason']}.")
        w("")

    w("## Headline\n")
    for line in summary["headline"]:
        w(f"- {line}")
    w("")

    if summary.get("winners"):
        _write_winners(w, summary["winners"], both)

    w("## Results\n")
    w(
        "| Arm | Accuracy | 95% CI | Acc. when attempted | Failure rate | Tool calls | Model calls "
        "| Input tokens (median) | Input tokens (mean / max) | Output tokens | Tool chars (median) "
        f"| Built-in web searches | {text['cost']} (median / total) | p50 latency | p95 latency |"
    )
    w("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for a in arms.values():
        ci = a["accuracy_ci95"]
        w(
            f"| `{a['arm']}` | {_p(a['accuracy'])} ({a['correct']}/{a['served']}) "
            f"| {_p(ci[0]) + '-' + _p(ci[1]) if ci else 'n/a'} | {_p(a['accuracy_when_attempted'])} "
            f"| {_p(a['failure_rate'])} | {_n(a['tool_calls'], 1)} | {_n(a['model_calls'], 1)} "
            f"| {_n(a['input_tokens_p50'])} | {_n(a['input_tokens'])} / {_n(a['input_tokens_max'])} "
            f"| {_n(a['output_tokens'])} | {_n(a['tool_chars_p50'])} | {_n(a['web_searches'], 1)} "
            f"| {_usd(a['list_cost_p50'])} / {_usd(a['list_cost_total'])} "
            f"| {_s(a['latency_p50'])} | {_s(a['latency_p95'])} |"
        )
    w("")
    w(
        "Per served run. Tool calls, model calls and output tokens are means. Input tokens and tool chars lead "
        "with the median: a run that loops on searches can read ten times the tokens of a typical one, so one "
        "such run moves an arm's mean further than its whole difference from another arm. The mean and the "
        "largest single run sit beside the median so the tail stays visible.\n"
    )

    w("### What each column means\n")
    glossary = [(term, text["model_calls"] if term == "model calls" else meaning) for term, meaning in GLOSSARY]
    for term, meaning in [*glossary, (text["cost"], text["cost_meaning"])]:
        w(f"- **{term}** — {meaning}")
    w("")

    if summary["vs_baseline"]:
        if both:
            w("## Compared with each platform's native search\n")
            w(
                "Differences from the native arm of the arm's own platform. Accuracy in percentage points; median "
                "input tokens and median tool chars as a relative change; tool calls and median latency as absolute "
                "differences.\n"
            )
        else:
            w(f"## Compared with the baseline (`{summary['baseline']}`)\n")
            w(
                "Differences from the baseline arm. Accuracy in percentage points; median input tokens and median "
                "tool chars as a relative change; tool calls and median latency as absolute differences.\n"
            )
        w(
            f"| Arm | Accuracy | Input tokens (median) | Tool chars (median) | {text['cost']} (median) "
            "| Tool calls | p50 latency |\n"
            "|---|---|---|---|---|---|---|"
        )
        for arm, v in summary["vs_baseline"].items():
            w(
                f"| `{arm}` | {_signed(v['accuracy_pp'], ' pp')} | {_signed_pct(v['input_tokens_p50_pct'])} "
                f"| {_signed_pct(v['tool_chars_p50_pct'])} | {_signed_pct(v['list_cost_p50_pct'])} "
                f"| {_signed(v['tool_calls_diff'])} "
                f"| {_signed(v['latency_p50_diff'], 's')} |"
            )
        w("")

        if summary.get("vs_no_search"):
            w("## Compared with no search\n")
            w(
                "What each arm adds over the same agent with no web tool at all, its platform's `no-search` arm: "
                "how much of the accuracy is the search's rather than the model's own. Accuracy in percentage "
                f"points; {text['cost']} per correct answer is the session cost over the questions answered right, "
                "n/a where the platform states no cost.\n"
            )
            w(
                f"| Arm | Accuracy gained | {text['cost']} per correct answer | no-search's | Input tokens (median) |\n"
                "|---|---|---|---|---|"
            )
            for arm, v in summary["vs_no_search"].items():
                w(
                    f"| `{arm}` | {_signed(v['accuracy_pp'], ' pp')} | {_usd_fine(v['list_cost_per_correct'])} "
                    f"| {_usd_fine(v['base_list_cost_per_correct'])} | {_signed_pct(v['input_tokens_p50_pct'])} |"
                )
            w("")

        w(
            "### Question by question against the baseline\n"
            if not both
            else "### Question by question against native\n"
        )
        w(
            "For each question both arms served: were both right, did only one get it, or were both wrong. "
            'With repeats, an arm is "better" on a question when it was right more often, and the two are tied '
            "when both were right on the same share of their repeats, some but not all.\n"
        )
        w(
            "| Arm | Both right | Only this arm right | Only baseline right | Both wrong | Tied |\n"
            "|---|---|---|---|---|---|"
        )
        for arm, h in summary["head_to_head"].items():
            w(
                f"| `{arm}` | {h['both_right']} | {h['arm_better']} | {h['baseline_better']} | {h['both_wrong']} "
                f"| {h['tie']} |"
            )
        w("")

    if summary["guide_effect"]:
        w("## What the routing guide adds\n")
        w(
            "Each guided web-router arm against the same tools without the guide. The tools are identical, so "
            "these differences are the guide's.\n"
        )
        w(
            f"| Arm | Accuracy | Input tokens (median) | {text['cost']} (median) | Tool calls | p50 latency |\n"
            "|---|---|---|---|---|---|"
        )
        for arm, v in summary["guide_effect"].items():
            w(
                f"| `{arm}` vs `{arm}{NO_GUIDE}` | {_signed(v['accuracy_pp'], ' pp')} "
                f"| {_signed_pct(v['input_tokens_p50_pct'])} | {_signed_pct(v['list_cost_p50_pct'])} "
                f"| {_signed(v['tool_calls_diff'])} | {_signed(v['latency_p50_diff'], 's')} |"
            )
        w("")

    category = page.question_set(setup)["category"]
    w(f"## Accuracy by {category}\n")
    w(page.question_set(setup)["category_note"] + "\n")
    w(f"| {category.capitalize()} | Questions | " + " | ".join(f"`{a}`" for a in arms) + " |")
    w("|---|---|" + "---|" * len(arms))
    for kind, entry in summary["by_reasoning_type"].items():
        cells = " | ".join(_p(entry["accuracy"].get(a)) for a in arms)
        w(f"| {kind} | {entry['questions']} | {cells} |")
    w("")

    served_by = {arm: a["served_by"] for arm, a in arms.items() if a["served_by"]}
    if served_by:
        w("## Which backend served each web-router arm\n")
        w(
            "Read from each call's response, so for `auto` this is who actually answered. Searches and extracted "
            "URLs are counted apart because backends price them separately; an extract call of several URLs "
            "counts each one.\n"
        )
        for arm, counts in served_by.items():
            line = f"- `{arm}`: " + ", ".join(f"{p} ({n} calls)" for p, n in counts.items())
            searches, urls = arms[arm]["searches_total"], arms[arm]["extract_urls_total"]
            if searches is not None and urls is not None:
                line += f" -- {searches} searches, {urls} URLs extracted"
            w(line)
        w("")

    if any(a.get("withheld_total") or a.get("answer_key_seen_runs") for a in arms.values()):
        _write_answer_key(w, arms)

    w("## Every question\n")
    w("| # | Question | Gold | " + " | ".join(f"`{a}`" for a in arms) + " |")
    w("|---|---|---|" + "---|" * len(arms))
    for q in summary["questions"]:
        cells = " | ".join(
            " ".join(GRADE_MARK.get(run["grade"], f"⚠️ {run['grade']}") for run in q["arms"].get(a, [])) or "—"
            for a in arms
        )
        w(f"| {q['index']} | {_clip(q['question'], QUESTION_CHARS)} | {_clip(q['gold'], QUESTION_CHARS)} | {cells} |")
    w("")

    if summary["failures"]:
        w("## Failed runs\n")
        w("| Arm | Question | Repeat | Failed at | Error |\n|---|---|---|---|---|")
        for f in summary["failures"]:
            w(f"| `{f['arm']}` | {f['index']} | {f['repeat']} | {f['failed_at']} | {_clip(f['error'] or '', 160)} |")
        w("")

    w("## How to read this\n")
    w(
        "- **Sample size.** Agents are noisy and the confidence intervals say how much: with 20 questions an arm's "
        "interval spans roughly ±20 points. Treat a gap smaller than that as unresolved, and rerun with "
        "`--limit 100 --repeats 3` before drawing a line between close arms.\n"
        "- **Two things change at once against the baseline.** A web-router arm swaps the tools and, when "
        "guided, adds the routing guide. The `:no-guide` arm has the same tools without the guide, so the gap "
        "from the baseline to `:no-guide` is the tools and the gap from `:no-guide` to the guided arm is the "
        "guide.\n"
        f"{text['cost_note']}\n"
        + (
            "- **Compare within a platform.** Claude and OpenAI arms run different models, and each platform "
            "counts tokens with its own tokenizer, so accuracy, tokens and latency compare across rows of one "
            "platform; across the two, compare how far each arm moved from its own native search.\n"
            if both
            else ""
        )
        + (
            "- **An `mcp:` arm is the vendor as shipped.** Its server decides what a search returns, and the model "
            "picks how many results to ask for, where a web-router arm sends a fixed `max_results` and cuts the "
            "text to a budget; it reads pages only where the vendor's server has a page-read tool; and the "
            "platform calls the server, so its latency includes the platform's round trips. Against the "
            "`web-router:` arm on the same vendor, the gap is what web-router's tools add over connecting the "
            "vendor directly. The vendor bills its searches separately here too.\n"
            if any(_spec(arm).startswith(MCP) for arm in arms)
            else ""
        )
        + (
            "- **A `direct:` arm is the vendor's API with nothing in between.** The agent calls the vendor's search "
            "and page read itself and reads the raw JSON, with the request a developer following the vendor's docs "
            "would send. Against the `web-router:` arm on the same vendor, the gap is what web-router adds: a "
            "rendering cut to a budget, the routing guide, and the request web-router tunes per vendor.\n"
            if any(_spec(arm).startswith(DIRECT) for arm in arms)
            else ""
        )
        + (
            f"- **This run injected an outage.** `{setup['outage']['vendor']}` failed on purpose on every call, so "
            "an arm that leans on it measures how it copes, not the vendor: read failure rate, accuracy and who "
            "served each call after it (below), not winners. A flagged arm is the expected result here.\n"
            if setup.get("outage")
            else ""
        )
        + "- **Accuracy is a judgment.** It is one model's grade against the question set's gold answer and is "
        "comparable only "
        "with runs graded by the same judge. Every other column is measured.\n"
        "- **Tokens are a cost driver, not a cost.** No price is shown because none was stated by a provider; "
        "multiply by your own model's rates."
    )
    return "\n".join(out) + "\n"


def _judge(setup: dict[str, Any]) -> str:
    line = f"`{setup.get('judge')}` with the SimpleQA grader prompt (same judge for every arm)"
    family = own_family(setup)
    if family and len(platforms_of(setup)) > 1:
        agents = " and ".join(PLATFORM_TEXT[p]["agent"] for p in family)
        line += (
            f". **The judge is from the same model family as the {agents} agents**, so it is not independent of "
            "them; accuracy here is comparable only with runs graded by this same judge"
        )
    elif family:
        line += (
            ". **The judge is from the same model family as the agents**, so it is not independent of them; "
            "accuracy here is comparable only with runs graded by this same judge"
        )
    return line


def _limits(platform: dict[str, Any], setup: dict[str, Any]) -> str:
    timeout = platform.get("timeout", setup.get("timeout"))
    if "max_turns" in platform:
        return f"{timeout:.0f}s and {platform['max_turns']} requests per run"
    return f"{timeout:.0f}s and ${platform.get('session_budget_usd', 0):.2f} per session"


def backend_lines(config: dict[str, Any]) -> list[tuple[str, str, str]]:
    """``(tool, backend, request)`` for one arm's tools, ``auto`` as its first member and the rest by name.

    Shared with ``page.py`` so the report and the page describe a request the same way.
    """
    lines = []
    for tool in ("search", "extract"):
        entry = config.get(tool)
        if not entry:
            continue
        # A member auto passes over for this request is sent nothing, so it is left out of the order.
        chain = [m for m in entry.get("chain") or [] if "skipped" not in m]
        if chain:
            first, rest = chain[0], [m["provider"] for m in chain[1:]]
            backend = f"auto: {first['provider']} first" + (f", then {', '.join(rest)}" if rest else "")
            lines.append((tool, backend, _request(first)))
        else:
            lines.append((tool, entry["provider"], _request(entry)))
    # An mcp: arm: each of its servers, with the tools the agent was given.
    lines += [("mcp", entry["provider"], _request(entry)) for entry in config.get("mcp") or []]
    return lines


def _request(entry: dict[str, Any]) -> str:
    # "body" and "body_error" are how runs recorded before the whole call was kept.
    if "call" in entry:
        parts = [f"{key} {json.dumps(value, ensure_ascii=False)}" for key, value in entry["call"].items()]
    elif "body" in entry:
        parts = [json.dumps(entry["body"], ensure_ascii=False)]
    else:
        return entry.get("call_error") or entry.get("body_error") or "not described"
    return " ".join([entry["endpoint"], *parts] if entry.get("endpoint") else parts)


def _write_backends(w: Callable[[str], None], backends: dict[str, dict[str, Any]]) -> None:
    w("### What each backend was sent\n")
    w(
        "The request body each web-router arm's backends received, built by the providers' own translation with "
        "the model's query or URL as a placeholder. A field that is not here was not sent, so the backend's own "
        "default decided it — Nimble's search depth, for one. Page reads go to the arm's own backend when it "
        "reads pages, else to `auto`, which tries its members in order. An `mcp:` arm's row is its server and the "
        "tools the agent was given: the platform builds that request, and the model picks its arguments.\n"
    )
    w("| Arm | Tool | Backend | Request |\n|---|---|---|---|")
    for arm, config in backends.items():
        for tool, backend, request in backend_lines(config):
            cell = request.replace("|", PIPE_ESCAPED)
            w(f"| `{arm}` | {tool} | {backend} | `{cell}` |")
    w("")


def _write_winners(w: Callable[[str], None], winners: list[dict[str, Any]], both: bool) -> None:
    w("## Winners by axis\n")
    w(
        "The best arm on each axis"
        + (", within each platform" if both else "")
        + ", and how far ahead it is of the next arm and of native search. Each margin has a verdict from a "
        f"paired bootstrap: the runs both arms served, resampled {BOOTSTRAP_RESAMPLES:,} times. **clear** means "
        "the margin's 95% interval leaves out zero; **within noise** means this run does not tell the two apart, "
        "however large the gap looks. An arm with no figure on an axis is not ranked on it.\n"
    )
    w(("| Platform " if both else "") + "| Axis | Winner | Value | Over the runner-up | Over native |")
    w(("|---" if both else "") + "|---|---|---|---|---|")
    for entry in winners:
        platform = f"| {PLATFORM_TEXT[entry['platform']]['agent']} " if both else ""
        if entry["vs_native"] is not None:
            vs_native = _margin_cell(entry, entry["vs_native"])
        elif entry.get("native", True) is None:
            vs_native = NO_NATIVE
        else:
            vs_native = "— (native is best)" if entry["native_has_figure"] else "n/a (native has no figure)"
        runner_up = "—" if entry["runner_up"] is None else _margin_cell(entry, entry["runner_up"])
        value = _axis_value(entry["unit"], entry["axis"], entry["value"])
        tied = ", ".join(f"`{arm}`" for arm in entry["tied_with"])
        winner = f"`{entry['winner']}`" + (f" (tied with {tied})" if tied else "")
        w(f"{platform}| {entry['label']} | {winner} | {value} | {runner_up} | {vs_native} |")
    w("")


def _margin_cell(entry: dict[str, Any], margin: dict[str, Any]) -> str:
    unit, value = entry["unit"], margin["margin"]
    if value is None:
        shown = "n/a"
    elif unit == "pp":
        shown = f"{value:+.1f} pp"
    elif unit == "pct":
        shown = f"{value:+.0%}"
    elif unit == "s":
        shown = f"{value:+.1f}s"
    else:
        shown = f"{value:+.1f}"
    return f"{shown} vs `{margin['arm']}` ({margin['verdict']})"


def _axis_value(unit: str, axis: str, value: float) -> str:
    if unit == "pp":
        return _p(value)
    if unit == "s":
        return _s(value)
    if axis.startswith("list_cost"):
        return _usd(value)
    return _n(value, 1 if unit == "abs" else 0)


def _write_answer_key(w: Callable[[str], None], arms: dict[str, dict[str, Any]]) -> None:
    w("## Answer-key pages\n")
    w(
        "Pages that may hold this question set's own answers -- its dataset, its paper and their copies. An arm whose "
        "tools run here (`web-router:`, `direct:`) never shows them to the agent: a search drops them and a read "
        "fails, and they are counted under *withheld*. A native or `mcp:` arm's tools run on the platform, which "
        "shows the agent the page before this benchmark sees it, so those runs are counted, not filtered: read their "
        "accuracy knowing some answers may have been looked up rather than found.\n"
    )
    w("| Arm | Withheld from the agent | Runs that saw one | Of those, correct |\n|---|---|---|---|")
    for arm, a in arms.items():
        # Every arm, a zero included: an arm that never met one is part of the comparison.
        withheld, seen = a.get("withheld_total") or 0, a.get("answer_key_seen_runs") or 0
        w(f"| `{arm}` | {withheld} | {seen} | {a.get('answer_key_seen_correct') or 0} |")
    w("")


def _describe(arm: str, text: dict[str, str], setup: dict[str, Any]) -> str:
    if arm in BASELINES:
        return text["native"]
    if arm == NO_SEARCH:
        return "no web tool at all: the same agent and prompt, answering from what the model already knows"
    if arm.startswith(MCP):
        shuffled = arm.endswith(SHUFFLED)
        target = arm.removeprefix(MCP).removesuffix(SHUFFLED)
        whose = (
            "every vendor's own hosted MCP server at once (`mcp:all`)"
            if target == "all"
            else f"`{target}`'s own hosted MCP server"
        )
        tools = _mcp_tools(arm, setup)
        order = ", listed in a seeded order of its own for each question" if shuffled else ""
        return f"{whose}, {tools}, attached to the agent and called by the platform{order} -- no web-router"
    if arm.startswith(DIRECT):
        vendor = arm.removeprefix(DIRECT)
        return (
            f"`{vendor}`'s own REST API as two plain tools, search and page read, its JSON returned as it comes "
            "back -- no web-router, no routing guide; the request is under what each backend was sent"
        )
    spec = arm.removeprefix("web-router:")
    provider, _, extra = spec.partition(":")
    tools = f"web-router `web_router_search` + `web_router_extract`, provider `{provider}`"
    if provider.startswith("auto"):
        tools += " (tries its chain in order, falls back on failure; the order is under what each backend was sent)"
    return tools + (", no routing guide" if extra == "no-guide" else ", with `routing_guide()` in the system prompt")


def _mcp_tools(arm: str, setup: dict[str, Any]) -> str:
    """Which tools an ``mcp:`` arm's servers gave the agent, from the tools ``run.json`` recorded for each."""
    servers = ((setup.get("backends") or {}).get(arm) or {}).get("mcp")
    if not servers:
        return "its search tool, and its page-read tool where the vendor has one"
    without = [s["provider"] for s in servers if len(s["call"]["mcp_tools"]) < MCP_TOOLS_WITH_READ]
    if not without:
        return "search and page-read tools"
    if len(without) == len(servers):
        return "its search tool only, no page reads"
    return f"search tools, and page-read tools where the vendor has one (not {', '.join(without)})"


def _wilson(correct: int, n: int) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = correct / n
    denominator = 1 + Z_95**2 / n
    centre = (p + Z_95**2 / (2 * n)) / denominator
    margin = Z_95 * math.sqrt(p * (1 - p) / n + Z_95**2 / (4 * n * n)) / denominator
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def _by_repeat(served: list[dict[str, Any]]) -> list[float]:
    repeats = sorted({r["repeat"] for r in served})
    return [statistics.mean(r.get("grade") == "correct" for r in served if r["repeat"] == k) for k in repeats]


def _mean(rows: list[dict[str, Any]], key: str) -> float | None:
    return statistics.mean(r[key] for r in rows) if rows else None


def _mean_known(rows: list[dict[str, Any]], key: str) -> float | None:
    known = [r[key] for r in rows if r.get(key) is not None]
    return statistics.mean(known) if known else None


def _median_known(rows: list[dict[str, Any]], key: str) -> float | None:
    known = [r[key] for r in rows if r.get(key) is not None]
    return statistics.median(known) if known else None


def _total_known(rows: list[dict[str, Any]], key: str) -> float | None:
    known = [r[key] for r in rows if r.get(key) is not None]
    return sum(known) if known else None


def _median(rows: list[dict[str, Any]], key: str) -> float | None:
    return statistics.median(r[key] for r in rows) if rows else None


def _pct(values: list[float], fraction: float) -> float | None:
    return values[max(1, round(fraction * len(values))) - 1] if values else None


def _p(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def _n(value: float | None, digits: int = 0) -> str:
    return "n/a" if value is None else f"{value:,.{digits}f}"


def _usd(value: float | None) -> str:
    return "n/a" if value is None else f"${value:,.2f}"


def _title_tools(arms: Any) -> str:
    """The title's account of the arms: native against web-router where both ran, else a claim of nothing more."""
    specs = [_spec(arm) for arm in arms]
    native = any(spec in BASELINES for spec in specs)
    if native and any(spec.startswith(WEB_ROUTER) for spec in specs):
        return "on its built-in web search and on web-router"
    return "on different web tools"


def _usd_fine(value: float | None) -> str:
    """A cost per answer, a few cents, to the tenth of a cent so two arms' figures can be told apart."""
    return "n/a" if value is None else f"${value:,.3f}"


def _s(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f}s"


def _signed(value: float | None, unit: str = "") -> str:
    return "n/a" if value is None else f"{value:+.1f}{unit}"


def _signed_pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.0%}"


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split()).replace("|", "\\|")
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def write(folder: Path, rows: list[dict[str, Any]], setup: dict[str, Any]) -> str:
    """Write ``summary.json``, ``report.md`` and the stakeholder ``page.html`` beside the rows; return the report."""
    summary, report = build(rows, setup)
    (folder / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    (folder / "report.md").write_text(report, encoding="utf-8")
    page.write(folder, summary)
    return report


def read(folder: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = [json.loads(line) for line in (folder / ROWS_FILE).read_text(encoding="utf-8").splitlines() if line]
    return rows, json.loads((folder / RUN_FILE).read_text(encoding="utf-8"))


def combine(
    runs: list[tuple[str, list[dict[str, Any]], dict[str, Any]]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Several runs' rows and setups as one run of every platform they cover.

    Each web-router arm takes its platform in front of its name, as in a run of
    both, so ``web-router:nimble`` on Claude and on OpenAI stay two arms.
    Raises ``SystemExit`` on runs that did not ask the same questions the same way, graded against the same
    answers, or did not finish every question for every arm.
    """
    for key in MATCHED_SETUP:
        # A run from before --questions existed asked FRAMES, so it still combines with a FRAMES run that says so.
        values = {json.dumps(setup.get(key) or ("frames" if key == "questions" else None)) for _, _, setup in runs}
        if len(values) > 1:
            raise SystemExit(MSG_COMBINE_MISMATCH.format(key=key, values=", ".join(sorted(values))))
    asked: dict[int, tuple[str, str]] = {}
    rows: list[dict[str, Any]] = []
    arms: list[str] = []
    per_platform: dict[str, dict[str, Any]] = {}
    family: set[str] = set()
    backends: dict[str, Any] = {}
    for folder, run_rows, setup in runs:
        # An arm keeps its name when its chain changes (auto-shipped did, with auto's default order), so two runs of
        # one arm may have sent different requests; merged, the report would show one chain for both.
        for arm, config in (setup.get("backends") or {}).items():
            if arm in backends and backends[arm] != config:
                values = ", ".join(sorted({json.dumps(backends[arm]), json.dumps(config)}))
                raise SystemExit(MSG_COMBINE_MISMATCH.format(key=f"backends[{arm}]", values=values))
            backends[arm] = config
        _check_complete(folder, run_rows, setup)
        platforms = platforms_of(setup)
        for platform, ps in platform_setup(setup).items():
            known = per_platform.setdefault(platform, ps)
            for key in MATCHED_PLATFORM_SETUP:
                if known.get(key) != ps.get(key):
                    values = f"{known.get(key)}, {ps.get(key)}"
                    raise SystemExit(MSG_COMBINE_PLATFORM.format(platform=platform, key=key, values=values))
        family.update(own_family(setup))
        renamed = {}
        for arm in setup.get("arms") or list(dict.fromkeys(r["arm"] for r in run_rows)):
            platform = _platform_of(arm, platforms)
            name = arm if arm in BASELINES or arm != _spec(arm) else f"{platform}{PLATFORM_SEPARATOR}{arm}"
            if name in arms:
                raise SystemExit(MSG_COMBINE_ARM.format(arm=name))
            renamed[arm] = name
            arms.append(name)
        for r in run_rows:
            question, gold = asked.setdefault(r["index"], (r["question"], r["gold"]))
            if question != r["question"]:
                raise SystemExit(MSG_COMBINE_QUESTIONS.format(index=r["index"]))
            if gold != r["gold"]:
                raise SystemExit(MSG_COMBINE_GOLD.format(index=r["index"]))
            platform = r.get("platform") or _platform_of(r["arm"], platforms)
            rows.append({**r, "arm": renamed.get(r["arm"], r["arm"]), "platform": platform})
    first = runs[0][2]
    setup = {
        "arms": arms,
        **{key: first.get(key) for key in MATCHED_SETUP},
        # Runs that match on the set, its file and the limit loaded the same questions.
        **({"asked": first["asked"]} if first.get("asked") else {}),
        "timeout": first.get("timeout"),
        "platforms": [p for p in PLATFORMS if p in per_platform],
        "platform_setup": {p: per_platform[p] for p in PLATFORMS if p in per_platform},
        "judge_same_family": [p for p in PLATFORMS if p in family],
        "started": min(str(setup.get("started")) for _, _, setup in runs),
        "combined_from": [folder for folder, _, _ in runs],
        **({"backends": backends} if backends else {}),
    }
    return rows, setup


def select(
    rows: list[dict[str, Any]], setup: dict[str, Any], keep: list[str]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Only the arms in ``keep``, by their combined names, in that order -- every one of their rows, untouched."""
    unknown = next((arm for arm in keep if arm not in setup["arms"]), None)
    if unknown:
        raise SystemExit(MSG_SELECT_UNKNOWN.format(arm=unknown, known=", ".join(setup["arms"])))
    specs = {_spec(arm) for arm in keep}
    kept = {_platform_of(arm, platforms_of(setup)) for arm in keep}
    selected = {
        **setup,
        "arms": list(keep),
        # A platform none of the kept arms ran on would otherwise still title the report and fill its setup rows.
        "platforms": [p for p in platforms_of(setup) if p in kept],
        "platform_setup": {p: ps for p, ps in platform_setup(setup).items() if p in kept},
        "judge_same_family": [p for p in own_family(setup) if p in kept],
    }
    if setup.get("backends"):
        selected["backends"] = {spec: config for spec, config in setup["backends"].items() if spec in specs}
    return [r for r in rows if r["arm"] in keep], selected


def _check_complete(folder: str, rows: list[dict[str, Any]], setup: dict[str, Any]) -> None:
    """Every arm the setup names ran every question it asked, at every repeat -- a failed run is still a row."""
    questions = setup.get("asked") or setup.get("limit") or len({r["index"] for r in rows})
    repeats = setup.get("repeats") or 1
    expected = questions * repeats
    done: dict[str, set[tuple[int, int]]] = defaultdict(set)
    for r in rows:
        done[r["arm"]].add((r["index"], r["repeat"]))
    for arm in setup.get("arms") or list(done):
        if len(done[arm]) != expected:
            raise SystemExit(
                MSG_COMBINE_INCOMPLETE.format(
                    arm=arm, folder=folder, got=len(done[arm]), expected=expected, questions=questions, repeats=repeats
                )
            )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("folder", nargs="?", type=Path, help="a run's folder, to rebuild its report")
    parser.add_argument("--combine", nargs="+", type=Path, default=None, help="run folders to merge into --out")
    parser.add_argument("--out", type=Path, default=None, help="with --combine: the merged run's folder")
    parser.add_argument(
        "--arms", default=None, help="with --combine: comma-separated arms to keep, by their combined names"
    )
    args = parser.parse_args(argv)
    if args.arms and not args.combine:
        parser.error("--arms works only with --combine")
    if args.combine:
        if args.out is None or len(args.combine) < 2:
            parser.error("--combine takes two or more run folders and an --out folder")
        if (args.out / ROWS_FILE).exists():
            raise SystemExit(MSG_COMBINE_OUT.format(path=args.out / ROWS_FILE))
        rows, setup = combine([(str(folder), *read(folder)) for folder in args.combine])
        if args.arms:
            rows, setup = select(rows, setup, [a.strip() for a in args.arms.split(",") if a.strip()])
        args.out.mkdir(parents=True, exist_ok=True)
        with (args.out / ROWS_FILE).open("w", encoding="utf-8") as handle:
            handle.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
        (args.out / RUN_FILE).write_text(json.dumps(setup, indent=2), encoding="utf-8")
        folder = args.out
    elif args.folder is not None:
        folder = args.folder
        rows, setup = read(folder)
    else:
        parser.error("pass a run's folder, or --combine with --out")
    write(folder, rows, setup)
    print(f"wrote {folder / 'report.md'}, {folder / 'summary.json'} and {folder / 'page.html'}")


if __name__ == "__main__":
    main()
