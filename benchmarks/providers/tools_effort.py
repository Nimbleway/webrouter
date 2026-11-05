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
from :mod:`web_router.prices`, else the stated fallback below -- never $0. The
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
import json
import os
import sys
import time
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from _common import DEFAULT_OUT, ENV_FILE, _local
from _frozen_checks import answers_digest
from _tools_effort_plan import (
    ANSWER,
    DATASET,
    EASY,
    EXTRACT,
    FRAMES,
    HARD,
    MAX_RESULTS,
    PAGES_READ,
    PATHS,
    QUESTIONS,
    READER,
    RESEARCH,
    SEALQA_HARD,
    SEARCH_EXTRACT,
    SET_NAME,
    SIMPLEQA,
    SIZES,
    SYNTHESIS,
    URL_GIVEN,
    URL_GIVEN_SIZES,
    Item,
    Task,
    build_sample,
    plan,
)
from answer_report import stated_cost
from dotenv import load_dotenv

from web_router import prices
from web_router.constants import (
    ENV_BRAVE_ANSWER_API_KEY,
    ENV_EXA_API_KEY,
    ENV_LINKUP_API_KEY,
    ENV_NIMBLE_API_KEY,
    ENV_OPENAI_API_KEY,
    ENV_PARALLEL_API_KEY,
    ENV_PERPLEXITY_API_KEY,
    ENV_YOU_API_KEY,
    EVALS_RESULT_CHARS,
    EVALS_RESULT_FORMAT,
    EVALS_SYNTHESIS_PROMPT,
    EVALS_SYNTHESIS_USER,
    SEALQA_WITHHELD_MARKERS,
)
from web_router.evals import Dataset, OpenAIJudge
from web_router.evals.judge import grader_prompt, parse_grade
from web_router.integrations._render import cut_to_budget

DEFAULT_BUDGET = 125.0
DEFAULT_CONCURRENCY = 6
"""Rows in flight at once. A research row takes minutes, so this is mostly how many research runs overlap."""
RESEARCH_TIMEOUT = 1200.0
"""Seconds a research run may take: twice ``research()``'s default, as a Linkup run can take a quarter of an hour."""
PAGE_CHARS = 8000
"""How much of a read page the judge sees; a search snippet keeps the published sets' 1,500 characters."""
DEFAULT_OUT_DIR = DEFAULT_OUT / SET_NAME

ROWS_FILE, SPEND_FILE, RUN_FILE = "rows.jsonl", "spend.jsonl", "run.json"


# What a call costs


@dataclass(frozen=True)
class Cost:
    """One call's cost: the most it may bill, which the budget reserves, and what it should bill, for the plan."""

    worst: float
    expected: float


UNPRICED: dict[tuple[str, str, str | None], Cost] = {
    ("perplexity", "answer", None): Cost(0.02, 0.0012),
    ("brave", "answer", None): Cost(0.06, 0.054),
    ("parallel", "answer", None): Cost(0.06, 0.06),
    ("perplexity", "research", "low"): Cost(0.10, 0.02),
    ("perplexity", "research", "medium"): Cost(0.25, 0.05),
}
"""Calls with no fixed list price. Perplexity bills tokens (its answers stated about $0.0012 in the answer scan),
Brave adds tokens to $0.004 a request (it stated $0.054 for one answer) and Parallel's answer price is unknown, so
it is charged the dearest answer seen. A call that states its cost is charged that instead."""
FALLBACK: dict[str, float] = {"search": 0.05, "extract": 0.01, "answer": 0.06, "research": 2.50}
"""Any other call with no price: ten times a list search or extract, the dearest answer, the dearest research run."""
JUDGE_INPUT_USD = 2.50 / 1_000_000
JUDGE_OUTPUT_USD = 10.00 / 1_000_000
"""gpt-4o's list price per token, input and output (OpenAI's pricing page; not in prices.json)."""
JUDGE_CHARS_PER_TOKEN = 3
JUDGE_REPLY_TOKENS = 1000
"""A judge call's worst case before it is sent: its prompt at three characters a token, and a 1,000-token reply."""
JUDGE_COST = {"synthesis": Cost(0.05, 0.025), "grade": Cost(0.02, 0.005), "grade_research": Cost(0.10, 0.03)}
"""What a row reserves for its judge before the backend answers; each call's own prompt tops it up if larger."""


