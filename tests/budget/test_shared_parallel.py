"""A shared budget under parallel calls: reservations keep threads and asyncio tasks from overspending together.

Every call here is held open inside its backend until the test has seen every
other call either start or be refused, so all of them are admitted while the
others are still in flight: only a reservation made when a call starts can
stop them reserving more than the cap between them.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from tests.budget.conftest import Table
from tests.conftest import FakeProvider
from web_router import AsyncWebRouter, Budget, BudgetExceededError, WebRouter, registry
from web_router.routing.pool import BudgetPool
from web_router.types import SearchRequest, SearchResponse

CALLS = 8
COST = 0.03
SHARED = Budget(max_cost_usd=0.10)
FITS = 3
"""How many ``COST`` calls fit in ``SHARED``: the rest are refused."""
WAIT_S = 5.0


class Gate:
    """Holds every gated call open until :meth:`open`, counting the calls that reached their backend."""

    def __init__(self) -> None:
        self.entered = 0
        self.refused: list[BudgetExceededError] = []
        self.lock = threading.Lock()
        self.thread_open = threading.Event()
        self.task_open: asyncio.Event | None = None

    def enter(self) -> None:
        with self.lock:
            self.entered += 1

    def refuse(self, error: BudgetExceededError) -> None:
        with self.lock:
            self.refused.append(error)

    def settled(self, calls: int) -> bool:
        with self.lock:
            return self.entered + len(self.refused) == calls


@pytest.fixture
def gate(priced: Table) -> Iterator[Gate]:
    """``gated``, a search backend priced at ``COST`` that answers only once the gate opens."""
    made = Gate()
    priced.update(gated=(COST, None))

    class Gated(FakeProvider):
        name = "gated"

        def _search(self, request: SearchRequest) -> SearchResponse:
            made.enter()
            assert made.thread_open.wait(WAIT_S), "the test never opened the gate"
            return self._respond(request)

        async def _asearch(self, request: SearchRequest) -> SearchResponse:
            made.enter()
            assert made.task_open is not None
            await asyncio.wait_for(made.task_open.wait(), WAIT_S)
            return self._respond(request)

    registry.register_provider("gated", Gated)
    yield made
    made.thread_open.set()
    registry.unregister_provider("gated")


def _in_threads(gate: Gate, search: Callable[[], Any]) -> list[Any]:
    """``search`` in ``CALLS`` threads started together; the gate opens once each has started or been refused."""
    results: list[Any] = []
    start = threading.Barrier(CALLS)

    def run() -> None:
        start.wait()
        try:
            results.append(search())
        except BudgetExceededError as error:
            gate.refuse(error)

    threads = [threading.Thread(target=run) for _ in range(CALLS)]
    for thread in threads:
        thread.start()
    _until(lambda: gate.settled(CALLS))
    gate.thread_open.set()
    for thread in threads:
        thread.join(WAIT_S)
    return results


def _until(condition: Callable[[], bool]) -> None:
    ends = time.monotonic() + WAIT_S
    while not condition():
        assert time.monotonic() < ends, "the calls never all started or were refused"
        time.sleep(0.005)


def test_parallel_threads_never_reserve_more_than_the_cap_between_them(gate: Gate) -> None:
    with WebRouter().budget(SHARED) as shared:
        results = _in_threads(gate, lambda: shared.search("q", provider="gated"))
        assert (len(results), len(gate.refused)) == (FITS, CALLS - FITS)
        status = shared.status()
    assert (status.spent_usd, status.reserved_usd, status.remaining_usd) == (0.09, 0.0, 0.01)
    assert all(result.decision.estimated_cost_usd == COST for result in results)


def test_a_refused_call_fails_fast_with_the_reservations_in_flight_named(gate: Gate) -> None:
    with WebRouter().budget(SHARED) as shared:
        _in_threads(gate, lambda: shared.search("q", provider="gated"))
    error = gate.refused[0]
    assert error.skipped == {"search:gated": "est. $0.03 > $0.01 left in the shared budget"}


def test_a_call_in_flight_holds_a_reservation_until_it_ends_then_settles_at_its_estimate(gate: Gate) -> None:
    shared = WebRouter().budget(SHARED)
    worker = threading.Thread(target=lambda: shared.search("q", provider="gated"))
    worker.start()
    _until(lambda: gate.entered == 1)
    in_flight = shared.status()
    gate.thread_open.set()
    worker.join(WAIT_S)
    ended = shared.status()
    assert (in_flight.spent_usd, in_flight.reserved_usd, in_flight.remaining_usd) == (0.0, COST, 0.07)
    assert (ended.spent_usd, ended.reserved_usd, ended.remaining_usd) == (COST, 0.0, 0.07)


async def test_parallel_asyncio_tasks_never_reserve_more_than_the_cap_between_them(gate: Gate) -> None:
    async with AsyncWebRouter().budget(SHARED) as shared:
        results = await _in_tasks(gate, lambda: shared.search("q", provider="gated"))
        status = shared.status()
    assert (len(results), len(gate.refused)) == (FITS, CALLS - FITS)
    assert (status.spent_usd, status.reserved_usd) == (0.09, 0.0)


async def test_tasks_calling_the_router_itself_inside_the_block_share_its_budget(gate: Gate) -> None:
    router = AsyncWebRouter()
    async with router.budget(SHARED) as shared:
        results = await _in_tasks(gate, lambda: router.search("q", provider="gated"))
    assert (len(results), len(gate.refused)) == (FITS, CALLS - FITS)
    assert shared.status().spent_usd == 0.09


async def _in_tasks(gate: Gate, search: Callable[[], Any]) -> list[Any]:
    """``search`` in ``CALLS`` asyncio tasks; the gate opens once each has started or been refused."""
    gate.task_open = asyncio.Event()

    async def run() -> Any:
        try:
            return await search()
        except BudgetExceededError as error:
            gate.refuse(error)
            return None

    async def opener() -> None:
        while not gate.settled(CALLS):
            await asyncio.sleep(0.005)
        assert gate.task_open is not None
        gate.task_open.set()

    answered = await asyncio.wait_for(asyncio.gather(*(run() for _ in range(CALLS)), opener()), WAIT_S)
    return [result for result in answered if result is not None]


def test_many_threads_without_a_gate_spend_exactly_what_fits(priced: Table, fake_provider: Any) -> None:
    priced.update(fake=(0.01, None))
    with WebRouter().budget(Budget(max_cost_usd=0.25)) as shared:
        refused: list[BudgetExceededError] = []

        def run() -> None:
            for _ in range(10):
                try:
                    shared.search("q", provider="fake")
                except BudgetExceededError as error:
                    refused.append(error)

        threads = [threading.Thread(target=run) for _ in range(10)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(WAIT_S)
    assert (len(refused), shared.status().spent_usd) == (75, 0.25)


class _SlowCost(float):
    """A cost whose comparison with what is left takes a while, so a check and its reservation are far apart."""

    def __gt__(self, other: object) -> bool:
        time.sleep(0.02)
        return float(self) > other  # type: ignore[operator]


def test_the_check_and_the_reservation_are_one_step_however_long_the_check_takes() -> None:
    pool = BudgetPool(SHARED)
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
    assert pool.status().reserved_usd == 0.09


class _SlowSum(float):
    """A cost whose arithmetic with the pool's totals takes a while, so an update's read and write are far apart."""

    def __radd__(self, other: object) -> float:
        time.sleep(0.01)
        return float(other) + float(self)  # type: ignore[arg-type]

    def __rsub__(self, other: object) -> float:
        time.sleep(0.01)
        return float(other) - float(self)  # type: ignore[arg-type]


def test_settlements_from_many_threads_at_once_lose_no_update() -> None:
    pool = BudgetPool(Budget(max_cost_usd=1.0))
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
    status = pool.status()
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
    inner = BudgetPool(Budget(max_cost_usd=0.5), parent=outer)
    big = _ReportingCost(0.5)
    refused: list[float | None] = []
    worker = threading.Thread(target=lambda: refused.append(inner.reserve(big)))
    worker.start()
    assert big.at_outer.wait(WAIT_S)
    assert inner.reserve(0.05) is None, "the refused reservation was never visible in the inner pool"
    worker.join(WAIT_S)
    assert refused == [pytest.approx(0.10)]
    assert (inner.status().reserved_usd, outer.status().reserved_usd) == (0.05, 0.05)
