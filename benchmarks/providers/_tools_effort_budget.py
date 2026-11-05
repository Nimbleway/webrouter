"""The tool and effort set's costs and budget: what each call may cost, and the cap a run holds to.

Kept apart from ``tools_effort.py`` so the runner's other parts stay small. A
call costs what its response states, else its list price from
:mod:`web_router.prices`, else the stated fallback below -- never $0. Imported
as a sibling, by name, as ``_common`` is.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from _tools_effort_plan import EXTRACT, PAGES_READ, READER, RESEARCH, SEARCH_EXTRACT, Task

from web_router import prices

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