def call_cost(provider: str, method: str, mode: str | None = None) -> Cost:
    """A call's worst and expected cost: :data:`UNPRICED`, else the list price, else :data:`FALLBACK` -- never 0."""
    if (provider, method, mode) in UNPRICED:
        return UNPRICED[(provider, method, mode)]
    price = prices.estimate(provider, method, mode)
    if price is None:
        return Cost(FALLBACK[method], FALLBACK[method])
    return Cost(price, price)


def list_price(provider: str, method: str, mode: str | None = None) -> float | None:
    """The list price a frozen row records when the response states none; ``None`` when there is no fixed one."""
    return None if (provider, method, mode) in UNPRICED else prices.estimate(provider, method, mode)


def _mode(task: Task) -> str | None:
    return task.mode if task.path == RESEARCH else None


def backend_calls(task: Task) -> list[tuple[str, str, str | None]]:
    """The (provider, method, mode) of every backend call a row makes, at most."""
    if task.path == SEARCH_EXTRACT:
        return [(task.provider, "search", None), *[(READER, "extract", None)] * PAGES_READ]
    if task.path == EXTRACT:
        return [(READER, "extract", None)] * len(task.item.urls)
    return [(task.provider, task.path, _mode(task))]


def judge_calls(task: Task) -> list[str]:
    if task.path in (SEARCH_EXTRACT, EXTRACT):
        return ["synthesis", "grade"]
    return ["grade_research" if task.path == RESEARCH else "grade"]


def task_cost(task: Task) -> Cost:
    """A row's worst and expected cost: its backend calls and its judge calls."""
    costs = [call_cost(*call) for call in backend_calls(task)] + [JUDGE_COST[c] for c in judge_calls(task)]
    return Cost(sum(c.worst for c in costs), sum(c.expected for c in costs))


def judge_worst(system: str, user: str) -> float:
    tokens = (len(system) + len(user)) / JUDGE_CHARS_PER_TOKEN
    return tokens * JUDGE_INPUT_USD + JUDGE_REPLY_TOKENS * JUDGE_OUTPUT_USD


# The budget


class BudgetExceeded(Exception):
    """A row or call that would take the run past its budget; nothing was sent for it."""


@dataclass
class Budget:
    """What the run may spend, has spent and holds reserved for rows in flight.

    ``spent`` starts from what earlier runs of the same folder spent, so a
    resumed run keeps to the same cap.

    A call is charged what it actually billed. One that bills more than the
    worst case reserved for it -- a token-billed backend stating a dearer
    cost -- can take ``spent`` past ``cap`` by at most what the rows in flight
    overrun; the next row is then refused.
    """

    cap: float
    spent: float = 0.0
    reserved: float = 0.0
    on_spend: Callable[[dict[str, Any]], None] | None = None

    def reserve(self, amount: float, what: str) -> None:
        if self.spent + self.reserved + amount > self.cap + 1e-9:
            raise BudgetExceeded(
                f"{what} needs up to ${amount:.4f}, but ${self.spent:.2f} is spent and ${self.reserved:.2f}"
                f" reserved of the ${self.cap:.2f} budget"
            )
        self.reserved += amount

    def release(self, amount: float) -> None:
        self.reserved = max(0.0, self.reserved - amount)

    def charge(self, amount: float, entry: dict[str, Any]) -> None:
        self.spent += amount
        if self.on_spend is not None:
            self.on_spend({**entry, "usd": amount})


