"""A budget on a routed call: the router's order and the budget's check both hold, and one record carries both."""

from __future__ import annotations

from typing import Any

import pytest

from tests.budget.conftest import ARGS, Chain, Table
from tests.routing_helpers import Ranker
from web_router import AsyncWebRouter, Budget, WebRouter

CAP = Budget(max_cost_usd=0.01)


def _ids(chain: Chain, *names: str) -> list[str]:
    return [f"{chain.tool.name}:{name}" for name in names]


def _check(chain: Chain, response: Any) -> None:
    assert chain.called == ["second"], "the router put third first, but the budget left it out"
    decision = response.decision
    assert decision.router is not None and decision.reason == "ranked"
    assert "third" not in str(decision.offered) and _ids(chain, "third")[0] in decision.dropped
    assert decision.merged == _ids(chain, "second", "first")
    assert [attempt.id for attempt in decision.attempts] == _ids(chain, "second")
    assert decision.estimated_cost_usd == 0.005


def test_a_routed_call_runs_in_the_routers_order_and_the_budget_still_skips(chain: Chain, priced: Table) -> None:
    priced.update(first=(0.0, None), second=(0.005, None), third=(0.05, None))
    router = WebRouter(router=Ranker("third", "second"))
    response = getattr(router, chain.tool.name)(ARGS[chain.tool.name], provider=chain.auto(), budget=CAP)
    _check(chain, response)


async def test_a_routed_async_call_holds_both_as_a_sync_one_does(chain: Chain, priced: Table) -> None:
    priced.update(first=(0.0, None), second=(0.005, None), third=(0.05, None))
    router = AsyncWebRouter(router=Ranker("third", "second"))
    response = await getattr(router, chain.tool.name)(ARGS[chain.tool.name], provider=chain.auto(), budget=CAP)
    _check(chain, response)


@pytest.mark.parametrize("budget", [None, Budget()])
def test_a_routed_call_with_no_cap_runs_the_routers_first(chain: Chain, priced: Table, budget: Budget | None) -> None:
    router = WebRouter(router=Ranker("third"))
    response = getattr(router, chain.tool.name)(ARGS[chain.tool.name], provider=chain.auto(), budget=budget)
    assert chain.called == ["third"]
    assert response.decision.merged[0] == _ids(chain, "third")[0]
