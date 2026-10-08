"""The agent benchmark's report and command line -- the numbers it prints, not the sessions it runs.

``benchmarks/agent/`` is a script beside the package, not part of it, so its
two modules are imported from their folder the way ``run.py`` imports
``report.py``.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import httpx
import pytest

from web_router.integrations.anthropic import ToolResult
from web_router.types import ExtractedDocument, ExtractFailure, ExtractResponse, SearchResponse

AGENT_BENCH = Path(__file__).resolve().parents[2] / "benchmarks" / "agent"

# What `run.py` writes to run.json, which the report reads back.
SETUP: dict[str, Any] = {
    "limit": 2,
    "seed": 0,
    "repeats": 1,
    "model": "claude-opus-5",
    "surface": "Claude Managed Agents",
    "max_results": 5,
    "timeout": 600.0,
    "session_budget_usd": 2.0,
    "judge": "openai:gpt-4o",
    "started": "2026-09-29T00:00:00+00:00",
}


@pytest.fixture(scope="module")
def bench() -> Iterator[tuple[ModuleType, ModuleType]]:
    sys.path.insert(0, str(AGENT_BENCH))
    try:
        import report
        import run

        yield report, run
    finally:
        sys.path.remove(str(AGENT_BENCH))
        sys.modules.pop("report", None)
        sys.modules.pop("run", None)
        sys.modules.pop("direct", None)


def _row(arm: str, index: int, grade: str, *, repeat: int = 0, latency: float = 1.0, **extra: Any) -> dict[str, Any]:
    return {
        "arm": arm,
        "repeat": repeat,
        "index": index,
        "question": f"q{index}",
        "gold": "a",
        "reasoning_types": "Numerical reasoning",
        "answer": "a",
        "grade": grade,
        "error": None,
        "failed_at": None,
        "latency": latency,
        "model_calls": 1,
        "tool_calls": 1,
        "tool_errors": 0,
        "tool_chars": 10,
        "input_tokens": 100,
        "output_tokens": 10,
        "web_searches": 0,
        "list_cost_usd": None,
        "providers": [],
        **extra,
    }


def test_p50_latency_is_the_median_of_an_even_count(bench: tuple[ModuleType, ModuleType]) -> None:
    report, _ = bench
    rows = [_row("claude-native", 0, "correct", latency=1.0), _row("claude-native", 1, "correct", latency=3.0)]

    summary, _ = report.build(rows, {**SETUP, "arms": ["claude-native"]})

    assert summary["arms"]["claude-native"]["latency_p50"] == 2.0


def test_repeats_count_each_question_once_and_show_their_ties(bench: tuple[ModuleType, ModuleType]) -> None:
    report, _ = bench
    arms = ["claude-native", "web-router:nimble"]
    # Question 0: each arm right on one of its two repeats -- a tie. Question 1: both right twice.
    rows = [
        _row(arm, index, grade, repeat=repeat)
        for arm in arms
        for index, grades in ((0, ("correct", "incorrect")), (1, ("correct", "correct")))
        for repeat, grade in enumerate(grades)
    ]

    summary, markdown = report.build(rows, {**SETUP, "arms": arms, "repeats": 2})

    assert summary["by_reasoning_type"]["Numerical reasoning"]["questions"] == 2
    assert summary["head_to_head"]["web-router:nimble"] == {
        "both_right": 1,
        "arm_better": 0,
        "baseline_better": 0,
        "both_wrong": 0,
        "tie": 1,
    }
    assert "| Both wrong | Tied |" in markdown
    assert "| `web-router:nimble` | 1 | 0 | 0 | 0 | 1 |" in markdown


def test_concurrency_below_one_is_refused(bench: tuple[ModuleType, ModuleType]) -> None:
    _, run = bench
    with pytest.raises(SystemExit):
        run.parse_args(["--concurrency", "0"])
    assert run.parse_args(["--concurrency", "2"]).concurrency == 2


def test_a_folder_that_already_holds_rows_is_refused_before_anything_runs(
    bench: tuple[ModuleType, ModuleType], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, run = bench
    (tmp_path / run.ROWS_FILE).write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(run, "ENV_FILE", tmp_path / "absent.env")

    def must_not_run(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("the benchmark started")

    monkeypatch.setattr(run, "run", must_not_run)

    with pytest.raises(SystemExit, match="already exists"):
        run.main(["--out", str(tmp_path)])


async def test_searches_and_extracted_urls_are_counted_apart(bench: tuple[ModuleType, ModuleType]) -> None:
    _, run = bench
    search = ToolResult("…", response=SearchResponse(provider="nimble", query="q", results=[]))
    extract = ToolResult(
        "…",
        response=ExtractResponse(
            provider="nimble",
            documents=[ExtractedDocument(url="https://a.example", content="a")],
            failures=[ExtractFailure(url="https://b.example", error="HTTP 404")],
        ),
    )
    refused = ToolResult("bad input", is_error=True)  # the model's mistake: nothing reached a backend

    async def send(session_id: str, events: list[dict[str, Any]]) -> None:
        pass

    client = SimpleNamespace(beta=SimpleNamespace(sessions=SimpleNamespace(events=SimpleNamespace(send=send))))

    async def done(result: ToolResult) -> ToolResult:
        return result

    pending = {f"use-{i}": asyncio.ensure_future(done(r)) for i, r in enumerate((search, extract, refused))}
    row = run.Row(arm="web-router:nimble", repeat=0, index=0, question="q", gold="a", reasoning_types=None)

    await run._answer(client, "session", pending, row)

    assert (row.searches, row.extract_urls, row.providers) == (1, 2, ["nimble", "nimble"])


def test_the_report_splits_web_router_calls_and_omits_the_split_for_older_runs(
    bench: tuple[ModuleType, ModuleType],
) -> None:
    report, _ = bench
    arms = ["claude-native", "web-router:nimble"]
    rows = [
        _row("claude-native", 0, "correct"),
        _row("web-router:nimble", 0, "correct", providers=["nimble"] * 3, searches=2, extract_urls=4),
    ]

    summary, markdown = report.build(rows, {**SETUP, "arms": arms})
    assert (
        summary["arms"]["web-router:nimble"]["searches_total"],
        summary["arms"]["web-router:nimble"]["extract_urls_total"],
    ) == (2, 4)
    assert "nimble (3 calls) -- 2 searches, 4 URLs extracted" in markdown

    older = [{k: v for k, v in r.items() if k not in ("searches", "extract_urls")} for r in rows]
    _, markdown = report.build(older, {**SETUP, "arms": arms})
    assert "nimble (3 calls)\n" in markdown and "URLs extracted" not in markdown


def _item(kind: str, **fields: Any) -> SimpleNamespace:
    return SimpleNamespace(type=kind, **fields)


def _response(rid: str, output: list[SimpleNamespace], text: str = "") -> SimpleNamespace:
    usage = SimpleNamespace(input_tokens=100, output_tokens=10, input_tokens_details=SimpleNamespace(cached_tokens=40))
    return SimpleNamespace(
        id=rid, status="completed", incomplete_details=None, output=output, output_text=text, usage=usage
    )


class FakeResponses:
    """Hands back scripted responses in order and keeps every request it was sent."""

    def __init__(self, *responses: SimpleNamespace) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    async def create(self, **request: Any) -> SimpleNamespace:
        self.requests.append(request)
        return self.responses.pop(0)


def _args(**overrides: Any) -> SimpleNamespace:
    return SimpleNamespace(**{"models": {"openai": "gpt-5"}, "timeout": 60.0, "max_turns": 30, **overrides})


async def test_an_openai_run_executes_web_router_calls_here_and_chains_the_responses(
    bench: tuple[ModuleType, ModuleType],
) -> None:
    _, run = bench
    call = _item("function_call", name="web_router_search", arguments='{"query": "q"}', call_id="c1")
    responses = FakeResponses(_response("r1", [call]), _response("r2", [_item("message")], text=" Paris "))
    executor = SimpleNamespace(acall=None)
    result = SimpleNamespace(
        content="rendered",
        is_error=False,
        provider="nimble",
        response=SearchResponse(provider="nimble", query="q", results=[]),
        function_call_output=lambda call_id: {"type": "function_call_output", "call_id": call_id, "output": "x"},
    )

    async def acall(name: str, arguments: str) -> SimpleNamespace:
        assert (name, arguments) == ("web_router_search", '{"query": "q"}')
        return result

    executor.acall = acall
    arm = run.Arm("web-router:nimble", "system", [], executor)
    row = run.Row(arm=arm.name, repeat=0, index=0, question="q?", gold="Paris", reasoning_types=None)

    await run.run_response(SimpleNamespace(responses=responses), arm, row, _args())

    assert row.answer == "Paris" and row.error is None
    assert (row.model_calls, row.tool_calls, row.searches, row.input_tokens) == (2, 1, 1, 200)
    assert row.cached_input_tokens == 80
    assert row.tool_chars == len("rendered") and row.providers == ["nimble"]
    assert responses.requests[1]["previous_response_id"] == "r1"
    assert responses.requests[1]["input"] == [{"type": "function_call_output", "call_id": "c1", "output": "x"}]


async def test_openai_native_counts_its_own_searches_and_has_no_tool_chars(
    bench: tuple[ModuleType, ModuleType],
) -> None:
    _, run = bench
    output = [
        _item("web_search_call", action=SimpleNamespace(type="search")),
        _item("web_search_call", action=SimpleNamespace(type="open_page")),
        _item("message"),
    ]
    arm = run.build_arm("openai-native", None, 5, run.OPENAI)
    row = run.Row(arm=arm.name, repeat=0, index=0, question="q?", gold="a", reasoning_types=None)

    responses = FakeResponses(_response("r1", output, text="a"))
    await run.run_response(SimpleNamespace(responses=responses), arm, row, _args())

    assert responses.requests[0]["tools"] == [{"type": "web_search"}]
    assert (row.tool_calls, row.web_searches, row.tool_chars, row.answer) == (2, 1, None, "a")


async def test_an_openai_run_that_keeps_calling_tools_stops_at_max_turns(bench: tuple[ModuleType, ModuleType]) -> None:
    _, run = bench
    call = _item("function_call", name="web_router_search", arguments="{}", call_id="c")
    result = SimpleNamespace(
        content="x", is_error=True, provider=None, response=None, function_call_output=lambda call_id: {}
    )

    async def acall(name: str, arguments: str) -> SimpleNamespace:
        return result

    arm = run.Arm("web-router:nimble", "system", [], SimpleNamespace(acall=acall))
    row = run.Row(arm=arm.name, repeat=0, index=0, question="q?", gold="a", reasoning_types=None)
    responses = FakeResponses(*(_response(f"r{i}", [call]) for i in range(5)))

    await run.run_response(SimpleNamespace(responses=responses), arm, row, _args(max_turns=2))

    assert row.failed_at == "agent" and "--max-turns" in (row.error or "")
    assert len(responses.requests) == 2


def test_each_platform_brings_its_own_arms_model_and_an_outside_judge(bench: tuple[ModuleType, ModuleType]) -> None:
    _, run = bench
    claude, openai = run.parse_args([]), run.parse_args(["--platform", "openai"])

    assert claude.arms.startswith("claude-native,") and claude.models == {"claude": "claude-opus-5"}
    assert openai.arms.startswith("openai-native,") and openai.models == {"openai": "gpt-5"}
    assert run.judge_for("claude").label.startswith("openai:")
    assert run.judge_for("openai").label.startswith("anthropic:")


def test_an_openai_report_names_its_baseline_and_states_no_cost(bench: tuple[ModuleType, ModuleType]) -> None:
    report, _ = bench
    arms = ["openai-native", "web-router:nimble"]
    rows = [_row("openai-native", 0, "correct", tool_chars=None), _row("web-router:nimble", 0, "correct")]
    setup = {**SETUP, "arms": arms, "platform": "openai", "surface": "OpenAI Responses API", "max_turns": 30}
    setup.pop("session_budget_usd")

    summary, markdown = report.build(rows, setup)

    assert summary["baseline"] == "openai-native"
    assert summary["arms"]["openai-native"]["tool_chars_p50"] is None
    assert "the same OpenAI agent" in markdown and "30 requests per run" in markdown
    assert "OpenAI's built-in `web_search`, run by OpenAI (baseline)" in markdown
    assert "Anthropic" not in markdown


def test_the_judge_can_be_chosen_and_one_from_the_agents_family_is_named_as_such(
    bench: tuple[ModuleType, ModuleType],
) -> None:
    report, run = bench
    args = run.parse_args(["--judge", "anthropic", "--judge-model", "claude-opus-5-5"])

    judge = run.judge_for(args.platforms, args.judge, args.judge_model)
    assert judge.label == "anthropic:claude-opus-5-5"
    assert run.same_family(args.platforms[0], args.judge) is True
    assert run.same_family("claude", None) is False and run.same_family("openai", None) is False

    rows = [_row("claude-native", 0, "correct")]
    setup = {**SETUP, "arms": ["claude-native"], "judge": judge.label, "judge_same_family": True}
    _, markdown = report.build(rows, setup)
    assert "same model family as the agents" in markdown
    _, markdown = report.build(rows, {**setup, "judge_same_family": False})
    assert "same model family" not in markdown


def test_a_run_of_both_platforms_puts_each_arm_on_its_platform(bench: tuple[ModuleType, ModuleType]) -> None:
    _, run = bench
    args = run.parse_args(["--platform", "both", "--model", "openai=gpt-5-mini"])

    assert args.platforms == ["claude", "openai"]
    assert args.models == {"claude": "claude-opus-5", "openai": "gpt-5-mini"}
    arms = run.resolve_arms("claude-native,openai-native,web-router:nimble,claude/web-router:exa", args.platforms)
    assert arms == [
        ("claude-native", "claude", "claude-native"),
        ("openai-native", "openai", "openai-native"),
        ("claude/web-router:nimble", "claude", "web-router:nimble"),
        ("openai/web-router:nimble", "openai", "web-router:nimble"),
        ("claude/web-router:exa", "claude", "web-router:exa"),
    ]
    # One platform keeps the names older runs used, so their agents and reports carry over.
    assert run.resolve_arms("claude-native,web-router:nimble", ["claude"])[1] == (
        "web-router:nimble",
        "claude",
        "web-router:nimble",
    )


def test_a_run_of_both_platforms_refuses_what_it_cannot_run_before_anything_is_paid_for(
    bench: tuple[ModuleType, ModuleType],
) -> None:
    _, run = bench
    with pytest.raises(SystemExit, match="one model for a run of one platform"):
        run.parse_args(["--platform", "claude,openai", "--model", "gpt-5"])
    with pytest.raises(SystemExit):
        run.parse_args(["--platform", "gemini"])
    with pytest.raises(SystemExit, match="does not include"):
        run.resolve_arms("openai-native", ["claude"])
    with pytest.raises(SystemExit, match="listed twice"):
        run.resolve_arms("web-router:nimble,claude/web-router:nimble", ["claude", "openai"])


def test_a_run_of_both_platforms_has_one_judge_and_records_whose_family_it_is(
    bench: tuple[ModuleType, ModuleType],
) -> None:
    _, run = bench
    both = run.parse_args(["--platform", "both"])
    setup = run.run_setup(both, ["claude-native", "openai-native"])

    assert setup["judge"].startswith("openai:") and setup["judge_same_family"] == ["openai"]
    assert setup["platform_setup"]["claude"]["session_budget_usd"] == 2.0
    assert setup["platform_setup"]["openai"]["max_turns"] == 30
    assert "platform" not in setup
    one = run.run_setup(run.parse_args([]), ["claude-native"])
    assert (one["platform"], one["model"], one["judge_same_family"]) == ("claude", "claude-opus-5", False)


BOTH_SETUP: dict[str, Any] = {
    **{k: v for k, v in SETUP.items() if k not in ("model", "surface", "session_budget_usd")},
    "platforms": ["claude", "openai"],
    "platform_setup": {
        "claude": {"model": "claude-opus-5", "surface": "Claude Managed Agents", "session_budget_usd": 2.0},
        "openai": {"model": "gpt-5", "surface": "OpenAI Responses API", "max_turns": 30},
    },
    "judge_same_family": ["openai"],
}


def _both_rows(questions: int) -> list[dict[str, Any]]:
    """Claude's web-router arm right on every question where native is right on none; OpenAI's two identical."""
    rows = []
    for i in range(questions):
        rows += [
            _row("claude-native", i, "incorrect", input_tokens=300, platform="claude"),
            _row("claude/web-router:nimble", i, "correct", input_tokens=100, platform="claude"),
            _row("openai-native", i, "correct", tool_chars=None, platform="openai"),
            _row("openai/web-router:nimble", i, "correct", platform="openai"),
        ]
    return rows


def test_each_arm_is_compared_with_its_own_platforms_native_and_winners_carry_a_verdict(
    bench: tuple[ModuleType, ModuleType],
) -> None:
    report, _ = bench
    arms = ["claude-native", "claude/web-router:nimble", "openai-native", "openai/web-router:nimble"]

    summary, markdown = report.build(_both_rows(20), {**BOTH_SETUP, "arms": arms})

    assert summary["baselines"] == {"claude": "claude-native", "openai": "openai-native"}
    assert summary["vs_baseline"]["claude/web-router:nimble"]["baseline"] == "claude-native"
    assert summary["vs_baseline"]["openai/web-router:nimble"]["baseline"] == "openai-native"
    winners = {(w["platform"], w["axis"]): w for w in summary["winners"]}
    claude_accuracy = winners[("claude", "accuracy")]
    assert claude_accuracy["winner"] == "claude/web-router:nimble"
    assert claude_accuracy["vs_native"]["margin"] == 100 and claude_accuracy["vs_native"]["verdict"] == "clear"
    assert winners[("claude", "input_tokens_p50")]["vs_native"]["margin"] == pytest.approx(-2 / 3)
    assert winners[("openai", "accuracy")]["runner_up"]["verdict"] == "within noise"
    # No OpenAI arm states a cost, and OpenAI's native search shows no tool chars: neither is ranked on a zero.
    assert ("openai", "list_cost_p50") not in winners
    assert winners[("openai", "tool_chars_p50")]["winner"] == "openai/web-router:nimble"
    assert "n/a (native has no figure)" in markdown
    assert "## Winners by axis" in markdown and "same model family as the OpenAI agents" in markdown


def test_a_margin_on_a_handful_of_questions_gets_no_verdict(bench: tuple[ModuleType, ModuleType]) -> None:
    report, _ = bench
    arms = ["claude-native", "claude/web-router:nimble", "openai-native", "openai/web-router:nimble"]

    summary, _ = report.build(_both_rows(3), {**BOTH_SETUP, "arms": arms})

    accuracy = next(w for w in summary["winners"] if (w["platform"], w["axis"]) == ("claude", "accuracy"))
    assert accuracy["vs_native"]["verdict"] == "too few runs"


def _write_run(folder: Path, setup: dict[str, Any], rows: list[dict[str, Any]]) -> Path:
    folder.mkdir()
    (folder / "run.json").write_text(json.dumps(setup), encoding="utf-8")
    (folder / "rows.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return folder


OPENAI_SETUP: dict[str, Any] = {
    **{k: v for k, v in SETUP.items() if k != "session_budget_usd"},
    "platform": "openai",
    "model": "gpt-5",
    "surface": "OpenAI Responses API",
    "max_turns": 30,
}


def _full(arm: str, *grades: str, **extra: Any) -> list[dict[str, Any]]:
    """One row per question of SETUP's two, the run an arm makes when it finishes."""
    return [_row(arm, index, grade, **extra) for index, grade in enumerate(grades)]


