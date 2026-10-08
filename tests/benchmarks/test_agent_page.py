"""The agent benchmark's stakeholder page -- what a reader sees, built from the summary ``report.build()`` makes."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

AGENT_BENCH = Path(__file__).resolve().parents[2] / "benchmarks" / "agent"

SHARED: dict[str, Any] = {"limit": 6, "seed": 0, "repeats": 1, "max_results": 5, "timeout": 600.0}


@pytest.fixture(scope="module")
def bench() -> Iterator[tuple[ModuleType, ModuleType]]:
    sys.path.insert(0, str(AGENT_BENCH))
    try:
        import page
        import report

        yield report, page
    finally:
        sys.path.remove(str(AGENT_BENCH))
        sys.modules.pop("report", None)
        sys.modules.pop("page", None)


def _row(arm: str, index: int, correct: bool, platform: str, **extra: Any) -> dict[str, Any]:
    return {
        "arm": arm,
        "repeat": 0,
        "index": index,
        "question": f"q{index}",
        "gold": "a",
        "reasoning_types": "Numerical reasoning",
        "answer": "a",
        "grade": "correct" if correct else "incorrect",
        "error": None,
        "failed_at": None,
        "latency": 10.0,
        "model_calls": 2,
        "tool_calls": 2,
        "tool_errors": 0,
        "tool_chars": 1000,
        "input_tokens": 5000,
        "output_tokens": 100,
        "web_searches": 0,
        "list_cost_usd": None,
        "providers": [],
        "platform": platform,
        **extra,
    }


def _both(report: ModuleType) -> dict[str, Any]:
    arms = ["claude-native", "claude/web-router:nimble", "openai-native", "openai/web-router:nimble"]
    rows = []
    for i in range(6):
        rows += [
            _row("claude-native", i, i < 3, "claude", input_tokens=20000, list_cost_usd=0.2),
            _row("claude/web-router:nimble", i, True, "claude", input_tokens=8000, list_cost_usd=0.05),
            _row("openai-native", i, i < 5, "openai", tool_chars=None),
            _row("openai/web-router:nimble", i, i < 5, "openai"),
        ]
    setup = {
        **SHARED,
        "arms": arms,
        "judge": "openai:gpt-4o",
        "platforms": ["claude", "openai"],
        "platform_setup": {
            "claude": {"model": "claude-opus-5", "surface": "Claude Managed Agents", "session_budget_usd": 2.0},
            "openai": {"model": "gpt-5", "surface": "OpenAI Responses API", "max_turns": 30},
        },
        "judge_same_family": ["openai"],
    }
    summary, _ = report.build(rows, setup)
    return summary


def test_a_two_platform_page_names_each_platforms_winner_with_its_verdict(
    bench: tuple[ModuleType, ModuleType],
) -> None:
    report, page = bench
    html = page.render(_both(report))

    assert html.startswith("<title>") and "<html" not in html
    assert "Claude" in html and "OpenAI" in html
    # Claude's web-router arm read fewer tokens on every question: a margin the run can call.
    assert "web-router:nimble</code>, -60% against native" in html
    assert "Clear</span>" in html and "Not separated</span>" in html
    assert "same model family as the OpenAI agents" in html


def test_a_figure_a_platform_does_not_report_is_shown_as_absent_never_as_zero(
    bench: tuple[ModuleType, ModuleType],
) -> None:
    report, page = bench
    html = page.render(_both(report))

    assert "n/a (native has no figure)" in html  # OpenAI's native search keeps its tool chars to itself
    assert "No cost figure" in html  # and OpenAI reports no cost at all
    results = html.split('<table class="results">')[1]
    for line in results.split("<tr>"):
        if "openai" in line.split("</th>")[0]:
            assert "$" not in line


def test_an_arm_without_a_figure_is_left_off_that_chart_and_named(bench: tuple[ModuleType, ModuleType]) -> None:
    report, page = bench
    summary = _both(report)
    summary["arms"]["openai-native"]["input_tokens_p50"] = None

    html = page.render(summary)
    tokens = html.split("Input tokens per run (median)")[1].split("</figure>")[0]

    assert "Not shown, no figure: <code>openai-native</code>" in tokens
    assert 'title="openai-native' not in tokens


def test_a_single_platform_page_renders_and_escapes_what_came_from_the_run(
    bench: tuple[ModuleType, ModuleType],
) -> None:
    report, page = bench
    arms = ["openai-native", "web-router:nimble"]
    rows = [_row(arm, i, True, "openai") for arm in arms for i in range(3)]
    setup = {
        **SHARED,
        "arms": arms,
        "platform": "openai",
        "model": "<script>alert(1)</script>",
        "surface": "OpenAI Responses API",
        "max_turns": 30,
        "judge": "anthropic:claude-sonnet-5-5",
    }
    summary, _ = report.build(rows, setup)

    html = page.render(summary)

    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "Winners by axis" in html and "<h3>OpenAI</h3>" in html
    assert "same model family" not in html
