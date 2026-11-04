"""The tool and effort runner: its plan, its budget, resuming and freezing a run -- with fakes, never a paid call."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from web_router import prices
from web_router.evals import Question
from web_router.types import (
    Citation,
    ExtractedDocument,
    ExtractResponse,
    ResearchResponse,
    SearchResponse,
    SearchResult,
)
from web_router.types_answer import AnswerResponse


@pytest.fixture(scope="module")
def plan_module(tools_effort: ModuleType) -> ModuleType:
    """The plan, as the runner imported it."""
    return sys.modules["_tools_effort_plan"]


class FakeRouter:
    """Answers every call at once and counts them; ``raw`` carries a stated cost when one is given."""

    def __init__(self, stated: float | None = None) -> None:
        self.calls: Counter[str] = Counter()
        self.stated = stated

    def _raw(self) -> list[dict[str, Any]] | None:
        return None if self.stated is None else [{"usage": {"cost": {"total_cost": self.stated}}}]

    async def search(self, query: str, *, provider: str, max_results: int) -> SearchResponse:
        self.calls["search"] += 1
        results = [SearchResult(url=f"https://example.org/{i}", title=f"t{i}", description="snip") for i in range(5)]
        return SearchResponse(provider=provider, query=query, results=results)

    async def extract(self, url: str, *, provider: str) -> ExtractResponse:
        self.calls["extract"] += 1
        return ExtractResponse(provider=provider, documents=[ExtractedDocument(url=url, content="page text")])

    async def answer(self, query: str, *, provider: str) -> AnswerResponse:
        self.calls["answer"] += 1
        return AnswerResponse(provider=provider, query=query, answer="gold", raw=self._raw())

    async def research(self, query: str, *, provider: str, effort: str, timeout: float) -> ResearchResponse:
        self.calls["research"] += 1
        citations = [Citation(url="https://example.org/a")]
        return ResearchResponse(provider=provider, query=query, answer="gold", citations=citations, raw=self._raw())


class FakeJudge:
    """Grades everything correct, at a fixed cost per call (``None``: the call reports no usage)."""

    label = "openai:gpt-4o"

    def __init__(self, cost: float | None = 0.001) -> None:
        self.cost = cost
        self.calls = 0

    def check(self) -> None:
        pass

    async def ask(self, system: str, user: str) -> tuple[str, float | None]:
        self.calls += 1
        return ("A" if not system else "gold"), self.cost

    async def aclose(self) -> None:
        pass


def _items(plan_module: ModuleType) -> list[Any]:
    """One question of each group, with real-looking text and gold answers."""
    item = plan_module.Item.make
    return [
        item("easy", "simpleqa", 0, Question(question="Easy?", answer="gold")),
        item("hard", "sealqa-hard", 0, Question(question="Hard?", answer="gold")),
        item("synthesis", "frames", 0, Question(question="Many pages?", answer="gold")),
        item(
            "url-given",
            "frames",
            1,
            Question(question="Read these?", answer="gold"),
            ["https://a.org", "https://b.org"],
        ),
    ]


def _args(out: Path, budget: float, concurrency: int = 1) -> argparse.Namespace:
    return argparse.Namespace(out=out, budget=budget, concurrency=concurrency, research_timeout=1.0, local={})


# The plan


def test_the_dry_run_plans_the_approved_sample_and_paths(tools_effort: ModuleType) -> None:
    items = tools_effort.placeholder_sample()
    tasks = tools_effort.plan(items)
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

    text = tools_effort.dry_run(items, 125.0)
    assert "60 questions, 1100 rows" in text
    assert "research 600" in text and "answer 310" in text and "search 180" in text


def test_the_runner_works_through_whole_columns_cheapest_research_first(tools_effort: ModuleType) -> None:
    tasks = tools_effort.order(tools_effort.plan(tools_effort.placeholder_sample()))
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


# What a call costs


def test_an_unknown_price_is_never_counted_as_nothing(tools_effort: ModuleType) -> None:
    assert prices.estimate("parallel", "answer") is None, "the case this guards: no list price"
    parallel = tools_effort.call_cost("parallel", "answer")
    assert parallel.worst > 0 and parallel.expected > 0
    assert tools_effort.list_price("parallel", "answer") is None, "the frozen row says unknown, not $0"
    assert tools_effort.call_cost("nobody", "research").worst == tools_effort.FALLBACK["research"]
    for task in tools_effort.plan(tools_effort.placeholder_sample()):
        for call in tools_effort.backend_calls(task):
            cost = tools_effort.call_cost(*call)
            assert cost.worst >= cost.expected > 0, call


def test_a_call_with_no_price_is_charged_its_fallback_and_its_row_records_no_cost(
    tools_effort: ModuleType, plan_module: ModuleType
) -> None:
    item = _items(plan_module)[1]
    task = plan_module.Task(item, "answer", "parallel", "default")
    budget = tools_effort.Budget(cap=10.0)
    ticket = tools_effort.Ticket(budget, task)
    row = asyncio.run(tools_effort.Runner(FakeRouter(), FakeJudge(cost=None)).row(task, ticket))
    ticket.close()

    assert row["grade"] == "correct"
    assert row["cost_usd"] is None
    assert row["spend_usd"] == tools_effort.call_cost("parallel", "answer").worst
    assert row["judge_spend_usd"] > 0, "a judge call that reports no usage is charged its worst case"
    assert budget.spent == pytest.approx(row["spend_usd"] + row["judge_spend_usd"])
    assert budget.reserved == pytest.approx(0.0)


def test_a_stated_cost_is_what_is_charged_and_recorded(tools_effort: ModuleType, plan_module: ModuleType) -> None:
    task = plan_module.Task(_items(plan_module)[0], "research", "perplexity", "low")
    budget = tools_effort.Budget(cap=10.0)
    ticket = tools_effort.Ticket(budget, task)
    row = asyncio.run(tools_effort.Runner(FakeRouter(stated=0.0123), FakeJudge()).row(task, ticket))

    assert (row["cost_usd"], row["spend_usd"], row["judge_spend_usd"]) == (0.0123, 0.0123, 0.001)


# The budget


def test_the_budget_refuses_the_row_that_would_cross_it_and_calls_nothing_for_it(
    tools_effort: ModuleType, plan_module: ModuleType
) -> None:
    item = _items(plan_module)[0]
    tasks = [plan_module.Task(item, "research", "parallel", "max")] * 5
    worst = tools_effort.task_cost(tasks[0]).worst
    budget = tools_effort.Budget(cap=worst * 2.5)
    router, rows = FakeRouter(), []
    runner = tools_effort.Runner(router, FakeJudge(cost=None))
    finished, stopped = asyncio.run(
        tools_effort.execute(tasks, runner.row, budget, rows.append, concurrency=1, say=lambda _: None)
    )

    # Each row is charged its worst case (no stated cost, no judge usage), so a third would pass the cap.
    assert finished == 2 and len(rows) == 2
    assert router.calls["research"] == 2
    assert stopped is not None and "budget" in stopped
    assert budget.spent <= budget.cap
    assert budget.reserved == pytest.approx(0.0)


def test_rows_in_flight_reserve_their_worst_case_together(tools_effort: ModuleType, plan_module: ModuleType) -> None:
    task = plan_module.Task(_items(plan_module)[0], "research", "nimble", "high")
    budget = tools_effort.Budget(cap=tools_effort.task_cost(task).worst * 1.5)
    tools_effort.Ticket(budget, task)

    with pytest.raises(tools_effort.BudgetExceeded):
        tools_effort.Ticket(budget, task)
    assert budget.spent == 0.0


def test_a_call_dearer_than_planned_tops_up_or_is_refused(tools_effort: ModuleType, plan_module: ModuleType) -> None:
    task = plan_module.Task(_items(plan_module)[0], "answer", "you", "default")
    planned = tools_effort.task_cost(task).worst
    budget = tools_effort.Budget(cap=planned + 0.01)
    ticket = tools_effort.Ticket(budget, task)

    ticket.draw(planned + 0.005)
    assert budget.reserved == pytest.approx(planned + 0.005)
    with pytest.raises(tools_effort.BudgetExceeded):
        ticket.draw(1.0)


# Resuming


def test_a_rerun_skips_the_rows_already_written_and_counts_what_was_spent(
    tools_effort: ModuleType, plan_module: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    items = _items(plan_module)
    monkeypatch.setattr(tools_effort, "load_sample", lambda local: items)
    total = len(tools_effort.plan(items))
    first_router, second_router = FakeRouter(), FakeRouter()

    first = asyncio.run(tools_effort.run(_args(tmp_path, budget=1.0), FakeJudge(), first_router))
    spent = sum(e["usd"] for e in tools_effort.read_jsonl(tmp_path / tools_effort.SPEND_FILE))
    second = asyncio.run(tools_effort.run(_args(tmp_path, budget=100.0), FakeJudge(), second_router))
    third_router, third_judge = FakeRouter(), FakeJudge()
    third = asyncio.run(tools_effort.run(_args(tmp_path, budget=100.0), third_judge, third_router))

    rows = tools_effort.read_jsonl(tmp_path / tools_effort.ROWS_FILE)
    assert 0 < first < total, "the first run stopped at its budget"
    assert 0 < spent <= 1.0
    assert first + second == total == len(rows)
    assert len(tools_effort.done_keys(rows)) == total, "no row asked twice"
    assert third == 0 and not third_router.calls, "a finished run calls nothing"
    assert third_judge.calls == 0, "not even the judge's check"
    checks = [e for e in tools_effort.read_jsonl(tmp_path / tools_effort.SPEND_FILE) if e["path"] == "judge-check"]
    assert [e["usd"] for e in checks] == [0.001, 0.001], "each run that asked something paid for its check"
    run = json.loads((tmp_path / tools_effort.RUN_FILE).read_text(encoding="utf-8"))
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


def test_a_leaked_research_answer_is_not_graded(tools_effort: ModuleType, plan_module: ModuleType) -> None:
    class LeakyRouter(FakeRouter):
        async def research(self, query: str, *, provider: str, effort: str, timeout: float) -> ResearchResponse:
            citations = [Citation(url="https://huggingface.co/datasets/vtllms/sealqa")]
            return ResearchResponse(provider=provider, query=query, answer="gold", citations=citations)

    task = plan_module.Task(_items(plan_module)[1], "research", "parallel", "low")
    judge = FakeJudge()
    ticket = tools_effort.Ticket(tools_effort.Budget(cap=10.0), task)
    row = asyncio.run(tools_effort.Runner(LeakyRouter(), judge).row(task, ticket))

    assert row["leaked"] and row["grade"] is None and judge.calls == 0


def test_search_then_read_reads_the_top_three_pages_and_judges_once(
    tools_effort: ModuleType, plan_module: ModuleType
) -> None:
    task = plan_module.Task(_items(plan_module)[0], "search+extract", "linkup", "top3")
    router, judge = FakeRouter(), FakeJudge()
    row = asyncio.run(
        tools_effort.Runner(router, judge).row(task, tools_effort.Ticket(tools_effort.Budget(10.0), task))
    )

    assert router.calls == {"search": 1, "extract": 3}
    assert row["pages_read"] == 3 and row["grade"] == "correct" and judge.calls == 2
    assert row["cost_usd"] == pytest.approx(
        prices.estimate("linkup", "search") + 3 * prices.estimate("nimble", "extract")
    )


# Freezing


def test_a_finished_run_freezes_with_the_reused_answers_and_an_unfinished_one_is_refused(
    frozen: ModuleType,
    tools_effort: ModuleType,
    plan_module: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    items = _items(plan_module)
    monkeypatch.setattr(tools_effort, "load_sample", lambda local: items)
    asyncio.run(tools_effort.run(_args(tmp_path / "run", budget=100.0), FakeJudge(), FakeRouter()))
    easy = items[0].qid
    reused = [
        {
            "set": "answer-simpleqa-300",
            "question_id": qid,
            "provider": provider,
            "method": "answer",
            "mode": "default",
            "grade": "correct",
            "latency_s": 1.0,
            "cost_usd": None,
            "error": None,
        }
        for qid in (easy, "someotherquestion")
        for provider in ("perplexity", "you", "parallel", "exa", "linkup")
    ]
    sets = {"mixed-tiny": {"questions": "tools-effort", "method": frozen.MIXED, "runs": ["run"]}}
    monkeypatch.setattr(frozen, "FROZEN_SETS", sets)

    rows, entry = frozen.freeze_set("mixed-tiny", tmp_path, {"answer-simpleqa-300": reused})
    copied = {r["provider"]: r["cost_usd"] for r in rows if r["method"] == "answer" and r["provider"] != "brave"}
    # A copied answer with no stated cost gets its list price, as the set's own rows do; token-billed stays unknown.
    assert copied == {"linkup": 0.006, "you": 0.005, "exa": 0.005, "perplexity": None, "parallel": None}

    assert len(rows) == len(tools_effort.plan(items)) + 5
    assert {r["method"] for r in rows} == {"search+extract", "extract", "answer", "research"}
    assert all(tuple(r) == frozen.FIELDS and r["set"] == "mixed-tiny" for r in rows)
    assert all(r["grade"] == "correct" for r in rows)
    assert entry["question_count"] == 4 and entry["method"] == frozen.MIXED
    assert not any("question" in e for e in entry["sample"])
    with pytest.raises(SystemExit, match="planned rows are missing"):
        frozen.freeze_set("mixed-tiny", tmp_path, {"answer-simpleqa-300": reused[5:]})
    stray = {"question_id": items[0].qid, "path": "research", "provider": "nimble", "mode": "max", "grade": "correct"}
    with (tmp_path / "run" / "rows.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(stray) + "\n")
    with pytest.raises(SystemExit, match="1 rows are not in the run's plan"):
        frozen.freeze_set("mixed-tiny", tmp_path, {"answer-simpleqa-300": reused})


def test_join_finds_a_mixed_sets_questions_at_their_rows_in_the_public_sets(
    frozen: ModuleType, plan_module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    from web_router.evals import Dataset

    public = Dataset(name="frames", questions=[Question(question=f"q{i}", answer=f"a{i}") for i in range(3)])
    monkeypatch.setattr(Dataset, "for_set", classmethod(lambda cls, name, path=None: public))
    good = plan_module.Item.make("url-given", "frames", 2, public.questions[2], ["https://a.org"]).entry()
    moved = {**plan_module.Item.make("synthesis", "frames", 1, public.questions[0]).entry()}

    index = frozen.sample_index([good, moved], {})

    assert set(index) == {good["question_id"]}, "a question no longer at its row is left unresolved"
    assert index[good["question_id"]].metadata == {"group": "url-given", "source": "frames", "urls": ["https://a.org"]}


# What a run never loses or pays twice


def test_a_failing_row_stops_new_rows_and_is_raised_once_the_rest_finish(
    tools_effort: ModuleType, plan_module: ModuleType
) -> None:
    tasks = [plan_module.Task(_items(plan_module)[0], "research", "parallel", "low")] * 5
    router = FakeRouter()
    budget = tools_effort.Budget(cap=100.0)

    def broken_sink(row: dict[str, Any]) -> None:
        raise OSError("disk full")

    runner = tools_effort.Runner(router, FakeJudge())
    with pytest.raises(OSError, match="disk full"):
        asyncio.run(tools_effort.execute(tasks, runner.row, budget, broken_sink, concurrency=1, say=lambda _: None))
    assert router.calls["research"] == 1, "no row started after the one that failed"
    assert budget.reserved == pytest.approx(0.0)


def test_a_row_whose_grading_the_budget_refuses_is_kept_and_never_bought_again(
    tools_effort: ModuleType,
    frozen: ModuleType,
    plan_module: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    items = _items(plan_module)[:1]
    monkeypatch.setattr(tools_effort, "load_sample", lambda local: items)
    task = plan_module.Task(items[0], "research", "parallel", "max")
    budget = tools_effort.Budget(cap=tools_effort.task_cost(task).worst)
    monkeypatch.setattr(tools_effort, "judge_worst", lambda system, user: 1.0)  # a prompt dearer than planned
    router, rows = FakeRouter(), []
    finished, stopped = asyncio.run(
        tools_effort.execute([task], tools_effort.Runner(router, FakeJudge()).row, budget, rows.append, say=print)
    )

    (row,) = rows
    assert finished == 1 and stopped is not None and "budget" in stopped
    assert (row["grade"], row["failed_at"], row["answer"]) == (None, "budget", "gold")
    assert row["spend_usd"] == tools_effort.list_price("parallel", "research", "max") and row["latency"] is not None
    assert frozen.error_class(row) == "budget"
    assert task.key in tools_effort.done_keys(rows), "a resume skips it"


def test_a_failed_page_read_counts_in_the_rows_cost_and_time(tools_effort: ModuleType, plan_module: ModuleType) -> None:
    class FlakyRouter(FakeRouter):
        async def extract(self, url: str, *, provider: str) -> ExtractResponse:
            if url.endswith("/1"):
                self.calls["extract"] += 1
                raise RuntimeError("HTTP 500")
            return await super().extract(url, provider=provider)

    task = plan_module.Task(_items(plan_module)[0], "search+extract", "nimble", "top3")
    router = FlakyRouter()
    row = asyncio.run(
        tools_effort.Runner(router, FakeJudge()).row(task, tools_effort.Ticket(tools_effort.Budget(10.0), task))
    )

    assert router.calls["extract"] == 3 and row["pages_read"] == 2 and row["grade"] == "correct"
    assert row["cost_usd"] == pytest.approx(
        prices.estimate("nimble", "search") + 3 * prices.estimate("nimble", "extract")
    )
