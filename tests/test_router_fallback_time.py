"""The ``fallback`` router's time: each member's own limit, their sum, and the call's deadline cut across them."""

from __future__ import annotations

import time

import pytest

from tests.routing_fallback_helpers import answers, aslow, outcomes, request, slow
from web_router import AsyncWebRouter, Budget, RouteDecision, RouteRequest, RouterError, WebRouter
from web_router.constants_fallback import FALLBACK_MARGIN_S
from web_router.constants_invoke import ROUTER_TIMEOUT_S
from web_router.exceptions import ConfigurationError
from web_router.routing._runner import TimedOut
from web_router.routing.fallback import FallbackRouter


def test_its_limit_is_the_sum_of_its_members_and_the_default_holds_for_one_that_declares_none() -> None:
    router = FallbackRouter(routers=[answers("a", timeout_s=0.3), answers("b")])
    assert router.timeout_s == pytest.approx(0.3 + ROUTER_TIMEOUT_S + FALLBACK_MARGIN_S)


@pytest.mark.parametrize("limit", [FALLBACK_MARGIN_S, 0.01])
def test_a_time_limit_inside_its_margin_is_refused(limit: float) -> None:
    config = {"routers": [answers("a")], "timeout_s": limit}
    with pytest.raises(ConfigurationError, match=r"timeout_s must be more than its 0\.02 s margin"):
        WebRouter(router="fallback", router_config=config)
    assert FallbackRouter(routers=[answers("a")], timeout_s=0.03).timeout_s == 0.03


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
    assert decision.timed_out, "cut short by the time left, not by its own limit: the walk timed out"


async def test_each_member_gets_the_time_left_when_that_is_less_than_its_own_limit() -> None:
    def members() -> list[object]:
        return [aslow(1.0, timeout_s=0.1), aslow(0.08, timeout_s=0.1)]

    roomy = await FallbackRouter(routers=members()).aroute(request(deadline_s=0.4))
    assert [m.outcome for m in roomy.members] == ["router_timeout", "ok"] and not roomy.timed_out
    tight = await FallbackRouter(routers=members()).aroute(request(deadline_s=0.15))
    assert [m.outcome for m in tight.members] == ["router_timeout", "router_timeout"], "the second had 30 ms left"
    assert tight.timed_out


def test_a_budget_time_limit_cuts_the_whole_fallback() -> None:
    log: list[str] = []
    config = {"routers": [slow(1.0, timeout_s=5.0, log=log), answers("late", log, timeout_s=5.0)]}
    with WebRouter(router="fallback", router_config=config) as wr:
        plan = wr.plan("who won the 2022 world cup", budget=Budget(time_limit_s=0.1))
        time.sleep(0.2)  # long enough for the fallback, still running on its thread, to have asked the next
    assert plan.decision.reason == "router_timeout" and plan.decision.router_timeout
    members = plan.decision.members or []
    assert [(m.name, m.outcome, m.counted) for m in members] == [("custom", "router_timeout", False)]
    assert log == ["slow"], "the next member is never sent the prompt once the time is spent"


def test_members_that_fail_with_time_to_spare_are_empty_not_a_timeout() -> None:
    config = {"routers": [slow(1.0, timeout_s=0.05)], "timeout_s": 1.0}
    with WebRouter(router="fallback", router_config=config) as wr:
        plan = wr.plan("who won the 2022 world cup")
    assert plan.decision.reason == "router_empty", "cut by its own limit, not the call's: time was left"
    assert [m.outcome for m in plan.decision.members or []] == ["router_timeout"]


def test_fail_on_router_error_raises_a_timeout_when_the_time_ran_out() -> None:
    config = {"routers": [slow(1.0, timeout_s=5.0)]}
    with (
        WebRouter(router="fallback", router_config=config, fail_on_router_error=True) as wr,
        pytest.raises(RouterError, match="router_timeout"),
    ):
        wr.plan("who won the 2022 world cup", budget=Budget(time_limit_s=0.1))


def test_a_member_reporting_its_own_timeout_early_leaves_time_for_the_next() -> None:
    def early(req: RouteRequest) -> RouteDecision:
        raise TimedOut  # its own transport gave up, long before the time the fallback had left

    early.timeout_s = 5.0  # type: ignore[attr-defined]  # given less than this: only 1 s is left
    decision = FallbackRouter(routers=[early, answers("next")]).route(request(deadline_s=1.0))
    assert outcomes(decision) == [("custom", "router_timeout", False), ("custom", "ok", True)]
    assert not decision.timed_out


def test_a_last_member_given_the_time_left_that_times_out_is_the_walk_timing_out() -> None:
    inner = {"name": "fallback", "routers": [slow(0.4, timeout_s=1.0)]}  # stops its own 20 ms margin early
    with WebRouter(router="fallback", router_config={"routers": [inner], "timeout_s": 0.2}) as wr:
        plan = wr.plan("who won the 2022 world cup")
    assert plan.decision.reason == "router_timeout"
    assert [m.outcome for m in plan.decision.members or []] == ["router_timeout"]


def test_a_member_cut_short_that_times_out_at_once_still_leaves_the_next() -> None:
    def early(req: RouteRequest) -> RouteDecision:
        raise TimedOut

    early.timeout_s = 5.0  # type: ignore[attr-defined]  # cut to the 30 ms left, and gives up at once
    decision = FallbackRouter(routers=[early, answers("next")]).route(request(deadline_s=FALLBACK_MARGIN_S + 0.03))
    assert outcomes(decision) == [("custom", "router_timeout", False), ("custom", "ok", True)]