def test_combine_merges_a_claude_run_and_an_openai_run_into_one_report(
    bench: tuple[ModuleType, ModuleType], tmp_path: Path
) -> None:
    report, _ = bench
    claude = _write_run(
        tmp_path / "claude",
        {**SETUP, "arms": ["claude-native", "web-router:nimble"], "judge_same_family": False},
        _full("claude-native", "correct", "correct") + _full("web-router:nimble", "correct", "correct"),
    )
    openai = _write_run(
        tmp_path / "openai",
        {**OPENAI_SETUP, "arms": ["openai-native", "web-router:nimble"], "judge_same_family": True},
        _full("openai-native", "correct", "correct") + _full("web-router:nimble", "incorrect", "correct"),
    )

    report.main(["--combine", str(claude), str(openai), "--out", str(tmp_path / "both")])

    summary = json.loads((tmp_path / "both" / "summary.json").read_text(encoding="utf-8"))
    assert list(summary["arms"]) == [
        "claude-native",
        "claude/web-router:nimble",
        "openai-native",
        "openai/web-router:nimble",
    ]
    assert summary["arms"]["openai/web-router:nimble"]["incorrect"] == 1
    assert summary["setup"]["judge_same_family"] == ["openai"]
    assert summary["setup"]["platform_setup"]["openai"]["max_turns"] == 30


def test_combine_refuses_runs_that_would_not_compare(bench: tuple[ModuleType, ModuleType], tmp_path: Path) -> None:
    report, _ = bench
    first = _write_run(
        tmp_path / "a", {**SETUP, "arms": ["claude-native"]}, _full("claude-native", "correct", "correct")
    )
    other = {**OPENAI_SETUP, "arms": ["openai-native"]}
    full = _full("openai-native", "correct", "correct")
    refused = {
        "judge differs": ({**other, "judge": "anthropic:claude-sonnet-5-5"}, full),
        "different questions": (other, [{**full[0], "question": "another"}, full[1]]),
        "different answers": (other, [{**full[0], "gold": "another answer"}, full[1]]),
        # An OpenAI run stopped after one of its two questions: merged, it would be compared as if whole.
        "1 of the 2 runs": (other, full[:1]),
    }

    for n, (reason, (setup, rows)) in enumerate(refused.items()):
        second = _write_run(tmp_path / f"b{n}", setup, rows)
        with pytest.raises(SystemExit, match=reason):
            report.main(["--combine", str(first), str(second), "--out", str(tmp_path / f"out{n}")])


def test_combine_refuses_one_arm_name_sent_two_different_chains(
    bench: tuple[ModuleType, ModuleType], tmp_path: Path
) -> None:
    """auto-shipped kept its name when auto's default order changed; runs from either side must not merge as one."""
    report, _ = bench
    arm = "web-router:auto-shipped"

    def chain(*names: str) -> dict[str, Any]:
        return {"search": {"provider": "auto", "chain": [{"provider": n, "call": {}} for n in names]}}

    old = {arm: chain("nimble", "tavily", "you")}
    new = {arm: chain("perplexity", "nimble", "linkup")}

    def runs(tag: str, claude_chain: dict[str, Any], openai_chain: dict[str, Any]) -> tuple[Path, Path]:
        claude = _write_run(
            tmp_path / f"{tag}-claude",
            {**SETUP, "arms": [arm], "backends": claude_chain},
            _full(arm, "correct", "correct"),
        )
        openai = _write_run(
            tmp_path / f"{tag}-openai",
            {**OPENAI_SETUP, "arms": [arm], "backends": openai_chain},
            _full(arm, "correct", "correct"),
        )
        return claude, openai

    with pytest.raises(SystemExit, match=r"backends\[web-router:auto-shipped\] differs"):
        report.main(["--combine", *map(str, runs("mixed", old, new)), "--out", str(tmp_path / "mixed")])

    report.main(["--combine", *map(str, runs("same", new, new)), "--out", str(tmp_path / "same")])
    summary = json.loads((tmp_path / "same" / "summary.json").read_text(encoding="utf-8"))
    assert summary["setup"]["backends"] == new


