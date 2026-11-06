"""A budget several calls spend from: what :meth:`~web_router.WebRouter.budget` holds.

Experimental. A pool keeps one :class:`~web_router.Budget` for every call
made through its scope, and is the only state those calls share:

- each call's budget is the tighter of its own ``budget=`` and the pool's
  (:meth:`BudgetPool.limit`): its cost cap, the pool's time left, and strict
  if either is;
- the pool's clock runs from when the pool is made, not from each call;
- before each attempt, the call's :class:`~.ledger.Ledger` reserves the
  attempt's estimate here (:meth:`BudgetPool.reserve`). A reservation is
  checked against the cap less what is spent and what every call in flight
  has reserved, under one lock, so calls in parallel -- threads or asyncio
  tasks -- can never reserve more than the cap between them. One that does not
  fit is refused at once; nothing waits for another call to finish;
- when a call ends, however it ends, what it reserved is settled
  (:meth:`BudgetPool.settle`): moved from reserved to spent, at the estimate,
  since no call reports what it was billed. A failed attempt stays charged.

A pool belongs to the router that made it: only that router's calls spend
from it, and another router's calls inside the block run as they would
outside it. A pool made while another of the same router's is in force
spends from both: each reservation must fit every pool up the chain, checked
and taken under all their locks at once, and the clock is the earliest of
theirs.

The pools in force are held in a context variable, as the per-call deadline
is, so a call made on the router inside ``with wr.budget(...)`` spends from
it too. An asyncio task inherits them; a thread does not, so a thread calls
through the scope (``b.search``), which binds the pool for the call.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import ExitStack
from contextvars import ContextVar, Token
from dataclasses import dataclass

from web_router.constants_budget import STATUS_DIGITS
from web_router.routing.contract import Budget
from web_router.routing.pricing import over_cost


@dataclass(frozen=True, kw_only=True)
class BudgetStatus:
    """Where a shared budget stands. Experimental."""

    spent_usd: float
    """The estimates settled by calls that have ended, failed attempts included."""

    reserved_usd: float
    """The estimates reserved by calls still running."""

    remaining_usd: float | None
    """The least, up the chain, of each cap less what is spent and reserved; ``None`` with no cost cap."""

    elapsed_s: float
    """Seconds since the budget was made."""

    time_left_s: float | None
    """Seconds left on the budget's clock, never below zero; ``None`` with no time limit."""


class BudgetPool:
    """One :class:`~web_router.Budget` that every call made through its scope reserves from and settles to."""

    def __init__(self, budget: Budget, parent: BudgetPool | None = None, owner: object = None) -> None:
        self.budget = budget
        self.parent = parent
        self.owner = owner
        """The router whose calls spend from this pool."""
        self.started = time.monotonic()
        self._lock = threading.Lock()
        self._spent = 0.0
        self._reserved = 0.0

    def _chain(self) -> Iterator[BudgetPool]:
        pool: BudgetPool | None = self
        while pool is not None:
            yield pool
            pool = pool.parent

    def limit(self, budget: Budget | None) -> Budget:
        """One call's budget: the tighter of its own ``budget`` and this pool's, with the time left now.

        The time left is a snapshot for what the call shows a router and records;
        the call's deadline is held to :meth:`expires_at` itself, so a call that
        starts late gets no more time than the clock has left.
        """
        own = budget or Budget()
        caps = [own.max_cost_usd, *(pool.budget.max_cost_usd for pool in self._chain())]
        return Budget(
            max_cost_usd=_least(caps),
            time_limit_s=_least([own.time_limit_s, self.time_left()]),
            strict=own.strict or any(pool.budget.strict for pool in self._chain()),
        )

    def expires_at(self) -> float | None:
        """The earliest clock up the chain runs out at this :func:`time.monotonic` instant; ``None`` with no limit."""
        ends = [
            pool.started + pool.budget.time_limit_s for pool in self._chain() if pool.budget.time_limit_s is not None
        ]
        return min(ends) if ends else None

    def time_left(self) -> float | None:
        """Seconds left on the earliest clock up the chain, never below zero; ``None`` when none has a limit."""
        ends = self.expires_at()
        return None if ends is None else max(0.0, ends - time.monotonic())

    def reserve(self, cost: float) -> float | None:
        """Reserve ``cost`` in this pool and every one up the chain, or in none of them.

        Every pool's lock is held at once, outermost first, so no other call
        sees a reservation that is about to be undone.

        Returns ``None`` once reserved; otherwise the dollars left in the
        innermost pool that refused, which ``cost`` does not fit.
        """
        chain = list(self._chain())
        with ExitStack() as held:
            for pool in reversed(chain):
                held.enter_context(pool._lock)
            for pool in chain:
                left = pool._left()
                if left is not None and over_cost(cost, left):
                    return left
            for pool in chain:
                pool._reserved += cost
        return None

    def settle(self, cost: float) -> None:
        """Move ``cost``, what a call that has ended reserved, from reserved to spent, all up the chain."""
        for pool in self._chain():
            pool._move(cost, spent=True)

    def _left(self) -> float | None:
        cap = self.budget.max_cost_usd
        return None if cap is None else cap - self._spent - self._reserved

    def _move(self, cost: float, *, spent: bool) -> None:
        with self._lock:
            self._reserved -= cost
            if spent:
                self._spent += cost

    def status(self) -> BudgetStatus:
        """Spent and reserved dollars here, what is left up the chain, and the clock, as of now."""
        with self._lock:
            spent, reserved = round(self._spent, STATUS_DIGITS), round(self._reserved, STATUS_DIGITS)
        lefts: list[float | None] = []
        for pool in self._chain():
            with pool._lock:
                lefts.append(pool._left())
        remaining = _least(lefts)
        return BudgetStatus(
            spent_usd=spent,
            reserved_usd=reserved,
            remaining_usd=None if remaining is None else round(remaining, STATUS_DIGITS),
            elapsed_s=time.monotonic() - self.started,
            time_left_s=self.time_left(),
        )


def _least(values: list[float | None]) -> float | None:
    known = [value for value in values if value is not None]
    return min(known) if known else None


_POOLS: ContextVar[tuple[BudgetPool, ...]] = ContextVar("web_router_budget_pools", default=())
"""The pools in force in this context, innermost last."""


def bind(pool: BudgetPool) -> Token[tuple[BudgetPool, ...]]:
    """Put ``pool`` in force for its router's calls in this context, until :func:`unbind` with the token."""
    return _POOLS.set((*_POOLS.get(), pool))


def unbind(token: Token[tuple[BudgetPool, ...]]) -> None:
    """Put back the pools that were in force before :func:`bind` returned ``token``."""
    _POOLS.reset(token)


def current_pool(owner: object = None) -> BudgetPool | None:
    """The innermost pool ``owner``'s calls spend from in this context -- any router's with no owner -- or ``None``.

    A pool with no owner of its own (the MCP server's, which outlives the routers
    ``load_env`` replaces) applies to every router's calls.
    """
    for pool in reversed(_POOLS.get()):
        if owner is None or pool.owner is None or pool.owner is owner:
            return pool
    return None


def limited(budget: Budget | None, owner: object = None) -> Budget | None:
    """``budget`` held to ``owner``'s pool in force (:meth:`BudgetPool.limit`); as given outside every budget scope."""
    pool = current_pool(owner)
    return budget if pool is None else pool.limit(budget)
