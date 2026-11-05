"""The tool and effort runner's plan and run: the dry run, its order, resuming, failures, search then read."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.benchmarks._tools_effort_fakes import FakeJudge, FakeRouter, _args, _items
from web_router import prices
from web_router.evals import Question
from web_router.types import (
    Citation,
    ExtractResponse,
    ResearchResponse,
)

# The plan


def test_the_dry_run_plans_the_approved_sample_and_paths(run_module: ModuleType, plan_module: ModuleType) -> None:
    items = run_module.placeholder_sample()
    tasks = plan_module.plan(items)
    groups = Counter(i.group for i in items)
    columns = Counter((t.path, t.provider, t.mode) for t in tasks)
    research = {(p, m): n for (path, p, m), n in columns.items() if path == "research"}

    assert groups == {"easy": 10, "hard": 20, "synthesis": 20, "url-given": 10}
    assert research == {
        ("nimble", "low"): 60,
        ("nimble", "medium"): 60,
        ("nimble", "high"): 60,
        ("parallel", "low"): 60,
        ("parallel", "medium"): 60,
        ("parallel", "high"): 60,
        ("parallel", "max"): 60,
        ("linkup", "low"): 60,
        ("perplexity", "low"): 60,
        ("perplexity", "medium"): 60,
    }
    assert sum(research.values()) == 600
    assert {p: n for (path, p, _), n in columns.items() if path == "search+extract"} == {
        "nimble": 60,
        "perplexity": 60,
        "linkup": 60,
    }
    answers = {p: n for (path, p, _), n in columns.items() if path == "answer"}
    # The easy questions reuse the frozen answer rows of every backend but Brave.
    assert answers == {"perplexity": 50, "you": 50, "parallel": 50, "exa": 50, "linkup": 50, "brave": 60}
    assert columns[("extract", "nimble", "default")] == 10
    assert len(tasks) == 1100

    text = run_module.dry_run(items, 125.0)
    assert "60 questions, 1100 rows" in text
    assert "research 600" in text and "answer 310" in text and "search 180" in text


def test_the_runner_works_through_whole_columns_cheapest_research_first(
    run_module: ModuleType, plan_module: ModuleType
) -> None:
    tasks = run_module.order(plan_module.plan(run_module.placeholder_sample()))
    paths = [t.path for t in tasks]
    research = list(dict.fromkeys((t.provider, t.mode) for t in tasks if t.path == "research"))

    assert paths == sorted(paths, key=["search+extract", "extract", "answer", "research"].index)
    assert research[0] == ("parallel", "low") and research[-1] == ("nimble", "high")


def test_a_question_given_its_pages_gets_its_own_id(plan_module: ModuleType) -> None:
    question = Question(question="Read these?", answer="gold")
    alone = plan_module.Item.make("easy", "simpleqa", 0, question)
    given = plan_module.Item.make("url-given", "simpleqa", 0, question, ["https://a.org"])

    assert alone.qid != given.qid
    assert plan_module.Item.from_entry(given.entry()).qid == given.qid


# Resuming


def test_a_rerun_skips_the_rows_already_written_and_counts_what_was_spent(
    tools_effort: ModuleType,
    plan_module: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    run_module: ModuleType,
) -> None:
    items = _items(plan_module)
    monkeypatch.setattr(tools_effort, "load_sample", lambda local: items)
    total = len(plan_module.plan(items))
    first_router, second_router = FakeRouter(), FakeRouter()

    first = asyncio.run(tools_effort.run(_args(tmp_path, budget=1.0), FakeJudge(), first_router))
    spent = sum(e["usd"] for e in run_module.read_jsonl(tmp_path / run_module.SPEND_FILE))
    second = asyncio.run(tools_effort.run(_args(tmp_path, budget=100.0), FakeJudge(), second_router))
    third_router, third_judge = FakeRouter(), FakeJudge()
    third = asyncio.run(tools_effort.run(_args(tmp_path, budget=100.0), third_judge, third_router))

    rows = run_module.read_jsonl(tmp_path / run_module.ROWS_FILE)
    assert 0 < first < total, "the first run stopped at its budget"
    assert 0 < spent <= 1.0
    assert first + second == total == len(rows)
    assert len(run_module.done_keys(rows)) == total, "no row asked twice"
    assert third == 0 and not third_router.calls, "a finished run calls nothing"
    assert third_judge.calls == 0, "not even the judge's check"
    checks = [e for e in run_module.read_jsonl(tmp_path / run_module.SPEND_FILE) if e["path"] == "judge-check"]
    assert [e["usd"] for e in checks] == [0.001, 0.001], "each run that asked something paid for its check"
    run = json.loads((tmp_path / run_module.RUN_FILE).read_text(encoding="utf-8"))
    assert len(run["resumed"]) == 2
    assert all("question" not in entry for entry in run["sample"]), "run.json names questions by id only"


def test_a_resumed_run_refuses_another_sample_or_research_timeout(
    tools_effort: ModuleType, plan_module: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    items = _items(plan_module)
    monkeypatch.setattr(tools_effort, "load_sample", lambda local: items)
    judge = FakeJudge()
    with pytest.raises(SystemExit, match="Nothing was asked"):
        # Even the judge's check is refused past the budget.
        asyncio.run(tools_effort.run(_args(tmp_path, budget=0.0), judge, FakeRouter()))
    assert judge.calls == 0

    slower = {**vars(_args(tmp_path, budget=100.0)), "research_timeout": 99.0}
    with pytest.raises(SystemExit, match="research_timeout differs"):
        asyncio.run(tools_effort.run(argparse.Namespace(**slower), FakeJudge(), FakeRouter()))
    monkeypatch.setattr(tools_effort, "load_sample", lambda local: items[:2])
    with pytest.raises(SystemExit, match="another run"):
        asyncio.run(tools_effort.run(_args(tmp_path, budget=100.0), FakeJudge(), FakeRouter()))


# One row


def test_a_leaked_research_answer_is_not_graded(
    plan_module: ModuleType, budget_module: ModuleType, runner_module: ModuleType
) -> None:
    class LeakyRouter(FakeRouter):
        async def research(self, query: str, *, provider: str, effort: str, timeout: float) -> ResearchResponse:
            citations = [Citation(url="https://huggingface.co/datasets/vtllms/sealqa")]
            return ResearchResponse(provider=provider, query=query, answer="gold", citations=citations)

    task = plan_module.Task(_items(plan_module)[1], "research", "parallel", "low")
    judge = FakeJudge()
    ticket = budget_module.Ticket(budget_module.Budget(cap=10.0), task)
    row = asyncio.run(runner_module.Runner(LeakyRouter(), judge).row(task, ticket))

    assert row["leaked"] and row["grade"] is None and judge.calls == 0


def test_search_then_read_reads_the_top_three_pages_and_judges_once(
    plan_module: ModuleType, budget_module: ModuleType, runner_module: ModuleType
) -> None:
    task = plan_module.Task(_items(plan_module)[0], "search+extract", "linkup", "top3")
    router, judge = FakeRouter(), FakeJudge()
    row = asyncio.run(
        runner_module.Runner(router, judge).row(task, budget_module.Ticket(budget_module.Budget(10.0), task))
    )

    assert router.calls == {"search": 1, "extract": 3}
    assert row["pages_read"] == 3 and row["grade"] == "correct" and judge.calls == 2
    assert row["cost_usd"] == pytest.approx(
        prices.estimate("linkup", "search") + 3 * prices.estimate("nimble", "extract")
    )


# What a run never loses


def test_a_failing_row_stops_new_rows_and_is_raised_once_the_rest_finish(
    tools_effort: ModuleType, plan_module: ModuleType, budget_module: ModuleType, runner_module: ModuleType
) -> None:
    tasks = [plan_module.Task(_items(plan_module)[0], "research", "parallel", "low")] * 5
    router = FakeRouter()
    budget = budget_module.Budget(cap=100.0)

    def broken_sink(row: dict[str, Any]) -> None:
        raise OSError("disk full")

    runner = runner_module.Runner(router, FakeJudge())
    with pytest.raises(OSError, match="disk full"):
        asyncio.run(tools_effort.execute(tasks, runner.row, budget, broken_sink, concurrency=1, say=lambda _: None))
    assert router.calls["research"] == 1, "no row started after the one that failed"
    assert budget.reserved == pytest.approx(0.0)


def test_a_failed_page_read_counts_in_the_rows_cost_and_time(
    plan_module: ModuleType, budget_module: ModuleType, runner_module: ModuleType
) -> None:
    class FlakyRouter(FakeRouter):
        async def extract(self, url: str, *, provider: str) -> ExtractResponse:
            if url.endswith("/1"):
                self.calls["extract"] += 1
                raise RuntimeError("HTTP 500")
            return await super().extract(url, provider=provider)

    task = plan_module.Task(_items(plan_module)[0], "search+extract", "nimble", "top3")
    router = FlakyRouter()
    row = asyncio.run(
        runner_module.Runner(router, FakeJudge()).row(task, budget_module.Ticket(budget_module.Budget(10.0), task))
    )

    assert router.calls["extract"] == 3 and row["pages_read"] == 2 and row["grade"] == "correct"
    assert row["cost_usd"] == pytest.approx(
        prices.estimate("nimble", "search") + 3 * prices.estimate("nimble", "extract")
    )