async def test_each_arm_records_the_request_its_backends_were_sent(
    bench: tuple[ModuleType, ModuleType], monkeypatch: pytest.MonkeyPatch
) -> None:
    report, run = bench
    monkeypatch.setenv("LINKUP_API_KEY", "test-key")
    monkeypatch.setenv("PERPLEXITY_API_KEY", "test-key")
    from web_router import AsyncWebRouter

    async with AsyncWebRouter() as router:
        linkup = run.backend_config(run.build_arm("web-router:linkup", router, 5, run.OPENAI).executor)
        perplexity = run.backend_config(run.build_arm("web-router:perplexity", router, 5, run.OPENAI).executor)

    assert linkup["search"]["endpoint"] == "https://api.linkup.so/v1/search"
    body = linkup["search"]["call"]["json"]
    assert body["maxResults"] == 5 and body["q"] == run.QUERY_PLACEHOLDER
    assert linkup["extract"]["provider"] == "linkup"
    # Perplexity reads no pages, so the extract tool calls auto, which pins markdown for every member.
    assert perplexity["extract"]["provider"] == "auto"
    tavily = next(m for m in perplexity["extract"]["chain"] if m["provider"] == "tavily")  # keyless, always there
    assert tavily["call"]["sdk"]["format"] == "markdown"

    rows = [_row("openai-native", 0, "correct", tool_chars=None), _row("web-router:linkup", 0, "correct")]
    setup = {**SETUP, "arms": ["openai-native", "web-router:linkup"], "platform": "openai", "backends": {}}
    setup["backends"]["web-router:linkup"] = linkup
    _, markdown = report.build(rows, setup)
    assert "### What each backend was sent" in markdown and '"depth": "fast"' in markdown


def _winner(summary: dict[str, Any], axis: str, platform: str = "claude") -> dict[str, Any]:
    return next(w for w in summary["winners"] if (w["platform"], w["axis"]) == (platform, axis))


def test_a_margin_is_measured_on_the_same_runs_as_its_verdict(bench: tuple[ModuleType, ModuleType]) -> None:
    """Nimble wins overall (20/30 against 16/25); on the 25 questions both answered, native was ahead (16 to 15).

    Native's 5 failed runs stay under the share that would flag it as an outage.
    """
    report, _ = bench
    arms = ["claude-native", "web-router:nimble"]
    rows = [_row("web-router:nimble", i, "incorrect" if i < 10 else "correct") for i in range(30)]
    rows += [_row("claude-native", i, "correct" if i < 16 else "incorrect") for i in range(25)]
    rows += [{**_row("claude-native", i, "incorrect"), "error": "timeout", "failed_at": "agent"} for i in range(25, 30)]

    summary, _ = report.build(rows, {**SETUP, "arms": arms})

    vs = _winner(summary, "accuracy")["vs_native"]
    low, high = vs["margin_ci95"]
    assert low <= vs["margin"] <= high
    assert not (vs["verdict"] == "clear" and (vs["margin"] > 0) != (low > 0))
    # Control: native serving every question, right on five of them, is clearly behind.
    control = [r for r in rows if r["arm"] == "web-router:nimble"]
    control += [_row("claude-native", i, "correct" if i < 10 and i % 2 else "incorrect") for i in range(30)]
    summary, _ = report.build(control, {**SETUP, "arms": arms})
    assert _winner(summary, "accuracy")["vs_native"]["verdict"] == "clear"


def test_repeats_of_a_question_do_not_narrow_the_interval(bench: tuple[ModuleType, ModuleType]) -> None:
    """A repeat that says what the first run said is not new evidence."""
    report, _ = bench
    arms = ["claude-native", "web-router:nimble"]

    def run_of(repeats: int) -> dict[str, Any]:
        rows = []
        for i in range(40):
            native, nimble = (
                ("incorrect", "correct") if i < 14 else ("correct", "incorrect") if i < 20 else ("correct",) * 2
            )
            for repeat in range(repeats):
                rows += [
                    _row("claude-native", i, native, repeat=repeat),
                    _row("web-router:nimble", i, nimble, repeat=repeat),
                ]
        summary, _ = report.build(rows, {**SETUP, "arms": arms, "repeats": repeats})
        return _winner(summary, "accuracy")["vs_native"]

    once, five = run_of(1), run_of(5)
    assert once["margin"] == pytest.approx(20) and once["verdict"] == five["verdict"] == "within noise"
    assert once["margin_ci95"] == pytest.approx(five["margin_ci95"], abs=1)


def test_no_arm_is_called_native_when_none_ran(bench: tuple[ModuleType, ModuleType]) -> None:
    report, _ = bench
    page = sys.modules.get("page") or __import__("page")
    arms = ["web-router:exa", "web-router:nimble"]
    rows = [
        _row(arm, i, "correct" if arm.endswith("nimble") or i % 2 else "incorrect") for arm in arms for i in range(10)
    ]

    summary, markdown = report.build(rows, {**SETUP, "arms": arms})

    winners = markdown.split("## Winners by axis")[1].split("## Results")[0]
    assert "native is best" not in winners and "no native arm in this run" in winners
    assert all(w["native"] is None and w["vs_native"] is None for w in summary["winners"])
    html = page.render(summary)
    assert "native is best" not in html.lower() and "no native arm in this run" in html


def test_two_arms_both_at_zero_are_tied_not_short_of_runs(bench: tuple[ModuleType, ModuleType]) -> None:
    report, _ = bench
    arms = ["claude-native", "web-router:nimble"]
    rows = [_row(arm, i, "correct", tool_chars=0) for arm in arms for i in range(30)]

    summary, _ = report.build(rows, {**SETUP, "arms": arms})

    assert _winner(summary, "tool_chars_p50")["runner_up"]["verdict"] == "tied"


