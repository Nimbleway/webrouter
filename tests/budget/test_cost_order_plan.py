"""``plan()`` and ``run()`` under a cost cap: a router that ranks no backends leaves them in ``auto``'s cost order.

The default ``data-driven`` router picks the method and ranks nothing, so a capped plan runs cheapest first, as a capped
``search()`` does; a router that ranks the backends itself keeps its order. The members are the harness's fakes.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.budget.conftest import Chain, Table
from tests.routing_helpers import MEMBERS, Ranker
from web_router import AsyncWebRouter, Budget, WebRouter
from web_router.routing import cost_order

CAP = Budget(max_cost_usd=1.0)
CHEAPEST_FIRST = ("second", "third", "first")
pytestmark = pytest.mark.parametrize("tool", ["search"], indirect=True)


@pytest.fixture
def costed(chain: Chain, priced: Table, monkeypatch: pytest.MonkeyPatch) -> Chain:
    """``chain``'s three members alike in the benchmark, ``second`` cheapest and ``first`` dearest."""
    measured = {name: {"accuracy": 0.5} for name in MEMBERS}
    monkeypatch.setattr(
        cost_order, "chains", lambda: {"search": {"noise_floor": {"value": 0.05}, "measured": measured}}
    )
    monkeypatch.setattr(cost_order, "_runs_default", lambda _chain: True)
    priced.update(first=(0.008, None), second=(0.002, None), third=(0.005, None))
    return chain


def _router(cls: type = WebRouter, router: Any = "data-driven") -> Any:
    return cls(provider_config={"auto": {"chain": list(MEMBERS)}}, router=router)


def test_a_capped_plan_on_the_default_router_is_in_cost_order(costed: Chain) -> None:
    plan = _router().plan("q", budget=CAP)
    assert plan.decision.router == "data-driven"
    assert plan.providers == {"search": CHEAPEST_FIRST}
    assert _router().plan("q").providers == {"search": MEMBERS}, "no cost cap: the static order"


def test_a_capped_run_on_the_default_router_calls_the_cheapest_first(costed: Chain) -> None:
    response = _router().run("q", budget=CAP)
    assert (response.provider, costed.called[0]) == ("second", "second")
    assert response.decision.merged == [f"search:{name}" for name in CHEAPEST_FIRST]


async def test_the_async_router_plans_and_runs_in_the_same_order(costed: Chain) -> None:
    router = _router(AsyncWebRouter)
    assert (await router.plan("q", budget=CAP)).providers == {"search": CHEAPEST_FIRST}
    assert (await router.run("q", budget=CAP)).provider == "second"


def test_a_router_that_ranks_the_backends_keeps_its_order(costed: Chain) -> None:
    plan = _router(router=Ranker("first")).plan("q", budget=CAP, tool="search")
    assert plan.providers == {"search": MEMBERS}, "its ranking first, then the static order"
