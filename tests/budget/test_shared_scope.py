"""The scope object itself: one scope entered from several threads or tasks, a router subclass, and a late start."""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

import pytest

from tests.budget.conftest import Chain, Table
from web_router import AsyncWebRouter, Budget, BudgetExceededError, WebRouter
from web_router.constants_budget import SKIP_NO_TIME
from web_router.exceptions import ConfigurationError, WebRouterError
from web_router.providers._deadline import call_deadline, current_deadline
from web_router.routing import ledger
from web_router.routing.pool import BudgetPool, current_pool
from web_router.types import SearchResponse

WAIT_S = 5.0


async def test_two_tasks_that_enter_one_scope_can_leave_it_out_of_order() -> None:
    scope = AsyncWebRouter().budget(Budget(max_cost_usd=1.0))
    first_in, second_in, first_out = asyncio.Event(), asyncio.Event(), asyncio.Event()
    seen: list[tuple[str, object]] = []

    async def first() -> None:
        async with scope:
            first_in.set()
            await second_in.wait()
        seen.append(("first after", current_pool()))
        first_out.set()

    async def second() -> None:
        await first_in.wait()
        async with scope:
            second_in.set()
            await first_out.wait()
            seen.append(("second inside", current_pool()))
        seen.append(("second after", current_pool()))

    await asyncio.wait_for(asyncio.gather(first(), second()), WAIT_S)
    assert seen == [("first after", None), ("second inside", scope.pool), ("second after", None)]


def test_two_threads_that_enter_one_scope_can_leave_it_out_of_order() -> None:
    scope = WebRouter().budget(Budget(max_cost_usd=1.0))
    first_in, second_in, first_out = threading.Event(), threading.Event(), threading.Event()
    seen: list[tuple[str, object]] = []
    failed: list[BaseException] = []

    def first() -> None:
        with scope:
            first_in.set()
            second_in.wait(WAIT_S)
        seen.append(("first after", current_pool()))
        first_out.set()

    def second() -> None:
        first_in.wait(WAIT_S)
        with scope:
            second_in.set()
            first_out.wait(WAIT_S)
            seen.append(("second inside", current_pool()))
        seen.append(("second after", current_pool()))

    def guarded(target: Any) -> Any:
        def run() -> None:
            try:
                target()
            except BaseException as exc:
                failed.append(exc)

        return run

    threads = [threading.Thread(target=guarded(first)), threading.Thread(target=guarded(second))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(WAIT_S)
    assert failed == []
    assert seen == [("first after", None), ("second inside", scope.pool), ("second after", None)]


def test_a_scope_entered_twice_in_one_context_leaves_the_outer_entry_in_force() -> None:
    scope = WebRouter().budget(Budget())
    with scope:
        with scope:
            pass
        assert current_pool() is scope.pool
    assert current_pool() is None


def test_the_scope_calls_a_router_subclass_override(priced: Table, fake_provider: Any) -> None:
    priced.update(fake=(0.01, None))

    class Tagged(WebRouter):
        def search(self, query: str, **options: Any) -> SearchResponse:
            return super().search(f"tagged {query}", **options)

    with Tagged().budget(Budget(max_cost_usd=1.0)) as scope:
        response = scope.search("q", provider="fake")
    assert response.query == "tagged q"
    assert scope.status().spent_usd == 0.01


async def test_the_async_scope_calls_a_router_subclass_override(priced: Table, fake_provider: Any) -> None:
    class Tagged(AsyncWebRouter):
        async def search(self, query: str, **options: Any) -> SearchResponse:
            return await super().search(f"tagged {query}", **options)

    async with Tagged().budget(Budget()) as scope:
        response = await scope.search("q", provider="fake")
    assert response.query == "tagged q"


def test_a_call_that_starts_late_gets_only_the_time_the_clock_has_left(
    priced: Table, fake_provider: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    limit, late = 0.5, 0.3
    real_limit, real_admit = BudgetPool.limit, ledger.Ledger.admit
    left: list[float] = []

    def delayed(self: BudgetPool, budget: Budget | None) -> Budget:
        limited = real_limit(self, budget)
        time.sleep(late)  # the time left was read; the call's deadline is not set yet
        return limited

    def spy(self: ledger.Ledger, attempt: ledger.Attempt) -> bool:
        deadline = current_deadline()
        left.append(-1.0 if deadline is None else deadline.left())
        return real_admit(self, attempt)

    monkeypatch.setattr(BudgetPool, "limit", delayed)
    monkeypatch.setattr(ledger.Ledger, "admit", spy)
    with WebRouter().budget(Budget(time_limit_s=limit)) as scope:
        scope.search("q", provider="fake")
    assert 0 < left[0] <= limit - late


def test_a_named_backend_under_an_enclosing_deadline_that_has_passed_reserves_nothing(
    priced: Table, fake_provider: Any
) -> None:
    priced.update(fake=(0.01, None))
    with WebRouter().budget(Budget(max_cost_usd=1.0)) as scope, call_deadline(0.01):
        time.sleep(0.02)
        with pytest.raises(BudgetExceededError) as raised:
            scope.search("q", provider="fake")
    assert raised.value.skipped == {"search:fake": SKIP_NO_TIME}
    assert (scope.status().spent_usd, scope.status().reserved_usd) == (0.0, 0.0)


@pytest.mark.parametrize("tool", ["search"], indirect=True)
def test_a_member_after_an_enclosing_deadline_has_passed_is_not_reserved(chain: Chain, priced: Table) -> None:
    chain.raises["first"] = ConfigurationError("first holds no key")
    chain.sleeps["first"] = 0.15  # outlasts the enclosing deadline, then fails
    priced.update(first=(0.01, None), second=(0.02, None))
    with WebRouter().budget(Budget(max_cost_usd=1.0)) as scope, call_deadline(0.1), pytest.raises(WebRouterError):
        scope.search("q", provider=chain.auto())
    assert chain.called == ["first"]
    assert scope.status().spent_usd == 0.01, "second was skipped for no time left, not reserved"
