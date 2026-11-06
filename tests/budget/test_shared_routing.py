"""A shared budget meets routing: ``plan`` and ``run`` on the scope, and the budget a router is shown inside it."""

from __future__ import annotations

from typing import Any

import pytest

from tests.budget.conftest import Chain, Table
from tests.test_auto_seam import MEMBERS
from web_router import AsyncBudgetScope, AsyncWebRouter, Budget, BudgetScope, RouteDecision, RouteRequest, WebRouter

SHARED = Budget(max_cost_usd=0.10)


def _seeing() -> Any:
    """A router that keeps the requests it is shown and leaves the order to the static one."""

    def route(req: RouteRequest) -> RouteDecision:
        route.seen.append(req)  # type: ignore[attr-defined]
        return RouteDecision()

    route.seen = []  # type: ignore[attr-defined]
    return route


def _wr(cls: type = WebRouter, router: Any = None) -> Any:
    return cls(provider_config={"auto": {"chain": list(MEMBERS)}}, router=router)


def test_the_scope_has_plan_and_run_on_both_routers() -> None:
    assert all(callable(getattr(scope, name)) for scope in (BudgetScope, AsyncBudgetScope) for name in ("plan", "run"))


@pytest.mark.parametrize("tool", ["search"], indirect=True)
def test_run_through_the_scope_spends_from_it(chain: Chain, priced: Table) -> None:
    priced.update(first=(0.04, None))
    with _wr().budget(SHARED) as shared:
        shared.run("q", tool="search")
        shared.run("q", tool="search")
    assert chain.called == ["first", "first"]
    assert shared.status().spent_usd == 0.08


@pytest.mark.parametrize("tool", ["search"], indirect=True)
async def test_async_run_through_the_scope_spends_from_it(chain: Chain, priced: Table) -> None:
    priced.update(first=(0.04, None))
    async with _wr(AsyncWebRouter).budget(SHARED) as shared:
        await shared.run("q", tool="search")
    assert shared.status().spent_usd == 0.04


@pytest.mark.parametrize("tool", ["search"], indirect=True)
def test_a_plan_in_a_scope_shows_the_router_the_scope_budget(chain: Chain, priced: Table) -> None:
    priced.update(first=(0.04, None))
    router = _seeing()
    with _wr(router=router).budget(SHARED) as shared:
        shared.run("q", tool="search")
        shared.plan("q", tool="search")
    shown = router.seen[-1].budget
    assert shown is not None and shown.max_cost_usd == SHARED.max_cost_usd


@pytest.mark.parametrize("tool", ["search"], indirect=True)
def test_a_routed_call_in_a_scope_shows_the_router_the_scope_budget(chain: Chain, priced: Table) -> None:
    priced.update(first=(0.01, None))
    router = _seeing()
    wr = _wr(router=router)
    with wr.budget(SHARED):
        wr.search("q", provider="auto")
    (req,) = router.seen
    assert req.budget is not None and req.budget.max_cost_usd == SHARED.max_cost_usd
