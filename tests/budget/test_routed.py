"""A budget on a routed call: the router's order and the budget's check both hold, and one record carries both."""

from __future__ import annotations

from typing import Any

import pytest

from tests.budget.conftest import ARGS, Chain, Table
from web_router import AsyncWebRouter, Budget, RouteDecision, RouteRequest, WebRouter

CAP = Budget(max_cost_usd=0.01)


class _Ranker:
    """A router that ranks ``ranking`` first for whichever tool it is asked about."""

    def __init__(self, *ranking: str) -> None:
        self.ranking = list(ranking)

    def route(self, req: RouteRequest) -> RouteDecision:
        assert req.pinned_tool is not None
        return RouteDecision(providers={req.pinned_tool: self.ranking}, reason="ranked")


def _ids(chain: Chain, *names: str) -> list[str]:
    return [f"{chain.tool.name}:{name}" for name in names]


def _check(chain: Chain, response: Any) -> None:
    assert chain.called == ["second"], "the router put third first, but the budget left it out"
    decision = response.decision
    assert decision.router is not None and decision.reason == "ranked"
    assert decision.merged == _ids(chain, "third", "second", "first")
    assert [attempt.id for attempt in decision.attempts] == _ids(chain, "second")
    assert decision.estimated_cost_usd == 0.005


def test_a_routed_call_runs_in_the_routers_order_and_the_budget_still_skips(chain: Chain, priced: Table) -> None:
    priced.update(first=(0.0, None), second=(0.005, None), third=(0.05, None))
    router = WebRouter(router=_Ranker("third", "second"))
    response = getattr(router, chain.tool.name)(ARGS[chain.tool.name], provider=chain.auto(), budget=CAP)
    _check(chain, response)


async def test_a_routed_async_call_holds_both_as_a_sync_one_does(chain: Chain, priced: Table) -> None:
    priced.update(first=(0.0, None), second=(0.005, None), third=(0.05, None))
    router = AsyncWebRouter(router=_Ranker("third", "second"))
    response = await getattr(router, chain.tool.name)(ARGS[chain.tool.name], provider=chain.auto(), budget=CAP)
    _check(chain, response)


@pytest.mark.parametrize("budget", [None, Budget()])
def test_a_routed_call_with_no_cap_runs_the_routers_first(chain: Chain, priced: Table, budget: Budget | None) -> None:
    router = WebRouter(router=_Ranker("third"))
    response = getattr(router, chain.tool.name)(ARGS[chain.tool.name], provider=chain.auto(), budget=budget)
    assert chain.called == ["third"]
    assert response.decision.merged[0] == _ids(chain, "third")[0]


@pytest.mark.parametrize("tool", ["search"], indirect=True)
@pytest.mark.parametrize("own", [None, Budget(max_cost_usd=1.0)])
def test_a_router_in_a_shared_budget_is_shown_the_budget_the_call_is_held_to(
    chain: Chain, priced: Table, own: Budget | None
) -> None:
    priced.update(first=(0.01, None))
    shown: list[Budget | None] = []
    ranker = _Ranker("first")
    router = WebRouter(router=lambda req: shown.append(req.budget) or ranker.route(req))
    with router.budget(Budget(max_cost_usd=0.10)) as shared:
        shared.search("q", provider=chain.auto(), budget=own)
    assert [budget.max_cost_usd if budget else None for budget in shown] == [0.10]
