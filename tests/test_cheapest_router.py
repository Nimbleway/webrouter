"""The built-in ``cheapest_first`` router: every call's backends by estimated cost, the free ones first."""

from __future__ import annotations

import pytest

from tests.routing_helpers import Table, Tool, order, wr
from web_router import AsyncWebRouter, Budget, WebRouter, available_routers, cheapest_first
from web_router.routing.contract import Candidate, RouteRequest


def _candidate(provider: str, cost: float | None, tool: str = "search") -> Candidate:
    return Candidate(tool=tool, provider=provider, est_cost_usd=cost)  # type: ignore[arg-type]


def test_it_orders_by_cost_free_first_and_unknown_last_ties_as_offered() -> None:
    offered = (
        _candidate("unknown", None),
        _candidate("paid", 0.008),
        _candidate("free-a", 0.0),
        _candidate("cheap", 0.001),
        _candidate("free-b", 0.0),
    )
    decision = cheapest_first(RouteRequest(prompt="q", candidates=offered, deadline_s=1.0))
    assert decision.providers == {"search": ["free-a", "free-b", "cheap", "paid", "unknown"]}
    assert decision.tools is None, "it never changes the method"


def test_each_method_is_ordered_on_its_own() -> None:
    offered = (_candidate("a", 0.5, "search"), _candidate("b", 0.1, "extract"), _candidate("c", 0.0, "search"))
    decision = cheapest_first(RouteRequest(prompt="q", candidates=offered, deadline_s=1.0))
    assert decision.providers == {"search": ["c", "a"], "extract": ["b"]}


def test_it_is_built_in_by_name() -> None:
    assert "cheapest_first" in available_routers()


@pytest.mark.parametrize("router_cls", [WebRouter, AsyncWebRouter])
async def test_a_router_built_by_name_runs_the_cheapest_member_first(
    router_cls: type, tool: Tool, called: list[str], priced: Table
) -> None:
    priced.update(first=(0.008, None), second=(0.0, None), third=(0.002, None))
    router = wr(router_cls, router="cheapest_first")
    method = getattr(router, tool.name)
    arg = "https://a.test" if tool.name == "extract" else "q"
    result = method(arg, budget=Budget(max_cost_usd=1.0))
    response = await result if router_cls is AsyncWebRouter else result
    assert order(called)[0] == "second", "the free member first, though it fails here and the chain moves on"
    assert response.decision.router == "cheapest_first"
    assert response.decision.merged == [f"{tool.name}:{name}" for name in ("second", "third", "first")]
