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


def test_a_routed_call_with_no_budget_never_skips_a_member_for_time(chain: Chain, priced: Table) -> None:
    # The ledger a routed call keeps with no budget caps nothing: an hour-long measured p90 still runs.
    priced.update(first=(0.0, 3600.0), second=(0.0, 3600.0), third=(0.0, 3600.0))
    router = WebRouter(router=Ranker("third"))
    response = getattr(router, chain.tool.name)(ARGS[chain.tool.name], provider=chain.auto())
    assert chain.called == ["third"]
    assert [attempt.id for attempt in response.decision.attempts] == _ids(chain, "third")
    assert response.decision.hops is None, "nothing skipped"


@pytest.mark.parametrize("tool", ["search"], indirect=True)
@pytest.mark.parametrize("own", [None, Budget(max_cost_usd=1.0)])
def test_a_router_in_a_shared_budget_is_shown_the_budget_the_call_is_held_to(
    chain: Chain, priced: Table, own: Budget | None
) -> None:
    priced.update(first=(0.01, None))
    shown: list[Budget | None] = []
    ranker = Ranker("first")
    router = WebRouter(router=lambda req: shown.append(req.budget) or ranker.route(req))
    with router.budget(Budget(max_cost_usd=0.10)) as shared:
        shared.search("q", provider=chain.auto(), budget=own)
    assert [budget.max_cost_usd if budget else None for budget in shown] == [0.10]