@dataclass
class Ticket:
    """One row's reservation: taken whole before the row starts, drawn from call by call, released when it ends.

    A call is charged its actual cost even when that is more than it drew; see :class:`Budget` for what that means
    for the cap."""

    budget: Budget
    task: Task
    left: float = 0.0
    spent: dict[str, float] = field(default_factory=lambda: {"backend": 0.0, "judge": 0.0})

    def __post_init__(self) -> None:
        amount = task_cost(self.task).worst
        self.budget.reserve(amount, _name(self.task))
        self.left = amount

    def draw(self, worst: float) -> float:
        """Hold ``worst`` for one call, topping the reservation up when it is short; refused past the budget."""
        if worst > self.left:
            self.budget.reserve(worst - self.left, f"{_name(self.task)} (a dearer call than planned)")
            self.left = worst
        self.left -= worst
        return worst

    def pay(self, drawn: float, actual: float, kind: str, basis: str) -> None:
        self.budget.release(drawn)
        self.spent[kind] += actual
        provider, mode = self.task.provider, self.task.mode
        entry = {"question_id": self.task.item.qid, "path": self.task.path, "provider": provider, "mode": mode}
        self.budget.charge(actual, {**entry, "kind": kind, "basis": basis})

    def close(self) -> None:
        self.budget.release(self.left)
        self.left = 0.0


def _name(task: Task) -> str:
    return f"{task.path} {task.provider}:{task.mode} on {task.item.qid}"


# The judge


class Asker(Protocol):
    label: str

    def check(self) -> None: ...

    async def ask(self, system: str, user: str) -> tuple[str, float | None]: ...

    async def aclose(self) -> None: ...


class MeteredJudge(OpenAIJudge):
    """The published sets' judge -- same model, temperature and prompts -- reporting what each call cost."""

    async def ask(self, system: str, user: str) -> tuple[str, float | None]:
        """The model's reply, and its cost at list price on the tokens OpenAI reports (``None`` if it reports none)."""
        response = await self._acreate(system, user)
        usage = getattr(response, "usage", None)
        cost = (
            None
            if usage is None
            else usage.prompt_tokens * JUDGE_INPUT_USD + usage.completion_tokens * JUDGE_OUTPUT_USD
        )
        return str(response.choices[0].message.content or ""), cost


# One row


WITHHELD = {SEALQA_HARD: Dataset(name=SEALQA_HARD, withheld_markers=SEALQA_WITHHELD_MARKERS)}
"""SealQA's own pages: kept from the judge on search+extract, and a ``leaked`` row on answer and research."""


def _withholds(item: Item, url: str) -> bool:
    dataset = WITHHELD.get(item.source)
    return dataset is not None and dataset.withholds(url)


def prompt(item: Item, path: str) -> str:
    """What a backend is asked: the question, and for an answer or research backend, the pages it was given."""
    if path == SEARCH_EXTRACT or not item.urls:
        return item.question
    return item.question + "\n\nSources: " + " ".join(item.urls)


def _sources(pages: Iterable[tuple[str, str, str, int]]) -> str:
    return "\n\n".join(
        EVALS_RESULT_FORMAT.format(title=title, url=url, text=cut_to_budget(text, chars))
        for title, url, text, chars in pages
    )


def _total(costs: list[float | None]) -> float | None:
    """The calls' summed cost, or ``None`` when one has no known cost."""
    return None if any(c is None for c in costs) else sum(c for c in costs if c is not None)


def _describe(exc: BaseException) -> str:
    return str(exc) or type(exc).__name__


BUDGET_STAGE = "budget"
"""``failed_at`` of a row whose backend answered but whose grading the budget refused; frozen as error ``budget``."""


@dataclass(frozen=True)
class Read:
    """One page read: its text (empty when it failed), its cost, its time and, when it failed, why."""

    text: str
    cost: float | None
    elapsed: float
    error: str | None = None


