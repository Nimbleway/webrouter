"""The ``fallback`` router's time: each member's own limit, their sum, and the call's deadline cut across them."""

from __future__ import annotations

import time

import pytest

from tests.routing_fallback_helpers import answers, aslow, outcomes, request, slow
from web_router import AsyncWebRouter, Budget, WebRouter
from web_router.constants_invoke import ROUTER_TIMEOUT_S
from web_router.routing.fallback import FallbackRouter


def test_its_limit_is_the_sum_of_its_members_and_the_default_holds_for_one_that_declares_none() -> None:
    router = FallbackRouter(routers=[answers("a", timeout_s=0.3), answers("b")])
    assert router.timeout_s == pytest.approx(0.3 + ROUTER_TIMEOUT_S)


def test_a_member_given_more_than_the_default_gets_it() -> None:
    with WebRouter(router="fallback", router_config={"routers": [slow(0.15, timeout_s=0.5)]}) as wr:
        plan = wr.plan("who won the 2022 world cup")
    assert plan.decision.reason == "slow", "the router's limit is its members', not the 50 ms default"


def test_a_member_slower_than_its_own_limit_is_cut_and_the_next_is_asked() -> None:
    log: list[str] = []
    config = {"routers": [slow(1.0, timeout_s=0.05, log=log), answers("next", log, timeout_s=1.0)]}
    with WebRouter(router="fallback", router_config=config) as wr:
        plan = wr.plan("who won the 2022 world cup")
    assert log == ["slow", "next"], "the slow member was sent the prompt before it was cut"
    assert plan.decision.reason == "next"
    first, second = plan.decision.members or []
    assert (first.outcome, second.outcome) == ("router_timeout", "ok")
    assert 40 <= first.ms < 500


async def test_an_async_member_past_its_limit_is_cut_from_async_code() -> None:
    config = {"routers": [aslow(1.0, timeout_s=0.05), answers("next", timeout_s=1.0)]}
    async with AsyncWebRouter(router="fallback", router_config=config) as wr:
        plan = await wr.plan("who won the 2022 world cup")
    assert [m.outcome for m in plan.decision.members or []] == ["router_timeout", "ok"]


def test_the_deadline_is_shared_and_no_member_is_asked_once_it_is_spent() -> None:
    log: list[str] = []
    router = FallbackRouter(routers=[slow(1.0, timeout_s=5.0, log=log), answers("late", log, timeout_s=5.0)])
    started = time.perf_counter()
    decision = router.route(request(deadline_s=0.1))
    assert time.perf_counter() - started < 0.5
    assert log == ["slow"] and outcomes(decision) == [("custom", "router_timeout", False)]


async def test_each_member_gets_the_time_left_when_that_is_less_than_its_own_limit() -> None:
    def members() -> list[object]:
        return [aslow(1.0, timeout_s=0.1), aslow(0.08, timeout_s=0.1)]

    roomy = await FallbackRouter(routers=members()).aroute(request(deadline_s=0.4))
    assert [m.outcome for m in roomy.members] == ["router_timeout", "ok"]
    tight = await FallbackRouter(routers=members()).aroute(request(deadline_s=0.15))
    assert [m.outcome for m in tight.members] == ["router_timeout", "router_timeout"], "the second had 50 ms left"


def test_a_budget_time_limit_cuts_the_whole_fallback() -> None:
    log: list[str] = []
    config = {"routers": [slow(1.0, timeout_s=5.0, log=log), answers("late", log, timeout_s=5.0)]}
    with WebRouter(router="fallback", router_config=config) as wr:
        plan = wr.plan("who won the 2022 world cup", budget=Budget(time_limit_s=0.1))
        time.sleep(0.2)  # long enough for the fallback, still running on its thread, to have asked the next
    assert plan.decision.reason == "router_timeout"
    assert log == ["slow"], "the next member is never sent the prompt once the time is spent"
