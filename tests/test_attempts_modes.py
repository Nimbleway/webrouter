"""A router's ``attempts`` run one backend in several vendor modes: each in its own mode, priced, recorded, and
a failure in one read for what it means for the backend's other modes.

Offline: Exa's API and Tavily are faked (:mod:`tests.attempts_helpers`); nothing is sent.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from tests.attempts_helpers import DEEP, INSTANT, TAVILY, Reply, fake_backends, hooked, price, routed
from tests.candidates_helpers import Shown
from web_router import AsyncWebRouter, Budget, BudgetExceededError, ProviderError, RouteDecision, WebRouter
from web_router.routing.attempts import CallRoute, call_route

EMPTY: dict[str, Any] = {"results": []}


def test_the_attempts_run_in_order_each_in_its_own_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = fake_backends(monkeypatch, EMPTY, EMPTY)
    with routed(INSTANT, DEEP, TAVILY) as wr:
        response = wr.search("q")
    assert (calls.exa, len(calls.tavily), response.provider) == (["instant", "deep"], 1, "tavily")


async def test_the_async_walk_runs_them_the_same(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = fake_backends(monkeypatch, EMPTY)
    async with routed(INSTANT, DEEP, TAVILY, cls=AsyncWebRouter) as wr:
        response = await wr.search("q")
    assert (calls.exa, response.provider, calls.tavily) == (["instant", "deep"], "exa", [])


def test_a_mode_replaces_the_routers_depth_and_every_other_option_still_applies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = fake_backends(monkeypatch, 500)
    params = {"search": {"search_depth": "deep", "max_results": 3}}
    with routed("search:exa:exa-highlights", "search:tavily:lite", TAVILY, params=params) as wr:
        response = wr.search("q")
    assert response.decision is not None and response.decision.params == params["search"], "the router's, kept"
    assert calls.exa == [None], "the content mode was sent, not refused for the router's depth"
    (sent,) = calls.tavily
    assert (sent.search_depth, sent.mode, sent.max_results) == ("lite", "lite", 3)


def test_a_refused_mode_is_taken_back_and_the_next_mode_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = fake_backends(monkeypatch, 422)
    with routed(INSTANT, DEEP, TAVILY) as wr:
        response = wr.search("q", budget=Budget(max_cost_usd=1.0))
    record = response.decision
    assert record is not None and (calls.exa, calls.tavily) == (["instant", "deep"], [])
    assert record.estimated_cost_usd == pytest.approx(price("exa-deep")), "only the mode that ran is charged"
    assert [(hop.id, hop.error) for hop in record.hops or ()] == [(INSTANT, "422")]


def test_a_refused_mode_starts_no_cooldown(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = fake_backends(monkeypatch, 422, 422)
    with routed(INSTANT, TAVILY) as wr:
        wr.search("q")
        wr.search("q")
    assert calls.exa == ["instant", "instant"], "the second call tried Exa again"


_STOPPING: list[Reply] = [403, 429, httpx.ReadTimeout("read timed out"), httpx.ConnectError("reset")]


@pytest.mark.parametrize("reply", _STOPPING, ids=["403", "429", "timeout", "no-status"])
def test_any_other_failure_stops_the_backend_for_the_call(monkeypatch: pytest.MonkeyPatch, reply: Reply) -> None:
    calls = fake_backends(monkeypatch, reply)
    with routed(INSTANT, DEEP, TAVILY) as wr:
        response = wr.search("q")
    assert (calls.exa, len(calls.tavily), response.provider) == (["instant"], 1, "tavily")
    hops = {hop.id: hop.error for hop in (response.decision.hops if response.decision else None) or ()}
    assert hops[DEEP] == "backend_failed"


@pytest.mark.parametrize("kind", ["target", "local"])
def test_a_failure_no_backend_could_do_better_raises(monkeypatch: pytest.MonkeyPatch, kind: str) -> None:
    calls = fake_backends(monkeypatch, ProviderError("exa", "the page's own", kind=kind))
    with routed(INSTANT, DEEP, TAVILY) as wr, pytest.raises(ProviderError, match="the page's own"):
        wr.search("q")
    assert (calls.exa, calls.tavily) == (["instant"], [])


def test_the_answer_is_priced_at_the_mode_that_gave_it(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_backends(monkeypatch, EMPTY)
    with routed(INSTANT, DEEP, TAVILY) as wr:
        response = wr.search("q")
    assert (response.provider, response.cost_source) == ("exa", "estimated")
    assert response.cost_usd == pytest.approx(price("exa-deep")) and price("exa-deep") != price("exa-instant")


def test_the_record_lists_each_mode_and_the_hooks_see_the_full_id(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_backends(monkeypatch, EMPTY, 422)
    seen: list[str] = []
    with routed(INSTANT, "search:exa:exa-fast", DEEP, TAVILY) as wr, call_route(CallRoute(**hooked(seen))):
        response = wr.search("q")
    assert seen == [INSTANT, "search:exa:exa-fast", "failed search:exa:exa-fast", DEEP]
    record = response.decision
    assert record is not None
    assert [(a.id, a.mode) for a in record.attempts or ()] == [(INSTANT, "exa-instant"), (DEEP, "exa-deep")]


def test_a_mode_the_cap_cannot_cover_is_skipped_before_anything_is_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = fake_backends(monkeypatch)
    cap = 0.01  # under either Exa mode, over Tavily's default
    assert min(price("exa-instant") or 0, price("exa-deep") or 0) > cap
    with routed(INSTANT, DEEP, TAVILY) as wr:
        response = wr.search("q", budget=Budget(max_cost_usd=cap))
    assert (calls.exa, len(calls.tavily), response.provider) == ([], 1, "tavily")
    record = response.decision
    assert record is not None and {INSTANT, DEEP} <= set(record.dropped or {}), "the budget dropped both modes"
    assert {INSTANT, DEEP} <= set(record.ignored or {}), "so the router's attempts named no candidate"


def test_a_throttled_mode_cools_its_backend_so_the_next_call_runs_none_of_its_modes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = fake_backends(monkeypatch, 429)
    with routed(INSTANT, DEEP, TAVILY) as wr:
        wr.search("q")
        response = wr.search("q")
    assert (calls.exa, len(calls.tavily)) == (["instant"], 2), "the second call sent Exa nothing"
    hops = {hop.id: hop.error for hop in (response.decision.hops if response.decision else None) or ()}
    assert (hops[INSTANT], hops[DEEP]) == ("429", "backend_failed"), "refused while cooling, then stopped"


def test_a_cap_only_a_mode_fits_still_asks_the_router_and_runs_that_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    calls, cap = fake_backends(monkeypatch), Budget(max_cost_usd=0.015)
    assert (price("exa-instant") or 1) < 0.015, "over the instant mode; the router's ids show the default is not"
    router = Shown(RouteDecision(attempts=[INSTANT]), route_modes=True)
    with WebRouter(provider_config={"auto": {"chain": {"search": ["exa"]}}}, router=router) as wr:
        response = wr.search("q", budget=cap)
    assert (router.ids, calls.exa, response.provider) == ([INSTANT], ["instant"], "exa")


def test_a_router_answer_that_leaves_nothing_to_run_lets_the_budget_say_why(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = fake_backends(monkeypatch)
    router = Shown(RouteDecision(providers={"search": ["exa"]}), route_modes=True)
    wr = WebRouter(provider_config={"auto": {"chain": {"search": ["exa"]}}}, router=router)
    with wr, pytest.raises(BudgetExceededError, match="search:exa skipped"):
        wr.search("q", budget=Budget(max_cost_usd=0.015))
    assert router.ids and calls.exa == [], "asked, since a mode fits; then nothing sent"


def test_a_cap_of_nothing_sends_nothing_and_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = fake_backends(monkeypatch)
    with routed(INSTANT, DEEP, TAVILY) as wr, pytest.raises(BudgetExceededError):
        wr.search("q", budget=Budget(max_cost_usd=0.0))
    assert (calls.exa, calls.tavily) == ([], [])