@dataclass
class Runner:
    """What a row needs: the router, the judge, and how long a research run may take."""

    router: Any
    judge: Asker
    research_timeout: float = RESEARCH_TIMEOUT

    async def _backend(
        self, ticket: Ticket, call: tuple[str, str, str | None], run: Callable[[], Awaitable[Any]]
    ) -> Any:
        """One paid call: its worst case drawn first, then charged what it states, its list price or the fallback."""
        cost = call_cost(*call)
        drawn = ticket.draw(cost.worst)
        try:
            response = await run()
        except BaseException:
            # A failed call may still bill: charged its worst case, never 0.
            ticket.pay(drawn, cost.worst, "backend", "failed")
            raise
        # Only answer and research payloads state a cost; a search's or a page's never does.
        stated = stated_cost(response) if isinstance(getattr(response, "raw", None), list) else None
        listed = list_price(*call)
        if stated is not None:
            ticket.pay(drawn, stated, "backend", "stated")
        elif listed is not None:
            ticket.pay(drawn, listed, "backend", "list")
        else:
            ticket.pay(drawn, cost.worst, "backend", "fallback")
        return response, stated if stated is not None else listed

    async def _judge(self, ticket: Ticket, system: str, user: str) -> str:
        drawn = ticket.draw(judge_worst(system, user))
        try:
            text, cost = await self.judge.ask(system, user)
        except BaseException:
            ticket.pay(drawn, drawn, "judge", "failed")
            raise
        ticket.pay(drawn, drawn if cost is None else cost, "judge", "fallback" if cost is None else "usage")
        return text

    async def _grade(self, ticket: Ticket, item: Item, answer: str) -> str:
        user = grader_prompt().format(question=item.question, target=item.answer, predicted_answer=answer)
        return str(parse_grade(await self._judge(ticket, "", user)))

    async def _synthesize(self, ticket: Ticket, item: Item, pages: list[tuple[str, str, str, int]]) -> str:
        user = EVALS_SYNTHESIS_USER.format(query=item.question, results=_sources(pages))
        return await self._judge(ticket, EVALS_SYNTHESIS_PROMPT, user)

    async def row(self, task: Task, ticket: Ticket) -> dict[str, Any]:
        """One row, graded -- or why it was not. A backend's failure is the row's, never the run's."""
        item = task.item
        row: dict[str, Any] = {
            "question_id": item.qid,
            "group": item.group,
            "source": item.source,
            "path": task.path,
            "provider": task.provider,
            "mode": task.mode,
            "question": item.question,
            "target": item.answer,
            "urls": list(item.urls),
            "latency": None,
            "answer": None,
            "grade": None,
            "error": None,
            "failed_at": None,
            "leaked": False,
            "cost_usd": None,
        }
        try:
            pages = await getattr(self, "_" + task.path.replace("+", "_"))(task, ticket, row)
        except BudgetExceeded as exc:
            if not ticket.spent["backend"]:
                raise  # nothing paid yet: asked again on resume
            return self._refused(row, ticket, exc)
        except Exception as exc:  # whatever a backend raises is this row's failure
            row.update(error=_describe(exc), failed_at=row["failed_at"] or task.path)
            pages = None
        if row["error"] is None and not row["leaked"]:
            try:
                if pages is not None:
                    row["answer"] = await self._synthesize(ticket, item, pages)
                row["grade"] = await self._grade(ticket, item, row["answer"] or "")
            except BudgetExceeded as exc:
                return self._refused(row, ticket, exc)
            except Exception as exc:  # the judge's failure is the row's
                row.update(error=_describe(exc), failed_at="judge")
        row.update(spend_usd=ticket.spent["backend"], judge_spend_usd=ticket.spent["judge"])
        return row

    @staticmethod
    def _refused(row: dict[str, Any], ticket: Ticket, exc: BudgetExceeded) -> dict[str, Any]:
        """A row whose backend was paid but whose grading the budget refused: kept ungraded, never bought twice."""
        row.update(
            grade=None,
            error=f"budget: {exc}",
            failed_at=BUDGET_STAGE,
            spend_usd=ticket.spent["backend"],
            judge_spend_usd=ticket.spent["judge"],
        )
        return row

    async def _search_extract(self, task: Task, ticket: Ticket, row: dict[str, Any]) -> list[tuple[str, str, str, int]]:
        item, started = task.item, time.perf_counter()
        row["failed_at"] = "search"
        response, search_cost = await self._backend(
            ticket,
            (task.provider, "search", None),
            lambda: self.router.search(item.question, provider=task.provider, max_results=MAX_RESULTS),
        )
        searched = time.perf_counter() - started
        results = [r for r in response.results if not _withholds(item, r.url)]
        row.update(failed_at=None, withheld=len(response.results) - len(results))
        reads = await asyncio.gather(*(self._read(ticket, r.url) for r in results[:PAGES_READ]))
        pages: list[tuple[str, str, str, int]] = []
        for i, result in enumerate(results):
            text = reads[i].text if i < len(reads) else ""
            snippet = result.content or result.description
            pages.append((result.title, result.url, text or snippet, PAGE_CHARS if text else EVALS_RESULT_CHARS))
        # A failed read counts too: it took its time, and it may have billed.
        read_costs = [r.cost for r in reads]
        row.update(
            latency=searched + max((r.elapsed for r in reads), default=0.0),
            pages_read=sum(1 for r in reads if r.text),
            cost_usd=_total([search_cost, *read_costs]),
        )
        return pages

    async def _read(self, ticket: Ticket, url: str) -> Read:
        """One page through the reader. A failed read is returned, charged and timed; only a budget refusal raises."""
        started = time.perf_counter()
        try:
            response, cost = await self._backend(
                ticket, (READER, "extract", None), lambda: self.router.extract(url, provider=READER)
            )
        except BudgetExceeded:
            raise
        except Exception as exc:  # the read's failure is this page's, not the row's
            return Read("", list_price(READER, "extract"), time.perf_counter() - started, _describe(exc))
        text = "\n\n".join(d.content for d in response.documents if d.content)
        return Read(text, cost, time.perf_counter() - started)

    async def _extract(self, task: Task, ticket: Ticket, row: dict[str, Any]) -> list[tuple[str, str, str, int]]:
        started = time.perf_counter()
        reads = await asyncio.gather(*(self._read(ticket, url) for url in task.item.urls))
        costs = [r.cost for r in reads]
        row.update(
            latency=time.perf_counter() - started,
            pages_read=sum(1 for r in reads if r.text),
            cost_usd=_total(costs),
        )
        done = [(url, r) for url, r in zip(task.item.urls, reads, strict=True) if r.text]
        if not done:
            failure = next((r.error for r in reads if r.error), None)
            raise RuntimeError(failure or "no page came back with text")
        return [("", url, read.text, PAGE_CHARS) for url, read in done]

    async def _answer(self, task: Task, ticket: Ticket, row: dict[str, Any]) -> None:
        started = time.perf_counter()
        response, cost = await self._backend(
            ticket,
            (task.provider, ANSWER, None),
            lambda: self.router.answer(prompt(task.item, ANSWER), provider=task.provider),
        )
        self._answered(task, row, started, response.answer, response.citations, cost)

    async def _research(self, task: Task, ticket: Ticket, row: dict[str, Any]) -> None:
        started = time.perf_counter()
        response, cost = await self._backend(
            ticket,
            (task.provider, RESEARCH, task.mode),
            lambda: self.router.research(
                prompt(task.item, RESEARCH),
                provider=task.provider,
                effort=task.mode,
                timeout=self.research_timeout,
            ),
        )
        text = response.answer or (json.dumps(response.structured) if response.structured is not None else "")
        self._answered(task, row, started, text, response.citations, cost)
        row["outcome"] = response.outcome

    def _answered(
        self, task: Task, row: dict[str, Any], started: float, text: str | None, citations: Any, cost: float | None
    ) -> None:
        row.update(
            latency=time.perf_counter() - started,
            answer=text,
            citations=len(citations),
            leaked=any(_withholds(task.item, c.url) for c in citations),
            cost_usd=cost,
        )