async def test_the_record_is_the_whole_call_and_carries_no_credential(
    bench: tuple[ModuleType, ModuleType], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, run = bench
    monkeypatch.setenv("JINA_API_KEY", "jina-test-secret")
    from web_router import AsyncWebRouter

    async with AsyncWebRouter() as router:
        config = run.backend_config(run.build_arm("web-router:you-mcp", router, 5, run.CLAUDE).executor)

    envelope = config["search"]["call"]["json"]
    assert envelope["jsonrpc"] == "2.0" and envelope["method"] == "tools/call"
    jina = next(m for m in config["extract"]["chain"] if m["provider"] == "jina")
    assert "headers" in jina["call"]
    assert "jina-test-secret" not in json.dumps(config)


def test_an_arm_whose_tools_failed_is_flagged_and_never_wins(bench: tuple[ModuleType, ModuleType]) -> None:
    """A backend whose key stopped working: every search errored, and its figures look leaner than anyone's."""
    report, _ = bench
    page = sys.modules.get("page") or __import__("page")
    arms = ["claude-native", "web-router:nimble", "web-router:webiq"]
    rows = [_row("claude-native", i, "correct" if i % 4 else "incorrect", input_tokens=300) for i in range(20)]
    rows += [_row("web-router:nimble", i, "correct" if i % 4 else "incorrect", searches=2) for i in range(20)]
    rows += [_row("web-router:webiq", i, "correct", input_tokens=10, tool_errors=3, searches=0) for i in range(20)]

    summary, markdown = report.build(rows, {**SETUP, "arms": arms})

    assert [a["arm"] for a in summary["alerts"]] == ["web-router:webiq"]
    assert "no search succeeded in any of its 20 runs" in summary["alerts"][0]["reason"]
    assert all(w["winner"] != "web-router:webiq" for w in summary["winners"])
    assert "## ⚠️ Arms whose tools or runs failed" in markdown and "web-router:webiq" not in summary["headline"][0]
    assert "Arms whose tools or runs failed" in page.render(summary)
    # A healthy run raises nothing.
    healthy, _ = report.build([r for r in rows if r["arm"] != "web-router:webiq"], {**SETUP, "arms": arms[:2]})
    assert healthy["alerts"] == []


def test_the_run_warns_while_an_arms_tools_keep_failing(
    bench: tuple[ModuleType, ModuleType], capsys: pytest.CaptureFixture[str]
) -> None:
    _, run = bench
    failing: dict[str, int] = __import__("collections").defaultdict(int)
    warned: set[str] = set()

    def row(**counts: int) -> Any:
        r = run.Row(arm="claude/web-router:webiq", repeat=0, index=0, question="q", gold="a", reasoning_types=None)
        r.tool_errors, r.searches = counts.get("errors", 0), counts.get("searches", 0)
        return r

    for _ in range(4):
        run._watch_tools(failing, warned, row(errors=2))
    assert "!!" not in capsys.readouterr().out
    run._watch_tools(failing, warned, row(errors=3))
    run._watch_tools(failing, warned, row(errors=3))  # said once, not on every later run
    assert capsys.readouterr().out.count("claude/web-router:webiq: 5 runs in a row") == 1
    run._watch_tools(failing, warned, row(errors=1, searches=2))
    assert failing["claude/web-router:webiq"] == 0


KEY = "pplx-test-key"


def test_an_mcp_arm_attaches_the_vendors_search_tool_alone_and_holds_no_key(
    bench: tuple[ModuleType, ModuleType], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, run = bench
    monkeypatch.setenv("PERPLEXITY_API_KEY", KEY)
    claude = run.build_arm("mcp:perplexity", None, 5, run.CLAUDE)
    openai = run.build_arm("openai/mcp:perplexity", None, 5, run.OPENAI, "mcp:perplexity")

    (toolset,) = claude.tools
    assert toolset["type"] == "mcp_toolset" and toolset["default_config"]["enabled"] is False
    assert [c["name"] for c in toolset["configs"] if c["enabled"]] == ["perplexity_search"]
    assert claude.mcp_servers == [{"type": "url", "name": "perplexity", "url": "https://api.perplexity.ai/mcp"}]
    assert openai.tools[0]["allowed_tools"] == ["perplexity_search"] and openai.mcp_servers == []
    assert claude.executor is None and claude.system == run.TASK_PROMPT
    # The key is added where it is sent, so nothing the arm keeps -- or its agent's fingerprint -- carries it.
    assert KEY not in json.dumps([claude.tools, claude.mcp_servers, openai.tools]) + claude.fingerprint
    assert run.request_tools(openai)[0]["headers"] == {"Authorization": f"Bearer {KEY}"}


def test_an_mcp_arm_without_its_key_or_vendor_is_refused_before_anything_runs(
    bench: tuple[ModuleType, ModuleType], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, run = bench
    monkeypatch.delenv("PERPLEXITY_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="PERPLEXITY_API_KEY"):
        run.build_arm("mcp:perplexity", None, 5, run.CLAUDE)
    with pytest.raises(SystemExit, match="unknown MCP vendor 'acme'"):
        run.build_arm("mcp:acme", None, 5, run.CLAUDE)


class _Recorder:
    """A Managed Agents client that records every call and answers with ids."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

        def method(name: str, answer: dict[str, Any] | None = None) -> Any:
            async def call(*args: Any, **kwargs: Any) -> SimpleNamespace:
                self.calls.append((name, args, kwargs))
                return SimpleNamespace(**(answer or {}))

            return call

        self.beta = SimpleNamespace(
            environments=SimpleNamespace(create=method("env.create", {"id": "env_1"}), update=method("env.update")),
            vaults=SimpleNamespace(
                create=method("vault.create", {"id": "vlt_1"}),
                credentials=SimpleNamespace(
                    create=method("cred.create", {"id": "cred_1"}),
                    update=method("cred.update"),
                    delete=method("cred.delete"),
                ),
            ),
            agents=SimpleNamespace(create=method("agent.create", {"id": "agt_1", "version": 1})),
        )

    def named(self, name: str) -> list[tuple[tuple[Any, ...], dict[str, Any]]]:
        return [(args, kwargs) for n, args, kwargs in self.calls if n == name]


async def test_a_claude_mcp_arm_gets_a_vault_for_its_key_and_the_state_file_never_holds_it(
    bench: tuple[ModuleType, ModuleType], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, run = bench
    state = tmp_path / "state.json"
    monkeypatch.setattr(run, "STATE_FILE", state)
    monkeypatch.setenv("PERPLEXITY_API_KEY", KEY)
    arm = run.build_arm("mcp:perplexity", None, 5, run.CLAUDE)
    client = _Recorder()

    environment, _, vaults = await run.ensure_setup(client, [arm], "claude-opus-5")

    assert (environment, vaults) == ("env_1", {"perplexity": "vlt_1"})
    ((_, credential),) = client.named("cred.create")
    assert credential["auth"] == {
        "type": "static_bearer",
        "token": KEY,
        "mcp_server_url": run.MCP_SERVERS["perplexity"].url,
    }
    ((_, agent),) = client.named("agent.create")
    assert agent["mcp_servers"] == arm.mcp_servers
    assert KEY not in state.read_text()

    # The next run reuses the vault and sets its token again, so a rotated key reaches it.
    monkeypatch.setenv("PERPLEXITY_API_KEY", "pplx-rotated")
    await run.ensure_setup(client, [arm], "claude-opus-5")
    assert len(client.named("vault.create")) == 1
    ((args, update),) = client.named("cred.update")
    assert args == ("cred_1",) and update["auth"]["token"] == "pplx-rotated"
    assert "pplx-rotated" not in state.read_text()


async def test_an_environment_from_before_mcp_arms_is_opened_to_their_servers(
    bench: tuple[ModuleType, ModuleType], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, run = bench
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"environment_id": "env_old", "agents": {}}))
    monkeypatch.setattr(run, "STATE_FILE", state)
    client = _Recorder()

    await run.ensure_setup(client, [], "claude-opus-5")
    await run.ensure_setup(client, [], "claude-opus-5")

    ((args, update),) = client.named("env.update")  # once: the config is recorded after it
    assert args == ("env_old",) and update["config"]["networking"]["allow_mcp_servers"] is True


def _use(use_id: str, server: str, tool: str, arguments: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(type="agent.mcp_tool_use", id=use_id, mcp_server_name=server, name=tool, input=arguments)


class _Stream:
    def __init__(self, events: list[SimpleNamespace]) -> None:
        self.events = events

    async def __aenter__(self) -> _Stream:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    def __aiter__(self) -> Any:
        async def events() -> Any:
            for event in self.events:
                yield event

        return events()


async def test_a_claude_mcp_arm_counts_the_platforms_calls_to_the_vendor(
    bench: tuple[ModuleType, ModuleType], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, run = bench
    monkeypatch.setenv("PERPLEXITY_API_KEY", KEY)
    arm = run.build_arm("mcp:perplexity", None, 5, run.CLAUDE)
    text = SimpleNamespace(type="text", text="ten results")
    events = [
        _use("u1", "perplexity", "perplexity_search", {"query": "q"}),
        SimpleNamespace(type="agent.mcp_tool_result", mcp_tool_use_id="u1", content=[text], is_error=False),
        _use("u2", "perplexity", "perplexity_search", {"query": "q"}),
        SimpleNamespace(type="agent.mcp_tool_result", mcp_tool_use_id="u2", content=[], is_error=True),
        SimpleNamespace(type="agent.message", content=[SimpleNamespace(type="text", text="Paris")]),
        SimpleNamespace(type="session.status_idle", stop_reason=SimpleNamespace(type="end_turn")),
    ]

    async def stream(session_id: str) -> _Stream:
        return _Stream(events)

    async def send(session_id: str, events: list[dict[str, Any]]) -> None:
        pass

    client = SimpleNamespace(
        beta=SimpleNamespace(sessions=SimpleNamespace(events=SimpleNamespace(stream=stream, send=send)))
    )
    row = run.Row(arm=arm.name, repeat=0, index=0, question="q?", gold="Paris", reasoning_types=None)

    await run._drive(client, arm, "session", row)

    assert (row.tool_calls, row.searches, row.tool_errors) == (2, 1, 1)
    assert (row.tool_chars, row.providers, row.answer) == (len("ten results"), ["perplexity"], "Paris")


async def test_an_openai_mcp_arm_sends_the_key_as_a_header_and_counts_the_calls_openai_ran(
    bench: tuple[ModuleType, ModuleType], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, run = bench
    monkeypatch.setenv("PERPLEXITY_API_KEY", KEY)
    arm = run.build_arm("mcp:perplexity", None, 5, run.OPENAI)
    output = [
        _item("mcp_list_tools", error=None),
        _item(
            "mcp_call",
            server_label="perplexity",
            name="perplexity_search",
            arguments="{}",
            output="ten results",
            error=None,
        ),
        _item(
            "mcp_call", server_label="perplexity", name="perplexity_search", arguments="{}", output=None, error="500"
        ),
        _item("message"),
    ]
    responses = FakeResponses(_response("r1", output, text="Paris"))
    row = run.Row(arm=arm.name, repeat=0, index=0, question="q?", gold="Paris", reasoning_types=None)

    await run.run_response(SimpleNamespace(responses=responses), arm, row, _args())

    assert responses.requests[0]["tools"][0]["headers"] == {"Authorization": f"Bearer {KEY}"}
    assert (row.model_calls, row.tool_calls, row.searches, row.tool_errors) == (1, 2, 1, 1)
    assert (row.tool_chars, row.providers, row.answer) == (len("ten results"), ["perplexity"], "Paris")


def test_the_report_names_an_mcp_arm_and_flags_it_when_its_tools_fail(bench: tuple[ModuleType, ModuleType]) -> None:
    report, _ = bench
    arms = ["claude-native", "mcp:perplexity"]
    rows = [_row("claude-native", i, "correct") for i in range(20)]
    rows += [_row("mcp:perplexity", i, "correct", tool_errors=2, searches=0) for i in range(20)]

    summary, markdown = report.build(rows, {**SETUP, "arms": arms})

    assert "`perplexity`'s own hosted MCP server" in markdown
    assert "An `mcp:` arm is the vendor as shipped" in markdown
    assert [a["arm"] for a in summary["alerts"]] == ["mcp:perplexity"]


def test_the_report_says_which_mcp_arms_could_read_pages(bench: tuple[ModuleType, ModuleType]) -> None:
    """Perplexity's server has no page-read tool; the others have one, and the report says so from what ran."""
    report, run = bench
    specs = {"mcp:perplexity": ("perplexity",), "mcp:exa": ("exa",), "mcp:all": run.MCP_SETS["all"]}
    backends = {arm: run.mcp_config(SimpleNamespace(mcp_vendors=vendors)) for arm, vendors in specs.items()}
    arms = ["claude-native", *specs]
    rows = [_row(arm, i, "correct") for arm in arms for i in range(5)]

    _, markdown = report.build(rows, {**SETUP, "arms": arms, "backends": backends})

    described = {
        arm: line
        for line in markdown.splitlines()
        for arm in specs
        if line.startswith(f"| `{arm}` |") and "hosted MCP server" in line
    }
    assert "its search tool only, no page reads" in described["mcp:perplexity"]
    assert "search and page-read tools" in described["mcp:exa"]
    assert "page-read tools where the vendor has one (not perplexity)" in described["mcp:all"]
    assert "no page-read tool" not in markdown


async def test_a_vault_whose_server_moved_gets_a_credential_for_the_new_url(
    bench: tuple[ModuleType, ModuleType], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A credential's URL is fixed, and one left on the old URL would send the new server no token."""
    _, run = bench
    state = tmp_path / "state.json"
    old = {"id": "vlt_1", "credential_id": "cred_old", "url": "https://old.example/mcp"}
    state.write_text(
        json.dumps(
            {"environment_id": "env_1", "environment_config": run.ENVIRONMENT_CONFIG, "vaults": {"perplexity": old}}
        )
    )
    monkeypatch.setattr(run, "STATE_FILE", state)
    monkeypatch.setenv("PERPLEXITY_API_KEY", KEY)
    client = _Recorder()

    _, _, vaults = await run.ensure_setup(client, [run.build_arm("mcp:perplexity", None, 5, run.CLAUDE)], "m")

    assert vaults == {"perplexity": "vlt_1"} and not client.named("vault.create") and not client.named("cred.update")
    ((args, created),) = client.named("cred.create")
    assert args == ("vlt_1",) and created["auth"]["mcp_server_url"] == "https://api.perplexity.ai/mcp"
    ((args, deleted),) = client.named("cred.delete")
    assert args == ("cred_old",) and deleted == {"vault_id": "vlt_1"}
    assert json.loads(state.read_text())["vaults"]["perplexity"]["url"] == "https://api.perplexity.ai/mcp"


async def test_a_vault_is_recorded_even_when_an_agent_then_fails_to_be_created(
    bench: tuple[ModuleType, ModuleType], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, run = bench
    state = tmp_path / "state.json"
    monkeypatch.setattr(run, "STATE_FILE", state)
    monkeypatch.setenv("PERPLEXITY_API_KEY", KEY)
    client = _Recorder()

    async def refuse(**_: Any) -> None:
        raise RuntimeError("agent create refused")

    client.beta.agents.create = refuse
    with pytest.raises(RuntimeError):
        await run.ensure_setup(client, [run.build_arm("mcp:perplexity", None, 5, run.CLAUDE)], "m")

    assert json.loads(state.read_text())["vaults"]["perplexity"]["id"] == "vlt_1"


async def test_an_mcp_server_that_was_never_reached_flags_the_arm_on_either_platform(
    bench: tuple[ModuleType, ModuleType], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bad key or a down server: no MCP call ever runs, and the run must still count a failure."""
    _, run = bench
    monkeypatch.setenv("PERPLEXITY_API_KEY", KEY)
    claude = run.build_arm("mcp:perplexity", None, 5, run.CLAUDE)
    events = [
        SimpleNamespace(type="session.error", error=SimpleNamespace(type="mcp_authentication_failed_error")),
        SimpleNamespace(type="session.error", error=SimpleNamespace(type="model_overloaded_error")),
        SimpleNamespace(type="session.status_idle", stop_reason=SimpleNamespace(type="end_turn")),
    ]

    async def stream(session_id: str) -> _Stream:
        return _Stream(events)

    async def send(session_id: str, events: list[dict[str, Any]]) -> None:
        pass

    client = SimpleNamespace(
        beta=SimpleNamespace(sessions=SimpleNamespace(events=SimpleNamespace(stream=stream, send=send)))
    )
    row = run.Row(arm=claude.name, repeat=0, index=0, question="q?", gold="a", reasoning_types=None)
    await run._drive(client, claude, "session", row)
    assert (row.tool_calls, row.tool_errors, row.searches) == (1, 1, 0)

    openai = run.build_arm("mcp:perplexity", None, 5, run.OPENAI)
    output = [_item("mcp_list_tools", error="401 Unauthorized"), _item("message")]
    responses = FakeResponses(_response("r1", output, text="a guess"))
    row = run.Row(arm=openai.name, repeat=0, index=0, question="q?", gold="a", reasoning_types=None)
    await run.run_response(SimpleNamespace(responses=responses), openai, row, _args())
    assert (row.tool_calls, row.tool_errors, row.searches) == (1, 1, 0)


def test_combine_can_keep_only_the_arms_asked_for(bench: tuple[ModuleType, ModuleType], tmp_path: Path) -> None:
    report, _ = bench
    backends = {"web-router:nimble": {"search": {"provider": "nimble"}}, "web-router:tavily": {"search": {}}}
    claude = _write_run(
        tmp_path / "claude",
        {**SETUP, "arms": ["claude-native", "web-router:nimble", "web-router:tavily"], "backends": backends},
        _full("claude-native", "correct", "correct")
        + _full("web-router:nimble", "correct", "incorrect")
        + _full("web-router:tavily", "correct", "correct"),
    )
    openai = _write_run(
        tmp_path / "openai",
        {**OPENAI_SETUP, "arms": ["openai-native"]},
        _full("openai-native", "correct", "correct"),
    )
    keep = "claude/web-router:nimble,claude-native"

    report.main(["--combine", str(claude), str(openai), "--out", str(tmp_path / "some"), "--arms", keep])

    summary = json.loads((tmp_path / "some" / "summary.json").read_text(encoding="utf-8"))
    assert list(summary["arms"]) == ["claude/web-router:nimble", "claude-native"]
    assert summary["arms"]["claude/web-router:nimble"]["incorrect"] == 1
    assert list(summary["setup"]["backends"]) == ["web-router:nimble"]
    rows = (tmp_path / "some" / "rows.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(rows) == 4
    assert summary["platforms"] == summary["setup"]["platforms"] == ["claude"]
    assert list(summary["setup"]["platform_setup"]) == ["claude"]
    markdown = (tmp_path / "some" / "report.md").read_text(encoding="utf-8")
    assert markdown.startswith("# Agent benchmark: the same Claude agent")
    assert "OpenAI timeout / budget" not in markdown
    report.main(
        [
            "--combine",
            str(claude),
            str(openai),
            "--out",
            str(tmp_path / "both"),
            "--arms",
            "claude-native,openai-native",
        ]
    )
    both = json.loads((tmp_path / "both" / "summary.json").read_text(encoding="utf-8"))
    assert both["platforms"] == ["claude", "openai"]
    with pytest.raises(SystemExit, match="cannot keep 'claude/web-router:exa'"):
        report.main(
            ["--combine", str(claude), str(openai), "--out", str(tmp_path / "x"), "--arms", "claude/web-router:exa"]
        )


def test_arms_without_combine_is_refused_rather_than_ignored(
    bench: tuple[ModuleType, ModuleType], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    report, _ = bench
    folder = _write_run(tmp_path / "run", {**SETUP, "arms": ["claude-native"]}, _full("claude-native", "correct"))

    with pytest.raises(SystemExit):
        report.main([str(folder), "--arms", "claude-native"])

    assert "--arms works only with --combine" in capsys.readouterr().err
    assert not (folder / "report.md").exists()


def test_mcp_all_attaches_every_vendors_server_with_its_own_key(
    bench: tuple[ModuleType, ModuleType], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, run = bench
    for server in run.MCP_SERVERS.values():
        monkeypatch.setenv(server.env_key, f"key-{server.env_key}")
    claude = run.build_arm("mcp:all", None, 5, run.CLAUDE)
    openai = run.build_arm("mcp:all", None, 5, run.OPENAI)

    assert claude.mcp_vendors == run.MCP_SETS["all"]
    assert [s["name"] for s in claude.mcp_servers] == list(run.MCP_SETS["all"])
    enabled = {t["mcp_server_name"]: [c["name"] for c in t["configs"]] for t in claude.tools}
    assert enabled["tavily"] == ["tavily_search", "tavily_extract"] and enabled["perplexity"] == ["perplexity_search"]
    sent = {t["server_label"]: t["headers"]["Authorization"] for t in run.request_tools(openai)}
    assert sent["exa"] == "Bearer key-EXA_API_KEY" and sent["nimble"] == "Bearer key-NIMBLE_API_KEY"
    assert "key-" not in json.dumps([claude.tools, claude.mcp_servers, openai.tools]) + claude.fingerprint
    monkeypatch.delenv("NIMBLE_API_KEY")
    with pytest.raises(SystemExit, match="NIMBLE_API_KEY"):
        run.build_arm("mcp:all", None, 5, run.CLAUDE)


async def test_each_mcp_call_is_credited_to_its_vendor_as_a_search_or_a_read_of_each_url(
    bench: tuple[ModuleType, ModuleType], monkeypatch: pytest.MonkeyPatch
) -> None:
    """What all-in pricing needs: who served each call, and whether it was a search or a page read."""
    _, run = bench
    for server in run.MCP_SERVERS.values():
        monkeypatch.setenv(server.env_key, "k")
    arm = run.build_arm("mcp:all", None, 5, run.CLAUDE)
    ok = SimpleNamespace(type="text", text="x")
    events = [
        _use("a", "tavily", "tavily_search", {"query": "q"}),
        _use("b", "exa", "web_fetch_exa", {"urls": ["https://a.example", "https://b.example"]}),
        _use("c", "nimble", "nimble_extract", {"url": "https://c.example"}),
        SimpleNamespace(type="agent.mcp_tool_result", mcp_tool_use_id="b", content=[ok], is_error=False),
        SimpleNamespace(type="agent.mcp_tool_result", mcp_tool_use_id="a", content=[ok], is_error=False),
        SimpleNamespace(type="agent.mcp_tool_result", mcp_tool_use_id="c", content=[], is_error=True),
        SimpleNamespace(type="session.status_idle", stop_reason=SimpleNamespace(type="end_turn")),
    ]

    async def stream(session_id: str) -> _Stream:
        return _Stream(events)

    async def send(session_id: str, events: list[dict[str, Any]]) -> None:
        pass

    client = SimpleNamespace(
        beta=SimpleNamespace(sessions=SimpleNamespace(events=SimpleNamespace(stream=stream, send=send)))
    )
    row = run.Row(arm=arm.name, repeat=0, index=0, question="q?", gold="a", reasoning_types=None)
    await run._drive(client, arm, "session", row)

    assert sorted(row.calls) == ["exa:read", "exa:read", "tavily:search"]
    assert (row.searches, row.extract_urls, row.tool_errors, row.tool_calls) == (1, 2, 1, 3)

    openai = run.build_arm("mcp:all", None, 5, run.OPENAI)
    output = [
        _item(
            "mcp_call", server_label="parallel", name="web_fetch", arguments='{"urls": ["u1"]}', output="p", error=None
        ),
        _item("mcp_call", server_label="perplexity", name="perplexity_search", arguments="{}", output="s", error=None),
        _item("message"),
    ]
    row = run.Row(arm=openai.name, repeat=0, index=0, question="q?", gold="a", reasoning_types=None)
    await run.run_response(
        SimpleNamespace(responses=FakeResponses(_response("r1", output, text="a"))), openai, row, _args()
    )
    assert row.calls == ["parallel:read", "perplexity:search"]


@pytest.mark.parametrize(
    ("arguments", "failed", "reads", "errors"),
    [
        ({"urls": ["a", "b", "c"]}, False, 3, 0),
        ('{"url": "a"}', False, 1, 0),
        ("not json", False, 0, 0),
        ({"query": "no url"}, False, 0, 0),
        ({"urls": ["a", "b"]}, True, 0, 1),
    ],
)
def test_a_direct_read_counts_each_url_it_named_and_nothing_it_did_not(
    bench: tuple[ModuleType, ModuleType], arguments: Any, failed: bool, reads: int, errors: int
) -> None:
    """The platform says only whether the whole call failed, so a read counts the URLs asked for -- and only those."""
    _, run = bench
    row = run.Row(arm="mcp:tavily", repeat=0, index=0, question="q?", gold="a", reasoning_types=None)

    run.mcp_call(row, "tavily", "tavily_extract", arguments, 0, failed)

    assert (row.calls, row.extract_urls, row.tool_errors) == (["tavily:read"] * reads, reads, errors)


async def test_a_web_router_call_is_credited_to_the_backend_that_served_it(
    bench: tuple[ModuleType, ModuleType],
) -> None:
    _, run = bench
    search = ToolResult("…", response=SearchResponse(provider="perplexity", query="q", results=[]))
    extract = ToolResult(
        "…",
        response=ExtractResponse(
            provider="nimble",
            documents=[ExtractedDocument(url="https://a.example", content="a")],
            failures=[ExtractFailure(url="https://b.example", error="HTTP 404")],
        ),
    )
    row = run.Row(arm="web-router:auto-tuned", repeat=0, index=0, question="q", gold="a", reasoning_types=None)
    run._record(search, row)
    run._record(extract, row)
    assert row.calls == ["perplexity:search", "nimble:read"]  # the failed URL was not served, so not billed


async def test_each_preset_runs_on_a_router_whose_auto_chain_is_the_presets(
    bench: tuple[ModuleType, ModuleType],
) -> None:
    _, run = bench
    from web_router import AsyncWebRouter

    assert run._preset("web-router:auto-tuned") == "auto-tuned" and run._preset("web-router:auto") is None
    for preset, chain in run.WEB_ROUTER_PRESETS.items():
        config = {"auto": {"chain": {m: list(c) for m, c in chain.items()}}}
        async with AsyncWebRouter(provider_config=config) as router:
            arm = run.build_arm(f"web-router:{preset}", router, 5, run.CLAUDE)
            assert arm.executor.provider == "auto"
            searched = [name for name, _ in router.get_provider("auto")._chain]
            assert searched == list(chain["search"])
            assert set(run.WEB_ROUTER_PRESETS[preset]["search"]) == set(run.MCP_SETS["all"])  # same vendors as mcp:all


def test_the_shipped_preset_is_autos_own_order_over_the_five_vendors(
    bench: tuple[ModuleType, ModuleType], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fails if auto's order changes and the preset does not follow; the keyless tier is left out on purpose."""
    _, run = bench
    from web_router.constants import AUTO_EXTRACT_DEFAULT_FORMAT
    from web_router.providers.auto import AutoExtractProvider, AutoProvider
    from web_router.registry import get_extract_provider_class

    for vendor in run.MCP_SETS["all"]:
        monkeypatch.setenv(run.MCP_SERVERS[vendor].env_key, "k")
    five = set(run.MCP_SETS["all"])
    searched = [name for name, _ in AutoProvider.default_chain() if name in five]
    read = [
        name
        for name, _ in AutoExtractProvider.default_chain()
        if name in five and AUTO_EXTRACT_DEFAULT_FORMAT in get_extract_provider_class(name).supported_formats
    ]

    assert run.WEB_ROUTER_PRESETS["auto-shipped"] == {"search": tuple(searched), "extract": tuple(read)}
    assert len(AutoProvider.default_chain()) > len(searched)  # the keyless tier the preset leaves out


def test_every_preset_reader_can_serve_the_format_the_tool_asks_for(bench: tuple[ModuleType, ModuleType]) -> None:
    """The extract tool sets no format, so auto asks for its default; a member that cannot return it is never tried."""
    _, run = bench
    from web_router.constants import AUTO_EXTRACT_DEFAULT_FORMAT
    from web_router.registry import get_extract_provider_class

    def serves(name: str, wanted: str) -> bool:
        return wanted in get_extract_provider_class(name).supported_formats

    for chain in run.WEB_ROUTER_PRESETS.values():
        assert all(serves(name, AUTO_EXTRACT_DEFAULT_FORMAT) for name in chain["extract"])
    assert not serves("exa", AUTO_EXTRACT_DEFAULT_FORMAT) and serves("exa", "text")  # why Exa is not listed


def test_exclude_samples_only_questions_the_named_runs_did_not_ask(
    bench: tuple[ModuleType, ModuleType], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, run = bench
    from web_router.evals import Dataset, Question

    full = Dataset(name="frames", questions=[Question(question=f"q{i}", answer="a") for i in range(10)])
    monkeypatch.setattr(
        run.Dataset, "frames", classmethod(lambda cls, path=None, limit=None, seed=0: full.sample(limit, seed))
    )
    old = _write_run(tmp_path / "old", SETUP, [_row("arm", i, "correct", question=f"q{i}") for i in range(6)])
    args = SimpleNamespace(dataset=None, limit=3, seed=1, exclude=[old])

    fresh = run.load_questions(args)

    assert len(fresh) == 3 and {q.question for q in fresh.questions} <= {"q6", "q7", "q8", "q9"}
    assert run.load_questions(args).questions == fresh.questions  # seeded, so reproducible
    with pytest.raises(SystemExit, match="more than the 4 questions left"):
        run.load_questions(SimpleNamespace(dataset=None, limit=5, seed=1, exclude=[old]))


def test_an_arm_whose_runs_mostly_failed_is_flagged_even_with_no_tool_error(
    bench: tuple[ModuleType, ModuleType],
) -> None:
    """A vendor's server down on OpenAI: the platform rejects the whole request, so no tool call is ever recorded."""
    report, _ = bench
    arms = ["web-router:auto-tuned", "mcp:all", "claude-native"]
    failed = {"failed_at": "agent", "error": "APIStatusError: 424", "tool_calls": 0, "searches": 0}
    rows = [_row("web-router:auto-tuned", i, "correct", searches=2) for i in range(20)]
    rows += [
        _row("mcp:all", i, "correct", searches=2) if i < 12 else _row("mcp:all", i, None, **failed) for i in range(20)
    ]
    rows += [
        _row("claude-native", i, "correct") if i < 15 else _row("claude-native", i, None, **failed) for i in range(20)
    ]

    summary, _ = report.build(rows, {**SETUP, "arms": arms})

    reasons = {a["arm"]: a["reason"] for a in summary["alerts"]}
    assert reasons == {
        "mcp:all": "8 of its 20 runs ended without an answer (40%)",
        "claude-native": "5 of its 20 runs ended without an answer (25%)",
    }
    assert all(w["winner"] not in reasons for w in summary["winners"])


def test_a_judge_outage_flags_no_arm(bench: tuple[ModuleType, ModuleType]) -> None:
    """The agent answered and grading failed: the judge's outage, so neither arm is flagged or kept from winning."""
    report, _ = bench
    arms = ["claude-native", "mcp:all"]
    judged = {"failed_at": "judge", "error": "APIConnectionError: judge down"}
    rows = [
        _row(arm, i, "correct", searches=2) if i < 12 else _row(arm, i, None, searches=2, **judged)
        for arm in arms
        for i in range(20)
    ]

    summary, _ = report.build(rows, {**SETUP, "arms": arms})

    assert summary["alerts"] == []
    assert summary["arms"]["mcp:all"]["failure_rate"] == 0.4  # still excluded from accuracy, and still shown
    assert summary["baselines"] == {"claude": "claude-native"}


def test_the_run_warns_while_an_arm_keeps_ending_without_an_answer(
    bench: tuple[ModuleType, ModuleType], capsys: pytest.CaptureFixture[str]
) -> None:
    _, run = bench
    failed: dict[str, int] = __import__("collections").defaultdict(int)
    warned: set[str] = set()

    def row(error: str | None) -> Any:
        r = run.Row(arm="openai/mcp:all", repeat=0, index=0, question="q", gold="a", reasoning_types=None)
        r.failed_at, r.error = ("agent", error) if error else (None, None)
        return r

    for _ in range(4):
        run._watch_failures(failed, warned, row("APIStatusError: 424"))
    assert "!!" not in capsys.readouterr().out
    run._watch_failures(failed, warned, row("APIStatusError: 424"))
    run._watch_failures(failed, warned, row("APIStatusError: 424"))
    assert capsys.readouterr().out.count("openai/mcp:all: 5 runs in a row ended without an answer") == 1
    run._watch_failures(failed, warned, row(None))
    assert failed["openai/mcp:all"] == 0


def test_a_judge_outage_does_not_warn_that_an_arm_ended_without_an_answer(
    bench: tuple[ModuleType, ModuleType], capsys: pytest.CaptureFixture[str]
) -> None:
    _, run = bench
    failed: dict[str, int] = __import__("collections").defaultdict(int)
    warned: set[str] = set()

    def row(failed_at: str) -> Any:
        r = run.Row(arm="claude-native", repeat=0, index=0, question="q", gold="a", reasoning_types=None)
        r.failed_at, r.error = failed_at, "APIConnectionError: down"
        return r

    for _ in range(4):
        run._watch_failures(failed, warned, row(run.FAILED_AT_AGENT))
    run._watch_failures(failed, warned, row(run.FAILED_AT_JUDGE))  # answered: the streak ends
    for _ in range(6):
        run._watch_failures(failed, warned, row(run.FAILED_AT_JUDGE))

    assert failed["claude-native"] == 0
    assert "ended without an answer" not in capsys.readouterr().out


def test_a_flagged_baseline_leaves_the_report_without_a_baseline_rather_than_failing(
    bench: tuple[ModuleType, ModuleType],
) -> None:
    """The fresh-question run: the OpenAI direct arm, its platform's first arm, lost 123 runs to an outage."""
    report, _ = bench
    failed = {"failed_at": "agent", "error": "APIStatusError: 424", "tool_calls": 0, "searches": 0}
    rows = [_row("mcp:all", i, None, **failed) for i in range(10)]
    rows += [_row("web-router:auto-tuned", i, "correct", searches=2) for i in range(10)]

    summary, markdown = report.build(rows, {**SETUP, "arms": ["mcp:all", "web-router:auto-tuned"]})

    assert [a["arm"] for a in summary["alerts"]] == ["mcp:all"]
    assert "Baseline `mcp:all` is flagged above" in markdown


async def test_the_no_search_arm_sends_no_tool_on_either_platform(bench: tuple[ModuleType, ModuleType]) -> None:
    _, run = bench
    claude = run.build_arm("no-search", None, 5, run.CLAUDE)
    openai = run.build_arm("no-search", None, 5, run.OPENAI)
    assert claude.tools == [] and claude.mcp_servers == [] and claude.system == run.TASK_PROMPT

    responses = FakeResponses(_response("r1", [_item("message")], text="Paris"))
    row = run.Row(arm=openai.name, repeat=0, index=0, question="q?", gold="Paris", reasoning_types=None)
    await run.run_response(SimpleNamespace(responses=responses), openai, row, _args())

    assert "tools" not in responses.requests[0]  # none at all, rather than an empty list
    assert (row.answer, row.tool_calls, row.tool_chars) == ("Paris", 0, 0)  # 0, read nothing; native is n/a


def test_a_shuffled_direct_arm_lists_its_servers_in_a_seeded_order_per_question(
    bench: tuple[ModuleType, ModuleType], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, run = bench
    for server in run.MCP_SERVERS.values():
        monkeypatch.setenv(server.env_key, "k")
    arm = run.build_arm("mcp:all:shuffled", None, 5, run.OPENAI)
    plain = run.build_arm("mcp:all", None, 5, run.OPENAI)

    def row(index: int) -> Any:
        return run.Row(arm=arm.name, repeat=0, index=index, question="q", gold="a", reasoning_types=None)

    orders = [run.server_order(arm, row(i), seed=7) for i in range(20)]
    assert all(sorted(o) == sorted(run.MCP_SETS["all"]) for o in orders)
    assert run.server_order(arm, row(3), seed=7) == orders[3]  # reproducible from the seed
    assert len({o[0] for o in orders}) > 1  # no vendor is always first
    assert run.server_order(plain, row(3), seed=7) == list(run.MCP_SETS["all"])  # unshuffled keeps its order
    sent = [t["server_label"] for t in run.request_tools(arm, orders[3])]
    assert sent == orders[3]


async def test_a_shuffled_claude_session_overrides_the_agents_server_order_and_records_it(
    bench: tuple[ModuleType, ModuleType], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, run = bench
    for server in run.MCP_SERVERS.values():
        monkeypatch.setenv(server.env_key, "k")
    arm = run.build_arm("mcp:all:shuffled", None, 5, run.CLAUDE)
    created: dict[str, Any] = {}

    async def create(**kwargs: Any) -> SimpleNamespace:
        created.update(kwargs)
        raise RuntimeError("stop after the session is defined")

    client = SimpleNamespace(beta=SimpleNamespace(sessions=SimpleNamespace(create=create)))
    row = run.Row(arm=arm.name, repeat=0, index=4, question="q", gold="a", reasoning_types=None)
    with pytest.raises(RuntimeError):
        await run.run_session(
            client, arm, {"id": "agt_1", "version": 2}, "env_1", row, SimpleNamespace(seed=7, session_budget=200)
        )

    reference = created["agent"]
    assert reference["type"] == "agent_with_overrides" and (reference["id"], reference["version"]) == ("agt_1", 2)
    assert row.mcp_order == run.server_order(arm, row, seed=7)
    assert [s["name"] for s in reference["mcp_servers"]] == row.mcp_order
    assert [t["mcp_server_name"] for t in reference["tools"]] == row.mcp_order


def test_the_question_set_is_chosen_recorded_and_held_equal_when_runs_combine(
    bench: tuple[ModuleType, ModuleType], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    report, run = bench
    from web_router.evals import Dataset, Question

    asked: list[str] = []

    def loader(name: str) -> Any:
        def load(cls: Any, path: Any = None, limit: Any = None, seed: int = 0) -> Dataset:
            asked.append(name)
            return Dataset(name=name, questions=[Question(question="q", answer="a", metadata={"topic": "Art"})])

        return classmethod(load)

    monkeypatch.setattr(run.Dataset, "simpleqa_verified", loader("simpleqa-verified"))
    args = run.parse_args(["--questions", "simpleqa-verified", "--limit", "1"])
    assert run.load_questions(args).name == "simpleqa-verified" and asked == ["simpleqa-verified"]
    assert run.run_setup(args, ["claude-native"])["questions"] == "simpleqa-verified"
    assert run.parse_args([]).questions == "frames"

    frames = _write_run(
        tmp_path / "frames", {**SETUP, "arms": ["claude-native"]}, _full("claude-native", "correct", "correct")
    )
    sqa = _write_run(
        tmp_path / "sqa",
        {**OPENAI_SETUP, "arms": ["openai-native"], "questions": "simpleqa-verified"},
        _full("openai-native", "correct", "correct"),
    )
    with pytest.raises(SystemExit, match="cannot combine: questions differs"):
        report.main(["--combine", str(frames), str(sqa), "--out", str(tmp_path / "both")])

    _, markdown = report.build(
        _full("no-search", "correct", "incorrect", reasoning_types="Art"),
        {**SETUP, "arms": ["no-search"], "questions": "simpleqa-verified"},
    )
    assert "SimpleQA Verified (google/simpleqa-verified" in markdown and "## Accuracy by topic" in markdown
    assert "no web tool at all" in markdown and "FRAMES" not in markdown


def test_the_report_compares_each_arm_with_no_search_and_describes_only_the_arms_that_ran(
    bench: tuple[ModuleType, ModuleType],
) -> None:
    report, _ = bench
    page = sys.modules.get("page") or __import__("page")
    arms = ["no-search", "mcp:all:shuffled", "web-router:auto-tuned"]
    rows = _full("no-search", "correct", "incorrect", list_cost_usd=0.01)
    rows += _full("mcp:all:shuffled", "correct", "correct", list_cost_usd=0.10)
    rows += _full("web-router:auto-tuned", "correct", "correct", list_cost_usd=0.04)

    summary, markdown = report.build(rows, {**SETUP, "arms": arms})

    vs = summary["vs_no_search"]
    assert set(vs) == {"mcp:all:shuffled", "web-router:auto-tuned"}
    assert vs["web-router:auto-tuned"]["accuracy_pp"] == 50.0
    assert vs["web-router:auto-tuned"]["list_cost_per_correct"] == pytest.approx(0.04)
    assert vs["web-router:auto-tuned"]["base_list_cost_per_correct"] == pytest.approx(0.02)
    assert "## Compared with no search" in markdown and "$0.040" in markdown
    # Only what ran: no built-in search here, so none is claimed.
    assert "built-in web search" not in markdown.split("## Setup")[0]
    assert "no web tool at all" in page.render(summary).split("<table")[0] or "no web tool at all" in markdown
    assert (
        page.tool_kinds(arms)
        == "web-router's search and extract, vendors' own hosted MCP servers or no web tool at all"
    )


def test_an_outage_names_an_http_vendor_and_one_of_the_kinds(bench: tuple[ModuleType, ModuleType]) -> None:
    _, run = bench
    assert run.parse_args(["--outage", "perplexity:rate-limit"]).outage == ("perplexity", "rate-limit")
    assert run.parse_args([]).outage is None
    for bad in ("nimble:rate-limit", "perplexity:meltdown", "perplexity"):
        with pytest.raises(SystemExit):
            run.parse_args(["--outage", bad])


@pytest.mark.parametrize("kind", ["rate-limit", "server-error", "revoked-key", "timeout"])
async def test_the_simulated_vendor_fails_through_the_providers_own_error_handling(
    bench: tuple[ModuleType, ModuleType], kind: str
) -> None:
    """Below the provider: the failure takes the path a real one would, and nothing reaches the vendor."""
    _, run = bench
    from web_router.exceptions import ProviderError
    from web_router.providers.perplexity import PerplexityProvider
    from web_router.types import SearchRequest

    provider = PerplexityProvider(api_key="k", transport=run.failing_transport(run.OUTAGES[kind]))
    with pytest.raises(ProviderError):
        await provider.asearch(SearchRequest(query="q"))
    await provider.aclose()


async def test_auto_meets_the_outage_where_its_vendor_is_listed_and_falls_through_to_the_next(
    bench: tuple[ModuleType, ModuleType], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, run = bench
    from web_router import AsyncWebRouter

    monkeypatch.setenv("PERPLEXITY_API_KEY", "k")
    monkeypatch.setenv("EXA_API_KEY", "k")
    failing = {"perplexity": {"transport": run.failing_transport(run.OUTAGES["server-error"])}}
    chain = run.outage_chain({"search": ("perplexity", "exa")}, failing)
    assert chain["search"][1] == "exa" and chain["search"][0][0] == "perplexity"

    exa = httpx.MockTransport(lambda request: httpx.Response(200, json={"results": [{"url": "https://a.example"}]}))
    chain["search"][1] = ("exa", {"transport": exa})
    async with AsyncWebRouter(provider_config={"auto": {"chain": chain}}) as router:
        response = await router.search("q", provider="auto")
    assert response.provider == "exa" and response.results[0].url == "https://a.example"


def test_the_direct_side_of_an_outage_never_sends_a_real_key(
    bench: tuple[ModuleType, ModuleType], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, run = bench
    monkeypatch.setattr(run, "MCP_SERVERS", dict(run.MCP_SERVERS))
    monkeypatch.setenv("PERPLEXITY_API_KEY", "real-key")
    real = run.MCP_SERVERS["perplexity"]

    failing = run.apply_outage(("perplexity", "rate-limit"))

    replaced = run.MCP_SERVERS["perplexity"]
    assert replaced.url == "https://httpbin.org/status/429" and replaced.url != real.url
    assert run.mcp_key("mcp:perplexity", replaced) == run.OUTAGE_BEARER
    assert set(failing) == {"perplexity"} and "transport" in failing["perplexity"]
    run.apply_outage(("exa", "revoked-key"))
    assert run.MCP_SERVERS["exa"].url == "https://mcp.exa.ai/mcp"  # a revoked key keeps the real server
    assert run.MCP_SERVERS["exa"].token == run.OUTAGE_BEARER
    record = run.outage_record(("perplexity", "timeout"))
    assert "times out" in record["web_router"] and "real-key" not in json.dumps(record)


async def test_an_outage_run_keeps_its_credentials_in_vaults_of_their_own(
    bench: tuple[ModuleType, ModuleType], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A run still going reads its vault on every session, so an outage must never set a dummy token on it."""
    _, run = bench
    state = tmp_path / "state.json"
    real_vault = {"id": "vlt_real", "credential_id": "cred_real", "url": run.MCP_SERVERS["perplexity"].url}
    state.write_text(
        json.dumps(
            {
                "environment_id": "env_1",
                "environment_config": run.ENVIRONMENT_CONFIG,
                "vaults": {"perplexity": real_vault},
            }
        )
    )
    monkeypatch.setattr(run, "STATE_FILE", state)
    monkeypatch.setattr(run, "MCP_SERVERS", dict(run.MCP_SERVERS))
    monkeypatch.setenv("PERPLEXITY_API_KEY", KEY)
    run.apply_outage(("perplexity", "revoked-key"))
    client = _Recorder()

    _, _, vaults = await run.ensure_setup(client, [run.build_arm("mcp:perplexity", None, 5, run.CLAUDE)], "m")

    saved = json.loads(state.read_text())["vaults"]
    assert saved["perplexity"] == real_vault and vaults == {"perplexity": "vlt_1"}
    assert saved["perplexity:outage"]["id"] == "vlt_1"
    assert not client.named("cred.update") and not client.named("cred.delete")
    ((_, created),) = client.named("cred.create")
    assert created["auth"]["token"] == run.OUTAGE_BEARER and KEY not in state.read_text()


def test_the_report_says_an_outage_was_injected_and_how_each_side_failed(bench: tuple[ModuleType, ModuleType]) -> None:
    report, run = bench
    outage = run.outage_record(("perplexity", "server-error"))
    _, markdown = report.build(
        _full("web-router:auto-tuned", "correct", "correct"),
        {**SETUP, "arms": ["web-router:auto-tuned"], "outage": outage},
    )
    assert "Outage injected" in markdown and "answers 500 to every call" in markdown
    assert "This run injected an outage" in markdown


async def test_an_outage_runs_backends_are_recorded_with_the_failure_named(
    bench: tuple[ModuleType, ModuleType], monkeypatch: pytest.MonkeyPatch
) -> None:
    """run.json is JSON: the failing transport in a chain member's options is written as a label."""
    _, run = bench
    from web_router import AsyncWebRouter

    monkeypatch.setenv("PERPLEXITY_API_KEY", "k")
    failing = {"perplexity": {"transport": run.failing_transport(run.OUTAGES["server-error"])}}
    chain = run.outage_chain({"search": ("perplexity",), "extract": ("nimble",)}, failing)
    async with AsyncWebRouter(provider_config={"auto": {"chain": chain}}) as router:
        arm = run.build_arm("web-router:auto-tuned", router, 5, run.CLAUDE)
        config = run.backend_config(arm.executor)
    assert config["search"]["chain"][0]["options"] == {"transport": run.OUTAGE_LABEL}
    json.dumps(config)


async def test_a_plain_auto_arm_meets_the_outage_too_rather_than_the_real_vendor(
    bench: tuple[ModuleType, ModuleType], monkeypatch: pytest.MonkeyPatch
) -> None:
    """auto builds its members from its own chain, never from provider_config[vendor]."""
    _, run = bench
    from web_router import AsyncWebRouter

    monkeypatch.setattr(run, "MCP_SERVERS", dict(run.MCP_SERVERS))
    monkeypatch.setenv("PERPLEXITY_API_KEY", "k")
    failing = run.apply_outage(("perplexity", "server-error"))
    chain = run.default_outage_chain(failing)

    search = dict(chain["search"])
    assert isinstance(search["perplexity"]["transport"], httpx.BaseTransport)
    assert all("transport" not in options for name, options in chain["search"] if name != "perplexity")
    async with AsyncWebRouter(provider_config={**failing, "auto": {"chain": chain}}) as router:
        members = dict(router.get_provider("auto")._chain)
    assert "transport" in members["perplexity"]


async def test_an_outage_bites_even_where_this_machine_holds_no_key_for_the_vendor(
    bench: tuple[ModuleType, ModuleType], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without a key the member would be skipped as unconfigured, never meeting the failure; a placeholder builds it."""
    _, run = bench
    from web_router.exceptions import ProviderError
    from web_router.registry import create_provider
    from web_router.types import SearchRequest

    monkeypatch.setattr(run, "MCP_SERVERS", dict(run.MCP_SERVERS))
    monkeypatch.delenv("PERPLEXITY_API_KEY", raising=False)
    failing = run.apply_outage(("perplexity", "rate-limit"))

    provider = create_provider("perplexity", **failing["perplexity"])
    with pytest.raises(ProviderError):
        await provider.asearch(SearchRequest(query="q"))
    await provider.aclose()


def test_runs_combine_only_with_the_same_outage_and_the_combined_report_keeps_it(
    bench: tuple[ModuleType, ModuleType], tmp_path: Path
) -> None:
    report, run = bench
    outage = run.outage_record(("perplexity", "server-error"))
    claude = _write_run(
        tmp_path / "claude",
        {**SETUP, "arms": ["claude-native"], "outage": outage},
        _full("claude-native", "correct", "correct"),
    )
    openai = _write_run(
        tmp_path / "openai", {**OPENAI_SETUP, "arms": ["openai-native"]}, _full("openai-native", "correct", "correct")
    )
    with pytest.raises(SystemExit, match="cannot combine: outage differs"):
        report.main(["--combine", str(claude), str(openai), "--out", str(tmp_path / "x")])

    openai_too = _write_run(
        tmp_path / "openai2",
        {**OPENAI_SETUP, "arms": ["openai-native"], "outage": outage},
        _full("openai-native", "correct", "correct"),
    )
    report.main(["--combine", str(claude), str(openai_too), "--out", str(tmp_path / "both")])
    combined = json.loads((tmp_path / "both" / "run.json").read_text(encoding="utf-8"))
    assert combined["outage"] == outage
    assert "Outage injected" in (tmp_path / "both" / "report.md").read_text(encoding="utf-8")


def test_sealqa_and_your_own_csv_are_question_sets_too(
    bench: tuple[ModuleType, ModuleType], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, run = bench
    from web_router.evals import Dataset, Question

    asked: list[tuple[str, ...]] = []

    def sealqa(cls: Any, split: str, path: Any = None, limit: Any = None, seed: int = 0) -> Dataset:
        asked.append(("sealqa", split))
        return Dataset(name=f"sealqa-{split}", questions=[Question(question="q", answer="a")])

    monkeypatch.setattr(run.Dataset, "sealqa", classmethod(sealqa))
    assert run.load_questions(run.parse_args(["--questions", "sealqa-hard", "--limit", "1"])).name == "sealqa-seal-hard"
    assert asked == [("sealqa", "seal-hard")]

    with pytest.raises(SystemExit, match="--dataset"):
        run.load_questions(run.parse_args(["--questions", "csv"]))
    path = tmp_path / "mine.csv"
    path.write_text("q,gold,topic\nWho?,Alice,People\n", encoding="utf-8")
    mine = run.load_questions(
        run.parse_args(
            [
                "--questions",
                "csv",
                "--dataset",
                str(path),
                "--question-column",
                "q",
                "--answer-column",
                "gold",
                "--limit",
                "1",
            ]
        )
    )
    assert [(q.question, q.answer) for q in mine.questions] == [("Who?", "Alice")]


def test_a_run_of_fewer_questions_than_its_limit_is_complete_and_csvs_are_told_apart(
    bench: tuple[ModuleType, ModuleType], tmp_path: Path
) -> None:
    report, run = bench
    report._check_complete("small", _full("claude-native", "correct", "correct"), {**SETUP, "limit": 50, "asked": 2})

    path = tmp_path / "mine.csv"
    path.write_text("problem,answer\nWho?,Alice\n", encoding="utf-8")
    setup = run.run_setup(run.parse_args(["--questions", "csv", "--dataset", str(path)]), ["claude-native"])
    assert len(setup["dataset_sha256"]) == 64 and setup["answer_column"] == "answer"
    assert "dataset_sha256" in report.MATCHED_SETUP


def test_the_loaded_count_reaches_the_page_and_a_combined_report(
    bench: tuple[ModuleType, ModuleType], tmp_path: Path
) -> None:
    report, _ = bench
    import page

    small = {**SETUP, "arms": ["claude-native"], "limit": 50, "asked": 2}
    assert "Questions: 2 from" in page._intro(small, ["claude"], False).replace("</dt><dd>", ": ")
    first = ("a", _full("claude-native", "correct", "correct"), small)
    second = ("b", _full("web-router:nimble", "correct", "correct"), {**small, "arms": ["web-router:nimble"]})
    _, setup = report.combine([first, second])
    assert setup["asked"] == 2
    old = {**SETUP, "arms": ["claude-native"], "limit": 50}
    assert "50 from" in page._intro(old, ["claude"], False).replace("</dt><dd>", ": ").replace("Questions: ", "")


def _bench_row(run: ModuleType) -> Any:
    return run.Row(arm="a", repeat=0, index=0, question="q", gold="g", reasoning_types=None)


def test_a_web_router_arm_never_shows_the_agent_an_answer_key_page(
    bench: tuple[ModuleType, ModuleType],
    fake_provider: Any,
    fake_extract_provider: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, run = bench
    respond = fake_provider._respond
    # A backend that also synthesizes an answer, which may rest on the page withheld.
    monkeypatch.setattr(fake_provider, "_respond", lambda self, r: respond(self, r).model_copy(update={"answer": "42"}))
    from web_router.evals import Dataset, Question
    from web_router.integrations.anthropic import WebRouterTools

    sealed = Dataset(name="s", questions=[Question(question="q", answer="a")], withheld_markers=("example.com",))
    row = _bench_row(run)

    async def go() -> tuple[Any, Any, Any]:
        run._ROW.set(row)
        async with run.WithholdingRouter(sealed) as router:
            tools = WebRouterTools(router, provider="fake", extract_provider="fake")
            search = await tools.acall("web_router_search", {"query": "who"})
            read = await tools.acall("web_router_extract", {"urls": ["https://example.com/answers"]})
            mixed = await tools.acall("web_router_extract", {"urls": ["https://example.com/a", "https://other.org/b"]})
            return search, read, mixed

    search, read, mixed = asyncio.run(go())
    assert search.response.results == [] and "example.com" not in search.content
    assert search.response.answer is None
    assert read.is_error and "answer key" in read.content
    assert [d.url for d in mixed.response.documents] == ["https://other.org/b"]
    assert row.withheld == 3


def test_a_set_that_withholds_nothing_passes_every_call_through(
    bench: tuple[ModuleType, ModuleType], fake_provider: Any
) -> None:
    _, run = bench
    from web_router.evals import Dataset, Question

    plain = Dataset(name="p", questions=[Question(question="q", answer="a")])

    async def go() -> Any:
        async with run.WithholdingRouter(plain) as router:
            return await router.search("who", provider="fake")

    assert [r.url for r in asyncio.run(go()).results] == ["https://example.com"]


def test_a_direct_arm_calls_the_vendor_raw_keeps_the_key_local_and_withholds_too(
    bench: tuple[ModuleType, ModuleType], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, run = bench
    import direct
    import httpx

    monkeypatch.setenv("WEBIQ_API_KEY", "test-key")
    sent: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        hits = [{"url": "https://arxiv.org/abs/2506.01062"}, {"url": "https://en.wikipedia.org/wiki/X"}]
        return httpx.Response(200, json={"webResults": hits, "traceId": "t"})

    withholds = {"https://arxiv.org/abs/2506.01062", "https://arxiv.org/abs/1"}.__contains__
    tools = direct.DirectTools("direct:webiq", "webiq", "openai", 5, withholds)
    tools._client = httpx.AsyncClient(base_url="https://api.microsoft.ai", transport=httpx.MockTransport(handle))
    row = _bench_row(run)

    async def go() -> tuple[Any, Any]:
        search = await tools.acall("webiq_search", '{"query": "who"}')
        read = await tools.acall("webiq_browse", {"url": "https://arxiv.org/abs/1"})
        await tools.aclose()
        return search, read

    search, read = asyncio.run(go())
    for result in (search, read):
        run._record(result, row)
    assert json.loads(search.content)["webResults"] == [{"url": "https://en.wikipedia.org/wiki/X"}]
    assert read.is_error and len(sent) == 1, "a withheld page is never fetched"
    assert json.loads(sent[0].content) == {"query": "who", "maxResults": 5, "contentFormat": "passage"}
    assert sent[0].headers["x-apikey"] == "test-key" and "test-key" not in json.dumps(tools.config())
    assert row.withheld == 2 and row.searches == 1 and row.calls == ["webiq:search"]
    assert [d["name"] for d in tools.definitions()] == ["webiq_search", "webiq_browse"]
    assert all(d["strict"] for d in tools.definitions())


def test_a_direct_arm_without_its_key_is_refused_before_anything_runs(
    bench: tuple[ModuleType, ModuleType], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, run = bench
    monkeypatch.delenv("WEBIQ_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="WEBIQ_API_KEY"):
        run.build_arm("direct:webiq", run.AsyncWebRouter(), 5)


def test_a_platform_run_tool_that_shows_an_answer_key_page_is_counted(bench: tuple[ModuleType, ModuleType]) -> None:
    _, run = bench
    row = _bench_row(run)
    run._WITHHOLDS.set(lambda url: "huggingface.co" in url)
    results = '{"results": [{"url": "https://huggingface.co/datasets/vtllms/sealqa", "text": "cf. https://huggingface.co/x"}]}'
    run._seen(row, run._result_urls(results))
    run._seen(
        row, run._result_urls("Title: A\nURL: https://huggingface.co/papers/1\nBody cites https://huggingface.co/y")
    )
    run._seen(row, [None, "https://huggingface.co/opened"])
    run._WITHHOLDS.set(None)
    run._seen(row, ["https://huggingface.co/again"])
    assert row.answer_key_seen == 3, "a result's or a read's own URL counts; a link inside a page's text does not"


def test_the_report_names_answer_key_pages_and_describes_a_direct_arm(bench: tuple[ModuleType, ModuleType]) -> None:
    report, _ = bench
    rows = _full("claude-native", "correct", "correct", answer_key_seen=1)
    rows += _full("direct:webiq", "correct", "incorrect", withheld=2)
    _, markdown = report.build(rows, {**SETUP, "arms": ["claude-native", "direct:webiq"]})

    assert "## Answer-key pages" in markdown
    assert "| `claude-native` | 0 | 2 | 2 |" in markdown and "| `direct:webiq` | 4 | 0 | 0 |" in markdown

    rows += _full("web-router:nimble", "correct", "correct")
    _, markdown = report.build(rows, {**SETUP, "arms": ["claude-native", "direct:webiq", "web-router:nimble"]})
    assert "| `web-router:nimble` | 0 | 0 | 0 |" in markdown, "an arm that met none still has its row"
    assert "`webiq`'s own REST API" in markdown and "A `direct:` arm is the vendor's API" in markdown


def test_the_most_accurate_web_router_arm_is_never_a_direct_or_mcp_one(bench: tuple[ModuleType, ModuleType]) -> None:
    report, _ = bench
    rows = [
        {**r, "platform": platform}
        for platform, prefix in (("claude", "claude/"), ("openai", "openai/"))
        for arm, grades in (
            (f"{prefix}web-router:webiq", ("correct", "incorrect")),
            (f"{prefix}direct:webiq", ("correct", "correct")),
        )
        for r in _full(arm, *grades)
    ]
    rows += [{**r, "platform": "claude"} for r in _full("claude-native", "correct", "correct")]
    rows += [{**r, "platform": "openai"} for r in _full("openai-native", "correct", "correct")]
    arms = [
        "claude-native",
        "openai-native",
        "claude/web-router:webiq",
        "openai/web-router:webiq",
        "claude/direct:webiq",
        "openai/direct:webiq",
    ]
    setup = {**SETUP, "arms": arms, "platforms": ["claude", "openai"]}
    _, markdown = report.build(rows, setup)
    assert "Most accurate web-router arm: `web-router:webiq` on Claude" in markdown


def test_a_claude_search_result_block_counts_by_its_source(bench: tuple[ModuleType, ModuleType]) -> None:
    _, run = bench
    from types import SimpleNamespace

    def block(source: str) -> Any:
        return SimpleNamespace(type="search_result", source=source, model_dump=lambda: {"source": source})

    row = _bench_row(run)
    run._WITHHOLDS.set(lambda url: "sealqa" in url)
    run._seen(row, run._result_urls([block("https://huggingface.co/datasets/vtllms/sealqa"), block("https://a.org")]))
    run._WITHHOLDS.set(None)
    assert row.answer_key_seen == 1


def test_a_withheld_read_is_not_a_tool_error_but_a_vendor_failure_is(
    bench: tuple[ModuleType, ModuleType], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, run = bench
    import direct
    import httpx

    monkeypatch.setenv("WEBIQ_API_KEY", "SECRETKEY123")

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="Invalid api key SECRETKEY123")

    tools = direct.DirectTools("direct:webiq", "webiq", "claude", 5, lambda url: "sealqa" in url)
    tools._client = httpx.AsyncClient(base_url="https://api.microsoft.ai", transport=httpx.MockTransport(handle))

    async def go() -> tuple[Any, Any]:
        withheld = await tools.acall("webiq_browse", {"url": "https://x.org/sealqa"})
        failed = await tools.acall("webiq_search", {"query": "who"})
        await tools.aclose()
        return withheld, failed

    withheld, failed = asyncio.run(go())
    row = _bench_row(run)
    for result in (withheld, failed):
        run._record(result, row)
    assert row.withheld == 1 and row.tool_errors == 1, "only the vendor's failure is a tool error"
    assert "SECRETKEY123" not in failed.content and "401" in failed.content


def test_a_sealqa_agent_run_withholds_its_paper_and_passes_an_unrelated_one(
    bench: tuple[ModuleType, ModuleType], monkeypatch: pytest.MonkeyPatch, fake_provider: Any
) -> None:
    _, run = bench
    from web_router.constants import SEALQA_WITHHELD_MARKERS
    from web_router.evals import Dataset, Question
    from web_router.types import SearchResult

    hits = [
        SearchResult(url="https://arxiv.org/abs/2506.01062", title="Key"),
        SearchResult(url="https://arxiv.org/abs/1706.03762", title="Other"),
    ]
    respond = fake_provider._respond
    monkeypatch.setattr(
        fake_provider, "_respond", lambda self, r: respond(self, r).model_copy(update={"results": hits})
    )
    sealqa = Dataset(
        name="sealqa-seal-0", questions=[Question(question="q", answer="a")], withheld_markers=SEALQA_WITHHELD_MARKERS
    )

    async def go() -> Any:
        async with run.WithholdingRouter(sealqa) as router:
            return await router.search("who", provider="fake")

    assert [r.url for r in asyncio.run(go()).results] == ["https://arxiv.org/abs/1706.03762"]
