"""The tool and effort runner's costs and budget: what a call is charged, what a row reserves, and top-ups."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import ModuleType

import pytest

from tests.benchmarks._tools_effort_fakes import FakeJudge, FakeRouter, _items
from web_router import prices

# What a call costs


def test_an_unknown_price_is_never_counted_as_nothing(
    budget_module: ModuleType, run_module: ModuleType, plan_module: ModuleType
) -> None:
    assert prices.estimate("parallel", "answer") is None, "the case this guards: no list price"
    parallel = budget_module.call_cost("parallel", "answer")
    assert parallel.worst > 0 and parallel.expected > 0
    assert budget_module.list_price("parallel", "answer") is None, "the frozen row says unknown, not $0"
    assert budget_module.call_cost("nobody", "research").worst == budget_module.FALLBACK["research"]
    for task in plan_module.plan(run_module.placeholder_sample()):
        for call in budget_module.backend_calls(task):
            cost = budget_module.call_cost(*call)
            assert cost.worst >= cost.expected > 0, call


def test_a_call_with_no_price_is_charged_its_fallback_and_its_row_records_no_cost(
    plan_module: ModuleType, budget_module: ModuleType, runner_module: ModuleType
) -> None:
    item = _items(plan_module)[1]
    task = plan_module.Task(item, "answer", "parallel", "default")
    budget = budget_module.Budget(cap=10.0)
    ticket = budget_module.Ticket(budget, task)
    row = asyncio.run(runner_module.Runner(FakeRouter(), FakeJudge(cost=None)).row(task, ticket))
    ticket.close()

    assert row["grade"] == "correct"
    assert row["cost_usd"] is None
    assert row["spend_usd"] == budget_module.call_cost("parallel", "answer").worst
    assert row["judge_spend_usd"] > 0, "a judge call that reports no usage is charged its worst case"
    assert budget.spent == pytest.approx(row["spend_usd"] + row["judge_spend_usd"])
    assert budget.reserved == pytest.approx(0.0)


def test_a_stated_cost_is_what_is_charged_and_recorded(
    plan_module: ModuleType, budget_module: ModuleType, runner_module: ModuleType
) -> None:
    task = plan_module.Task(_items(plan_module)[0], "research", "perplexity", "low")
    budget = budget_module.Budget(cap=10.0)
    ticket = budget_module.Ticket(budget, task)
    row = asyncio.run(runner_module.Runner(FakeRouter(stated=0.0123), FakeJudge()).row(task, ticket))

    assert (row["cost_usd"], row["spend_usd"], row["judge_spend_usd"]) == (0.0123, 0.0123, 0.001)


# The budget


def test_the_budget_refuses_the_row_that_would_cross_it_and_calls_nothing_for_it(
    tools_effort: ModuleType, plan_module: ModuleType, budget_module: ModuleType, runner_module: ModuleType
) -> None:
    item = _items(plan_module)[0]
    tasks = [plan_module.Task(item, "research", "parallel", "max")] * 5
    worst = budget_module.task_cost(tasks[0]).worst
    budget = budget_module.Budget(cap=worst * 2.5)
    router, rows = FakeRouter(), []
    runner = runner_module.Runner(router, FakeJudge(cost=None))
    finished, stopped = asyncio.run(
        tools_effort.execute(tasks, runner.row, budget, rows.append, concurrency=1, say=lambda _: None)
    )

    # Each row is charged its worst case (no stated cost, no judge usage), so a third would pass the cap.
    assert finished == 2 and len(rows) == 2
    assert router.calls["research"] == 2
    assert stopped is not None and "budget" in stopped
    assert budget.spent <= budget.cap
    assert budget.reserved == pytest.approx(0.0)


def test_rows_in_flight_reserve_their_worst_case_together(plan_module: ModuleType, budget_module: ModuleType) -> None:
    task = plan_module.Task(_items(plan_module)[0], "research", "nimble", "high")
    budget = budget_module.Budget(cap=budget_module.task_cost(task).worst * 1.5)
    budget_module.Ticket(budget, task)

    with pytest.raises(budget_module.BudgetExceeded):
        budget_module.Ticket(budget, task)
    assert budget.spent == 0.0


def test_a_call_dearer_than_planned_tops_up_or_is_refused(plan_module: ModuleType, budget_module: ModuleType) -> None:
    task = plan_module.Task(_items(plan_module)[0], "answer", "you", "default")
    planned = budget_module.task_cost(task).worst
    budget = budget_module.Budget(cap=planned + 0.01)
    ticket = budget_module.Ticket(budget, task)

    ticket.draw(planned + 0.005)
    assert budget.reserved == pytest.approx(planned + 0.005)
    with pytest.raises(budget_module.BudgetExceeded):
        ticket.draw(1.0)


# What a run never pays twice


def test_a_row_whose_grading_the_budget_refuses_is_kept_and_never_bought_again(
    tools_effort: ModuleType,
    frozen: ModuleType,
    plan_module: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    budget_module: ModuleType,
    runner_module: ModuleType,
    run_module: ModuleType,
) -> None:
    items = _items(plan_module)[:1]
    monkeypatch.setattr(tools_effort, "load_sample", lambda local: items)
    task = plan_module.Task(items[0], "research", "parallel", "max")
    budget = budget_module.Budget(cap=budget_module.task_cost(task).worst)
    monkeypatch.setattr(runner_module, "judge_worst", lambda system, user: 1.0)  # a prompt dearer than planned
    router, rows = FakeRouter(), []
    finished, stopped = asyncio.run(
        tools_effort.execute([task], runner_module.Runner(router, FakeJudge()).row, budget, rows.append, say=print)
    )

    (row,) = rows
    assert finished == 1 and stopped is not None and "budget" in stopped
    assert (row["grade"], row["failed_at"], row["answer"]) == (None, "budget", "gold")
    assert row["spend_usd"] == budget_module.list_price("parallel", "research", "max") and row["latency"] is not None
    assert frozen.error_class(row) == "budget"
    assert task.key in run_module.done_keys(rows), "a resume skips it"