# The run


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def done_keys(rows: Iterable[Mapping[str, Any]]) -> set[tuple[str, str, str, str]]:
    """The rows a resumed run skips: every one written, failed or not -- a failed call may have billed."""
    return {(r["question_id"], r["path"], r["provider"], r["mode"]) for r in rows}


def order(tasks: Iterable[Task]) -> list[Task]:
    """Path by path, and within research the cheapest effort first, so a run stopped by its budget has whole columns."""
    return sorted(tasks, key=lambda t: (PATHS.index(t.path), task_cost(t).expected))


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
    gate = asyncio.Semaphore(max(1, concurrency))
    running: set[asyncio.Task[None]] = set()
    stopped: list[str] = []
    failures: list[BaseException] = []
    finished = 0

    async def one(task: Task, ticket: Ticket) -> None:
        nonlocal finished
        try:
            try:
                row = await row_of(task, ticket)
            finally:
                ticket.close()
            sink(row)
            finished += 1
            if row["failed_at"] == BUDGET_STAGE:
                stopped.append(f"{row['error']}; the row is kept ungraded, and a resume does not ask it again")
            outcome = row["grade"] or ("leaked" if row["leaked"] else f"error at {row['failed_at']}")
            say(
                f"[{finished}/{len(tasks)}] {task.path} {task.provider}:{task.mode} {task.item.qid}: {outcome}"
                f" | spent ${budget.spent:.2f}, reserved ${budget.reserved:.2f}, budget ${budget.cap:.2f}"
            )
        except BudgetExceeded as exc:
            stopped.append(f"{exc}; that row is asked again on resume")
        except BaseException as exc:
            failures.append(exc)
            raise
        finally:
            # Released only once the row is written, so a failure stops the next row from starting.
            gate.release()

    for task in tasks:
        await gate.acquire()
        if stopped or failures:
            gate.release()
            break
        try:
            ticket = Ticket(budget, task)
        except BudgetExceeded as exc:
            gate.release()
            stopped.append(str(exc))
            break
        running.add(asyncio.create_task(one(task, ticket)))
    # Every row in flight finishes before anything is raised, so none is lost to another's bug.
    await asyncio.gather(*running, return_exceptions=True)
    if failures:
        raise failures[0]
    return finished, stopped[0] if stopped else None


