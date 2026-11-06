"""One connection's budget: the operator's ceiling, the task ``budget_start`` opens inside it, and the gate.

Experimental. The budget is a shared one (:mod:`web_router.routing.pool`),
bound around every tool call the connection makes, so every billed tool spends
from it as a call inside ``wr.budget(...)`` does:

- with a ceiling set (``WEB_ROUTER_MCP_MAX_COST_USD``,
  ``WEB_ROUTER_MCP_TIME_LIMIT_S``) and no ``budget_start``, the whole
  connection is one task under it, its clock running from the connection's
  start;
- ``budget_start`` opens a new task inside the ceiling, with the limits it
  states and a clock from now. Each reservation must fit both the task and
  the ceiling, so the task gets no more than the ceiling has left, and the
  agent can never reset or raise the ceiling. A call still running when a
  task starts stays charged where it was reserved, and so to the ceiling;
- with neither, nothing is bound and every tool runs exactly as it does with
  no budget.

search, extract, answer, ``web_run``, and research and browse as they start
their jobs, spend through the library's own per-attempt check. ``run_template``
and ``browser_open`` have no ``auto`` to hook, so :func:`charged` reserves
theirs before the call: a template run at its list price, a cloud browser per
minute of its lifetime. Under a cost cap a backend whose price is unknown never
runs, and the refusal names the unknown price.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager, nullcontext
from typing import TYPE_CHECKING, Any, cast

from web_router.client import AsyncWebRouter
from web_router.constants import MCP_ARG_PROVIDER, MCP_TOOL_BROWSER_OPEN
from web_router.constants_mcp import (
    MCP_MSG_BUDGET_LIMIT,
    MCP_PRICE_OPEN_BROWSER,
    MCP_PRICE_RUN_TEMPLATE,
    MCP_TOOL_BUDGET_START,
)
from web_router.mcp.chains import CHAIN_OF
from web_router.routing.attempts import Attempt
from web_router.routing.contract import Budget, ToolName
from web_router.routing.ledger import Ledger
from web_router.routing.pool import BudgetPool, bind, current_pool, unbind
from web_router.routing.pricing import attempt_cost
from web_router.types import BrowserRequest, NormalizedRequest, TemplateRequest

if TYPE_CHECKING:
    from web_router.mcp.tools import WebRouterTools


class ConnectionBudget:
    """The ceiling one connection runs under, if any, and the task in force inside it."""

    def __init__(self, ceiling: Budget) -> None:
        capped = ceiling.max_cost_usd is not None or ceiling.time_limit_s is not None
        self.ceiling: BudgetPool | None = BudgetPool(ceiling) if capped else None
        self.task: BudgetPool | None = self.ceiling

    def start(self, max_cost_usd: float, time_limit_s: float) -> None:
        """Open a new task with these limits, inside the ceiling; the previous one closes."""
        self.task = BudgetPool(Budget(max_cost_usd=max_cost_usd, time_limit_s=time_limit_s), parent=self.ceiling)

    @contextmanager
    def bound(self) -> Iterator[None]:
        """The task in force over the block, so every call made in it spends from it; nothing with no budget."""
        if self.task is None:
            yield
            return
        token = bind(self.task)
        try:
            yield
        finally:
            unbind(token)

    def status(self) -> dict[str, Any] | None:
        """Where the task stands, what is left counted against the ceiling too; ``None`` with no budget."""
        if self.task is None:
            return None
        now = self.task.status()
        pools = [pool for pool in (self.task, self.ceiling) if pool is not None]
        left = [
            status.remaining_usd for status in (pool.status() for pool in pools) if status.remaining_usd is not None
        ]
        limits = self.task.limit(None)
        ends = self.task.expires_at()  # the earliest clock up the chain: the task's, or the ceiling's if sooner
        return {
            "max_cost_usd": limits.max_cost_usd,
            "time_limit_s": None if ends is None else round(ends - self.task.started, 3),
            "spent_usd": now.spent_usd,
            "reserved_usd": now.reserved_usd,
            "remaining_usd": min(left) if left else None,
            "elapsed_s": round(now.elapsed_s, 3),
            "time_left_s": None if now.time_left_s is None else round(now.time_left_s, 3),
        }


@contextmanager
def charged(method: str, providers: Sequence[str], request: NormalizedRequest) -> Iterator[None]:
    """Reserve one call of ``method`` that no ``auto`` hooks, in the budget in force; settled when the block ends.

    ``providers`` are the backends that may serve it; the dearest is reserved, and
    one whose price is unknown is unknown for the call.

    Raises:
        BudgetExceededError: when the call does not fit what is left, or its price is unknown under a cost cap.
    """
    pool = current_pool()
    if pool is None:
        yield
        return
    attempts = [Attempt(tool=cast("ToolName", method), provider=name, request=request) for name in providers]
    costs = [attempt_cost(attempt) for attempt in attempts]
    dearest = costs.index(None) if None in costs else max(range(len(costs)), key=lambda at: costs[at] or 0.0)
    ledger = Ledger(pool.limit(None), pool)
    if not ledger.admit(attempts[dearest]):
        raise ledger.refusal()
    try:
        yield
    finally:
        pool.settle(ledger.spent)


def _limit(arguments: Mapping[str, Any], name: str) -> float:
    value = arguments[name]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not value > 0:
        raise ValueError(MCP_MSG_BUDGET_LIMIT.format(tool=MCP_TOOL_BUDGET_START, name=name, value=value))
    return float(value)


async def budget_start(tools: WebRouterTools, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Open a new task budget with the limits ``arguments`` state, and report it."""
    kwargs = tools.arguments(MCP_TOOL_BUDGET_START, arguments)
    tools.budget.start(_limit(kwargs, "max_cost_usd"), _limit(kwargs, "time_limit_s"))
    return await budget_status(tools, {})


async def budget_status(tools: WebRouterTools, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """The task in force and the ceiling, as the capabilities resource reports them."""
    routing = tools.capabilities_routing()
    return {"budget": routing["budget"], "ceiling": routing["ceiling"]}


def template_charged(router: AsyncWebRouter, kwargs: Mapping[str, Any]) -> AbstractContextManager[None]:
    """:func:`charged` for a ``run_template`` call with ``kwargs``: one run on the named backend."""
    if current_pool() is None:
        return nullcontext()
    backend = router.get_template_provider(kwargs[MCP_ARG_PROVIDER])
    return charged(MCP_PRICE_RUN_TEMPLATE, [backend.name], TemplateRequest(template=str(kwargs["template"])))


def browser_charged(router: AsyncWebRouter, kwargs: Mapping[str, Any]) -> AbstractContextManager[None]:
    """:func:`charged` for a ``browser_open`` with ``kwargs``: its lifetime on the dearest backend it may open on."""
    if current_pool() is None:
        return nullcontext()
    name = router.get_browser_provider(kwargs.get(MCP_ARG_PROVIDER)).name
    members = CHAIN_OF[MCP_TOOL_BROWSER_OPEN](router, name) or (name,)
    return charged(MCP_PRICE_OPEN_BROWSER, members, BrowserRequest(timeout=kwargs.get("timeout")))
