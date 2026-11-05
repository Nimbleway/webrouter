"""The tool and effort set: the same questions through search then read, extract, answer and research at each effort.

Each question goes through every path a router could pick for it, and every
row is graded by the judge the published sets use (``openai:gpt-4o``, the
SimpleQA grader), so route-eval can replay which tool, and which research
effort, a question was worth:

- ``search+extract``: a search (Nimble, Perplexity, Linkup; five results), the
  top three pages read with Nimble extract, and the judge's answer from the
  snippets and the pages.
- ``extract``: only for a question given its pages -- Nimble extract of those
  pages, and the judge's answer from them.
- ``answer``: each answer backend's own answer.
- ``research``: each research backend at each effort the set covers.

    uv run --all-extras python benchmarks/providers/tools_effort.py --dry-run
    uv run --all-extras python benchmarks/providers/tools_effort.py --budget 125

Every call is paid for, so the run holds a hard budget. Before a row starts,
its worst case is reserved; before each call, that call's worst case is drawn
from the reservation, topped up when the call turns out dearer; the row is
refused, and the run stops, when what is spent plus what is reserved would
pass ``--budget``. A call costs what its response states, else its list price
from :mod:`web_router.prices`, else the stated fallback in ``_tools_effort_budget.py`` -- never $0. The
judge's own calls count, at gpt-4o's list price on the tokens OpenAI reports.

Rows are appended to ``rows.jsonl`` as they finish, and every call's cost to
``spend.jsonl``. Running the same command again skips the finished rows and
counts what was spent before against the same budget. ``frozen.py build`` turns
a finished run into the frozen set.

Experimental until 1.0: the set, its paths and its files may change.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from _common import DEFAULT_OUT, ENV_FILE, _local
from _tools_effort_budget import Budget, BudgetExceeded, Ticket, judge_worst
from _tools_effort_plan import SET_NAME, Task, plan
from _tools_effort_run import (
    ROWS_FILE,
    SPEND_FILE,
    _appender,
    done_keys,
    dry_run,
    load_sample,
    missing_keys,
    open_run,
    order,
    placeholder_sample,
    read_jsonl,
    setup_of,
)
from _tools_effort_runner import BUDGET_STAGE, RESEARCH_TIMEOUT, Asker, MeteredJudge, Runner
from dotenv import load_dotenv

from web_router.evals.judge import grader_prompt

DEFAULT_BUDGET = 125.0
DEFAULT_CONCURRENCY = 6
"""Rows in flight at once. A research row takes minutes, so this is mostly how many research runs overlap."""
DEFAULT_OUT_DIR = DEFAULT_OUT / SET_NAME


@dataclass
class _Rows:
    """What :func:`execute` shares with its rows in flight: the gate, the rows done, and why it stopped."""

    total: int
    row_of: Callable[[Task, Ticket], Awaitable[dict[str, Any]]]
    budget: Budget
    sink: Callable[[dict[str, Any]], None]
    say: Callable[[str], None]
    gate: asyncio.Semaphore
    stopped: list[str] = field(default_factory=list)
    failures: list[BaseException] = field(default_factory=list)
    finished: int = 0

    async def one(self, task: Task, ticket: Ticket) -> None:
        """One row, written, counted and reported; a budget refusal stops new rows, any other error is kept."""
        budget = self.budget
        try:
            try:
                row = await self.row_of(task, ticket)
            finally:
                ticket.close()
            self.sink(row)
            self.finished += 1
            if row["failed_at"] == BUDGET_STAGE:
                self.stopped.append(f"{row['error']}; the row is kept ungraded, and a resume does not ask it again")
            outcome = row["grade"] or ("leaked" if row["leaked"] else f"error at {row['failed_at']}")
            self.say(
                f"[{self.finished}/{self.total}] {task.path} {task.provider}:{task.mode} {task.item.qid}: {outcome}"
                f" | spent ${budget.spent:.2f}, reserved ${budget.reserved:.2f}, budget ${budget.cap:.2f}"
            )
        except BudgetExceeded as exc:
            self.stopped.append(f"{exc}; that row is asked again on resume")
        except BaseException as exc:
            self.failures.append(exc)
            raise
        finally:
            # Released only once the row is written, so a failure stops the next row from starting.
            self.gate.release()


async def execute(
    tasks: list[Task],
    row_of: Callable[[Task, Ticket], Awaitable[dict[str, Any]]],
    budget: Budget,
    sink: Callable[[dict[str, Any]], None],
    concurrency: int = DEFAULT_CONCURRENCY,
    say: Callable[[str], None] = print,
) -> tuple[int, str | None]:
    """Every task, ``concurrency`` at a time, each row reserved before it starts; the rows done, and why it stopped.

    The first refusal stops new rows, and so does the first row that raises (a bug, or a sink that cannot write):
    rows in flight finish and are kept, then that error is raised.
    """
    rows = _Rows(len(tasks), row_of, budget, sink, say, asyncio.Semaphore(max(1, concurrency)))
    running: set[asyncio.Task[None]] = set()
    for task in tasks:
        await rows.gate.acquire()
        if rows.stopped or rows.failures:
            rows.gate.release()
            break
        try:
            ticket = Ticket(budget, task)
        except BudgetExceeded as exc:
            rows.gate.release()
            rows.stopped.append(str(exc))
            break
        running.add(asyncio.create_task(rows.one(task, ticket)))
    # Every row in flight finishes before anything is raised, so none is lost to another's bug.
    await asyncio.gather(*running, return_exceptions=True)
    if rows.failures:
        raise rows.failures[0]
    return rows.finished, rows.stopped[0] if rows.stopped else None


JUDGE_PROBE = ("What is 2 + 2?", "4", "4")
"""One question the judge must grade before any backend is asked."""


async def check_judge(judge: Asker, budget: Budget) -> None:
    """Grade one known answer, within the budget, so an expired key or a missing model costs no backend call."""
    question, target, predicted = JUDGE_PROBE
    user = grader_prompt().format(question=question, target=target, predicted_answer=predicted)
    worst = judge_worst("", user)
    try:
        budget.reserve(worst, "the judge's check")
    except BudgetExceeded as exc:
        raise SystemExit(f"Nothing was asked: {exc}") from exc
    entry = {"question_id": None, "path": "judge-check", "provider": judge.label, "mode": None, "kind": "judge"}
    try:
        _, cost = await judge.ask("", user)
    except Exception as exc:  # whatever the judge's SDK raises, the run must stop on it
        budget.release(worst)
        budget.charge(worst, {**entry, "basis": "failed"})
        raise SystemExit(f"The judge could not grade a test answer, so nothing was asked: {exc}") from exc
    budget.release(worst)
    budget.charge(worst if cost is None else cost, {**entry, "basis": "fallback" if cost is None else "usage"})


async def run(args: argparse.Namespace, judge: Asker | None = None, router: Any = None) -> int:
    """The run: the sample, then every row not yet in ``--out``, within ``--budget``. The rows written this time."""
    items = load_sample(args.local)
    tasks = order(plan(items))
    missing = missing_keys(tasks)
    if missing and router is None:
        raise SystemExit(f"Set these before the run: {', '.join(missing)} (see .env.example); nothing was called")
    judge = judge or MeteredJudge()
    judge.check()
    setup = open_run(args.out, setup_of(items, judge.label, args))
    done = done_keys(read_jsonl(args.out / ROWS_FILE))
    todo = [t for t in tasks if t.key not in done]
    spent = sum(float(e["usd"]) for e in read_jsonl(args.out / SPEND_FILE))
    budget = Budget(cap=args.budget, spent=spent, on_spend=_appender(args.out / SPEND_FILE))
    print(
        f"{SET_NAME}: {len(items)} questions, {len(tasks)} rows, {len(done)} done, {len(todo)} to ask"
        f" -> {args.out}; ${spent:.2f} spent before, budget ${args.budget:.2f}, judge {setup['judge']}"
    )
    if not todo:
        await judge.aclose()
        return 0
    try:
        await check_judge(judge, budget)
    except BaseException:
        await judge.aclose()
        raise
    sink = _appender(args.out / ROWS_FILE)
    try:
        if router is None:
            from web_router import AsyncWebRouter

            async with AsyncWebRouter() as owned:
                finished, stopped = await execute(
                    todo, Runner(owned, judge, args.research_timeout).row, budget, sink, args.concurrency
                )
        else:
            finished, stopped = await execute(
                todo, Runner(router, judge, args.research_timeout).row, budget, sink, args.concurrency
            )
    finally:
        await judge.aclose()
    print(f"{finished} rows written; ${budget.spent:.2f} spent of ${budget.cap:.2f}")
    if stopped:
        print(f"Stopped by the budget: {stopped}. Raise --budget and run again to go on.")
    return finished


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="print the plan and its cost; call nothing")
    parser.add_argument("--budget", type=float, default=DEFAULT_BUDGET, help="US dollars the whole run may spend")
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY, help="rows in flight at once")
    parser.add_argument("--research-timeout", type=float, default=RESEARCH_TIMEOUT, help="seconds per research run")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR, help="the run's folder; run again to resume it")
    parser.add_argument(
        "--dataset",
        action="append",
        default=[],
        metavar="NAME=PATH",
        help="a local copy of a question set (simpleqa, sealqa-hard, frames) in place of the download",
    )
    args = parser.parse_args(argv)
    args.local = _local(args.dataset)
    return args


def main(argv: list[str] | None = None) -> None:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.dry_run:
        print(dry_run(placeholder_sample(), args.budget))
        return
    # An entry point, as the scans are: it reads the repo's .env, and a variable already exported wins.
    if ENV_FILE.is_file():
        load_dotenv(ENV_FILE)
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