REQUIRED_KEYS = {
    "nimble": ENV_NIMBLE_API_KEY,
    "perplexity": ENV_PERPLEXITY_API_KEY,
    "linkup": ENV_LINKUP_API_KEY,
    "parallel": ENV_PARALLEL_API_KEY,
    "you": ENV_YOU_API_KEY,
    "exa": ENV_EXA_API_KEY,
    "brave": ENV_BRAVE_ANSWER_API_KEY,
}
"""The key each backend in the plan reads; Brave's answer key is its own."""


def missing_keys(tasks: Iterable[Task]) -> list[str]:
    needed = {REQUIRED_KEYS[t.provider] for t in tasks} | {ENV_OPENAI_API_KEY}
    if any(t.path in (SEARCH_EXTRACT, EXTRACT) for t in tasks):
        needed.add(REQUIRED_KEYS[READER])
    return sorted(name for name in needed if not os.environ.get(name))


def load_sample(local: Mapping[str, Path]) -> list[Item]:
    """The set's questions: the public sets downloaded, or read from the local copies ``--dataset`` names."""
    full = {name: Dataset.for_set(name, path=local.get(name)) for name in (SIMPLEQA, SEALQA_HARD, FRAMES)}
    return build_sample(full)


def setup_of(items: list[Item], judge: str, args: argparse.Namespace) -> dict[str, Any]:
    """``run.json``: the sample by id, never by text, and every setting a resumed run must share."""
    return {
        "set": SET_NAME,
        "questions": QUESTIONS,
        "dataset": DATASET,
        "judge": judge,
        "max_results": MAX_RESULTS,
        "pages_read": PAGES_READ,
        "page_chars": PAGE_CHARS,
        "research_timeout": args.research_timeout,
        "answers_sha256": answers_digest({i.qid: i.answer for i in items}),
        "sample": [i.entry() for i in items],
    }


MSG_OTHER_RUN = "{path} is another run ({key} differs); a resumed run must ask the same questions the same way"
RESUME_KEYS = (
    "set",
    "judge",
    "max_results",
    "pages_read",
    "page_chars",
    "research_timeout",
    "answers_sha256",
    "sample",
)


def open_run(out: Path, setup: dict[str, Any]) -> dict[str, Any]:
    """``run.json`` written for a new run, or checked against the one a resumed run left."""
    out.mkdir(parents=True, exist_ok=True)
    path = out / RUN_FILE
    if path.is_file():
        before = json.loads(path.read_text(encoding="utf-8"))
        for key in RESUME_KEYS:
            if before.get(key) != setup.get(key):
                raise SystemExit(MSG_OTHER_RUN.format(path=path, key=key))
        setup = {**before, "resumed": [*before.get("resumed", []), _now()]}
    else:
        setup = {**setup, "started": _now()}
    path.write_text(json.dumps(setup, indent=2) + "\n", encoding="utf-8")
    return setup


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _appender(path: Path) -> Callable[[dict[str, Any]], None]:
    def append(record: dict[str, Any]) -> None:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    return append


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


