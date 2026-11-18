"""The tool and effort set's rows: the judge it pays for, and one row asked, read and graded.

Kept apart from ``tools_effort.py`` so the runner's other parts stay small.
Imported as a sibling, by name, as ``_common`` is.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any, Protocol

from _tools_effort_budget import (
    JUDGE_INPUT_USD,
    JUDGE_OUTPUT_USD,
    BudgetExceeded,
    Ticket,
    call_cost,
    judge_worst,
    list_price,
)
from _tools_effort_plan import (
    ANSWER,
    MAX_RESULTS,
    PAGES_READ,
    READER,
    RESEARCH,
    SEALQA_HARD,
    SEARCH_EXTRACT,
    Item,
    Task,
)
from answer_report import stated_cost

from web_router.constants import (
    EVALS_RESULT_CHARS,
    EVALS_RESULT_FORMAT,
    EVALS_SYNTHESIS_PROMPT,
    EVALS_SYNTHESIS_USER,
)
from web_router.constants_evals import SEALQA_WITHHELD_MARKERS
from web_router.evals import Dataset, OpenAIJudge
from web_router.evals.judge import grader_prompt, parse_grade
from web_router.integrations._render import cut_to_budget

RESEARCH_TIMEOUT = 1200.0
"""Seconds a research run may take: twice ``research()``'s default, as a Linkup run can take a quarter of an hour."""
PAGE_CHARS = 8000
"""How much of a read page the judge sees; a search snippet keeps the published sets' 1,500 characters."""

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
        started = time.perf_counter()
        try:
            pages = await getattr(self, "_" + task.path.replace("+", "_"))(task, ticket, row)
        except BudgetExceeded as exc:
            if not ticket.spent["backend"]:
                raise  # nothing paid yet: asked again on resume
            return self._refused(row, ticket, exc)
        except Exception as exc:  # whatever a backend raises is this row's failure
            if row["latency"] is None:  # the time to the failure, as the frozen schema says
                row["latency"] = time.perf_counter() - started
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
