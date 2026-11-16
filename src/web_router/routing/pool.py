"""A budget several calls spend from: what :meth:`~web_router.WebRouter.budget` holds.

A pool keeps one :class:`~web_router.Budget` for every call
made through its scope, and is the only state those calls share:

- each call's budget is the tighter of its own ``budget=`` and what the
  pools in force have left (:meth:`PoolChain.limit`): the dollars and the
  seconds, and strict if any is;
- the pool's clock runs from when the pool is made, not from each call;
- before each attempt, the call's :class:`~.ledger.Ledger` reserves the
  attempt's estimate here (:meth:`PoolChain.reserve`). A reservation is
  checked against the cap less what is spent and what every call in flight
  has reserved, under one lock, so calls in parallel -- threads or asyncio
  tasks -- can never reserve more than the cap between them. One that does not
  fit is refused at once; nothing waits for another call to finish;
- when a call ends, however it ends, what it reserved is settled
  (:meth:`PoolChain.settle`): moved from reserved to spent, at the estimate,
  since no call reports what it was billed. A failed attempt stays charged.

A pool belongs to the router that made it: only that router's calls spend
from it, and another router's calls inside the block run as they would
outside it. A call spends from every pool of its router in force where it
starts -- its :class:`PoolChain`, taken once when the call starts --
whichever scope it is made through and wherever that scope was made, so an
outer block's cap holds for a scope made before the block was entered. Each
reservation must fit every pool in the chain, checked and taken under all
their locks at once, and the clock is the earliest of theirs.

The pools in force are held in a context variable, as the per-call deadline
is (:mod:`.pool_bind`), so a call made on the router inside ``with wr.budget(...)`` spends from
it too. An asyncio task inherits them; a thread does not, so a thread calls
through the scope (``b.search``), which binds the pool for the call.
"""

from __future__ import annotations

import itertools
import threading
import time
from contextlib import ExitStack
from dataclasses import dataclass

from web_router.constants_budget import STATUS_DIGITS
from web_router.routing.contract import Budget
from web_router.routing.pool_bind import bound
from web_router.routing.pool_status import BudgetStatus
from web_router.routing.pricing import over_cost

_ORDER = itertools.count()
"""Every pool's place in the one order locks are taken in, so two chains never wait on each other."""


class BudgetPool:
    """One :class:`~web_router.Budget`: its money, its lock and its clock."""

    def __init__(self, budget: Budget, owner: object = None) -> None:
        self.budget = budget
        self.owner = owner
        """The router whose calls spend from this pool."""
        self.started = time.monotonic()
        self.order = next(_ORDER)
        self._lock = threading.Lock()
        self._spent = 0.0
        self._reserved = 0.0

    def ends_at(self) -> float | None:
        """When this pool's clock runs out, on :func:`time.monotonic`'s clock; ``None`` with no limit."""
        limit = self.budget.time_limit_s
        return None if limit is None else self.started + limit

    def left(self) -> float | None:
        """The cap less what is spent and reserved, now; ``None`` with no cost cap."""
        with self._lock:
            return self._left()

    def _left(self) -> float | None:
        cap = self.budget.max_cost_usd
        return None if cap is None else cap - self._spent - self._reserved

    def _settle(self, cost: float) -> None:
        with self._lock:
            self._reserved -= cost
            self._spent += cost

    def status(self) -> BudgetStatus:
        """Spent and reserved dollars here, what is left counting the pools in force outside it, and the clock."""
        with self._lock:
            spent, reserved = round(self._spent, STATUS_DIGITS), round(self._reserved, STATUS_DIGITS)
        chain = _around(self)
        remaining = chain.left()
        return BudgetStatus(
            spent_usd=spent,
            reserved_usd=reserved,
            remaining_usd=None if remaining is None else round(remaining, STATUS_DIGITS),
            elapsed_s=time.monotonic() - self.started,
            time_left_s=chain.time_left(),
        )


@dataclass(frozen=True)
class PoolChain:
    """The pools one call spends from, innermost first: every one must fit each reservation."""

    pools: tuple[BudgetPool, ...]

    def limit(self, budget: Budget | None) -> Budget:
        """One call's budget: the tighter of its own ``budget`` and what the chain has left now.

        Both are snapshots, for what the call shows a router and records. The
        call's deadline is held to :meth:`expires_at` itself, so a call that
        starts late gets no more time than the clock has left, and each
        attempt is reserved against what is left when it starts.
        """
        own = budget or Budget()
        return Budget(
            max_cost_usd=_least([own.max_cost_usd, self.left()]),
            time_limit_s=_least([own.time_limit_s, self.time_left()]),
            strict=own.strict or any(pool.budget.strict for pool in self.pools),
        )

    def left(self) -> float | None:
        """The least any pool in the chain has left, never below zero; ``None`` when none has a cost cap."""
        left = _least([pool.left() for pool in self.pools])
        return None if left is None else max(0.0, left)

    def expires_at(self) -> float | None:
        """The earliest clock in the chain runs out at this :func:`time.monotonic` instant; ``None`` with no limit."""
        return _least([pool.ends_at() for pool in self.pools])

    def time_left(self) -> float | None:
        """Seconds left on the earliest clock in the chain, never below zero; ``None`` when none has a limit."""
        ends = self.expires_at()
        return None if ends is None else max(0.0, ends - time.monotonic())

    def reserve(self, cost: float) -> float | None:
        """Reserve ``cost`` in every pool of the chain, or in none of them.

        Every pool's lock is held at once, taken in one order shared by all
        chains, so no other call sees a reservation that is about to be undone
        and no two chains wait on each other.

        Returns ``None`` once reserved; otherwise the dollars left in the
        innermost pool that refused, which ``cost`` does not fit.
        """
        with ExitStack() as held:
            for pool in sorted(self.pools, key=lambda pool: pool.order):
                held.enter_context(pool._lock)
            for pool in self.pools:
                left = pool._left()
                if left is not None and over_cost(cost, left):
                    return left
            for pool in self.pools:
                pool._reserved += cost
        return None

    def settle(self, cost: float) -> None:
        """Move ``cost``, what a call that has ended reserved, from reserved to spent in every pool of the chain."""
        for pool in self.pools:
            pool._settle(cost)


def _least(values: list[float | None]) -> float | None:
    known = [value for value in values if value is not None]
    return min(known) if known else None


def chain_in_force(owner: object) -> PoolChain | None:
    """Every pool ``owner``'s calls spend from in this context, innermost first, once each; ``None`` with none.

    That is ``owner``'s own pools and every pool with no owner -- the MCP
    server's, which outlives the routers ``load_env`` replaces.
    """
    pools = tuple(dict.fromkeys(pool for pool in reversed(bound()) if pool.owner in (owner, None)))
    return PoolChain(pools) if pools else None


def _around(pool: BudgetPool) -> PoolChain:
    """``pool`` and the pools of its router in force outside its innermost binding here, as a call through it spends.

    Unbound, that is ``pool`` on top of every pool of its router in force here.
    """
    chain = chain_in_force(pool.owner)
    if chain is None:
        return PoolChain((pool,))
    if pool not in chain.pools:
        return PoolChain((pool, *chain.pools))
    return PoolChain(chain.pools[chain.pools.index(pool) :])


def limited(budget: Budget | None, owner: object = None) -> Budget | None:
    """``budget`` held to ``owner``'s pools in force (:meth:`PoolChain.limit`); as given outside every budget scope."""
    chain = chain_in_force(owner)
    return budget if chain is None else chain.limit(budget)