# The plan, without calling anything


def placeholder_sample() -> list[Item]:
    """The sample's shape with no question in it: every group at its size, each FRAMES question with all its pages.

    What ``--dry-run`` plans on, so it needs no download; a real FRAMES
    question has two to five links, so the extract count is an upper bound.
    """
    groups = ((EASY, SIMPLEQA), (HARD, SEALQA_HARD), (SYNTHESIS, FRAMES))
    items = [Item(group, source, i, qid=f"{group}-{i}") for group, source in groups for i in range(SIZES[group])]
    for source, count in URL_GIVEN_SIZES.items():
        urls = ("https://example.org/",) * (1 if source == SIMPLEQA else 5)
        items += [Item(URL_GIVEN, source, i, urls=urls, qid=f"{source}-url-{i}") for i in range(count)]
    return items


def dry_run(items: list[Item], budget: float) -> str:
    """The plan as text: questions per group, rows and calls per path, backend and mode, and the cost of each."""
    tasks = order(plan(items))
    groups = Counter((i.group, i.source) for i in items)
    lines = [f"{SET_NAME}: {len(items)} questions, {len(tasks)} rows (nothing is called)", ""]
    lines += ["Questions per group:"]
    lines += [f"  {group:<10} {source:<12} {count:>3}" for (group, source), count in groups.items()]
    columns: dict[tuple[str, str, str], list[Task]] = {}
    for task in tasks:
        columns.setdefault((task.path, task.provider, task.mode), []).append(task)
    lines += ["", f"  {'path':<15} {'backend':<11} {'mode':<8} {'rows':>4} {'calls':>5} {'worst':>9} {'expected':>9}"]
    totals = {"worst": 0.0, "expected": 0.0, "backend_worst": 0.0, "backend_expected": 0.0}
    calls = Counter[str]()
    for (path, provider, mode), column in columns.items():
        n_calls = sum(len(backend_calls(t)) for t in column)
        costs = [task_cost(t) for t in column]
        backend = [[call_cost(*c) for c in backend_calls(t)] for t in column]
        worst, expected = sum(c.worst for c in costs), sum(c.expected for c in costs)
        totals["worst"] += worst
        totals["expected"] += expected
        totals["backend_worst"] += sum(c.worst for cs in backend for c in cs)
        totals["backend_expected"] += sum(c.expected for cs in backend for c in cs)
        calls.update(method for t in column for _, method, _ in backend_calls(t))
        calls["judge"] += sum(len(judge_calls(t)) for t in column)
        lines.append(
            f"  {path:<15} {provider:<11} {mode:<8} {len(column):>4} {n_calls:>5} ${worst:>8.2f} ${expected:>8.2f}"
        )
    judge_worst_total = totals["worst"] - totals["backend_worst"]
    judge_expected = totals["expected"] - totals["backend_expected"]
    lines += [
        "",
        "Calls by method: " + ", ".join(f"{name} {count}" for name, count in calls.items()),
        "Unpriced calls, charged their stated fallback (never $0): "
        + ", ".join(f"{p} {m}{'' if mode is None else ':' + mode} ${c.worst}" for (p, m, mode), c in UNPRICED.items()),
        f"Backends: worst ${totals['backend_worst']:.2f}, expected ${totals['backend_expected']:.2f}",
        f"Judge (gpt-4o): worst ${judge_worst_total:.2f}, expected ${judge_expected:.2f}",
        f"Total: worst ${totals['worst']:.2f}, expected ${totals['expected']:.2f}; budget ${budget:.2f}",
        "The reused answer rows (the easy questions' answers from answer-simpleqa-300) cost nothing.",
    ]
    if totals["worst"] > budget:
        lines.append("Worst case is over the budget: the run reserves each row's worst case and stops before the cap.")
    return "\n".join(lines)


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
