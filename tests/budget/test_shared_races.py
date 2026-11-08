"""A shared budget's pool under racing threads: a check and its reservation, and each settlement, are one step.

Each test slows one comparison or sum inside the pool, so a race the lock did
not close would show. Parallel calls through the routers are in
:mod:`tests.budget.test_shared_parallel`.
"""

from __future__ import annotations

import threading
import time

import pytest

from web_router import Budget
from web_router.routing.pool import BudgetPool, PoolChain

CALLS = 8
COST = 0.03
SHARED = Budget(max_cost_usd=0.10)
FITS = 3
"""How many ``COST`` calls fit in ``SHARED``: the rest are refused."""
WAIT_S = 5.0


class _SlowCost(float):
    """A cost whose comparison with what is left takes a while, so a check and its reservation are far apart."""

    def __gt__(self, other: object) -> bool:
        time.sleep(0.02)
        return float(self) > other  # type: ignore[operator]


def test_the_check_and_the_reservation_are_one_step_however_long_the_check_takes() -> None:
    pool = PoolChain((BudgetPool(SHARED),))
    start = threading.Barrier(CALLS)
    refused: list[float | None] = []

    def run() -> None:
        start.wait()
        refused.append(pool.reserve(_SlowCost(COST)))

    threads = [threading.Thread(target=run) for _ in range(CALLS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(WAIT_S)
    assert refused.count(None) == FITS
    assert pool.pools[0].status().reserved_usd == 0.09


class _SlowSum(float):
    """A cost whose arithmetic with the pool's totals takes a while, so an update's read and write are far apart."""

    def __radd__(self, other: object) -> float:
        time.sleep(0.01)
        return float(other) + float(self)  # type: ignore[arg-type]

    def __rsub__(self, other: object) -> float:
        time.sleep(0.01)
        return float(other) - float(self)  # type: ignore[arg-type]


def test_settlements_from_many_threads_at_once_lose_no_update() -> None:
    budget = BudgetPool(Budget(max_cost_usd=1.0))
    pool = PoolChain((budget,))
    for _ in range(CALLS):
        assert pool.reserve(COST) is None
    start = threading.Barrier(CALLS)

    def run() -> None:
        start.wait()
        pool.settle(_SlowSum(COST))

    threads = [threading.Thread(target=run) for _ in range(CALLS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(WAIT_S)
    status = budget.status()
    assert (status.spent_usd, status.reserved_usd) == (round(CALLS * COST, 9), 0.0)


class _ReportingCost(float):
    """A cost that signals when the outer pool compares it, then takes a while there."""

    def __init__(self, value: float) -> None:
        self.compares = 0
        self.at_outer = threading.Event()

    def __gt__(self, other: object) -> bool:
        self.compares += 1
        if self.compares == 2:  # the inner pool's check is first, the outer's second
            self.at_outer.set()
            time.sleep(0.1)
        return float(self) > other  # type: ignore[operator]


def test_a_nested_reservation_the_outer_pool_refuses_never_blocks_another_call_in_the_inner_one() -> None:
    outer = BudgetPool(Budget(max_cost_usd=0.10))
    inner_pool = BudgetPool(Budget(max_cost_usd=0.5))
    inner = PoolChain((inner_pool, outer))
    big = _ReportingCost(0.5)
    refused: list[float | None] = []
    worker = threading.Thread(target=lambda: refused.append(inner.reserve(big)))
    worker.start()
    assert big.at_outer.wait(WAIT_S)
    assert inner.reserve(0.05) is None, "the refused reservation was never visible in the inner pool"
    worker.join(WAIT_S)
    assert refused == [pytest.approx(0.10)]
    assert (inner_pool.status().reserved_usd, outer.status().reserved_usd) == (0.05, 0.05)
