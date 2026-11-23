"""A preference preserves setup errors and records the budget's excluded members."""

from __future__ import annotations

from typing import Any

import pytest

from tests.budget.conftest import ARGS, Chain, Table
from tests.routing_helpers import MEMBERS
from web_router import AsyncWebRouter, Budget, WebRouter
from web_router.exceptions import ConfigurationError, ProviderNotInstalledError, WebRouterError
from web_router.routing import cost_order


@pytest.fixture(autouse=True)
def default_order(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cost_order, "_runs_default", lambda _chain: True)


@pytest.mark.parametrize("router_cls", [WebRouter, AsyncWebRouter])
@pytest.mark.parametrize("broken", [MEMBERS, ("third",)])
@pytest.mark.parametrize(
    "error",
    [ConfigurationError("setup failure sentinel"), ProviderNotInstalledError("setup failure sentinel", "search")],
)
async def test_preference_keeps_unbuildable_members_failure_details(
    chain: Chain, monkeypatch: pytest.MonkeyPatch, router_cls: Any, broken: tuple[str, ...], error: Exception
) -> None:
    auto = chain.auto()
    original = auto._member
    chain.raises.update({name: ConfigurationError("call failed") for name in MEMBERS})

    def member(position: int) -> Any:
        name = auto._chain[position][0]
        if name in broken:
            raise error
        return original(position)

    monkeypatch.setattr(auto, "_member", member)
    baseline = None
    for prefer in (None, "cheap", "fast"):
        with pytest.raises(WebRouterError) as caught:
            router = router_cls(prefer=prefer)
            response = getattr(router, chain.tool.name)(ARGS[chain.tool.name], provider=auto)
            if router_cls is AsyncWebRouter:
                await response
        failure = (type(caught.value), str(caught.value))
        if prefer is None:
            baseline = failure
        else:
            assert failure == baseline


@pytest.mark.parametrize("prefer", ["cheap", "fast"])
@pytest.mark.parametrize("cap", [Budget(max_cost_usd=0.005), Budget(time_limit_s=60)])
@pytest.mark.parametrize("router_cls", [WebRouter, AsyncWebRouter])
async def test_preferred_record_excludes_over_budget_members_and_keeps_the_reason(
    chain: Chain, priced: Table, prefer: Any, cap: Budget, router_cls: Any
) -> None:
    priced.update(first=(0.008, 90.0), second=(0.001, 1.0), third=(0.002, 2.0))
    router = router_cls(prefer=prefer)
    response = getattr(router, chain.tool.name)(ARGS[chain.tool.name], provider=chain.auto(), budget=cap)
    if router_cls is AsyncWebRouter:
        response = await response
    record = response.decision
    tool = chain.tool.name
    assert record.merged == [f"{tool}:second", f"{tool}:third"]
    assert f"{tool}:first" in record.dropped
    assert (record.ran, response.provider, chain.called) == (0, "second", ["second"])


@pytest.mark.parametrize("tool", ["search"], indirect=True)
@pytest.mark.parametrize("prefer", ["cheap", "fast"])
def test_an_unbuildable_member_that_refuses_the_option_stays_out_of_the_record(
    chain: Chain, priced: Table, monkeypatch: pytest.MonkeyPatch, prefer: Any
) -> None:
    for name in MEMBERS:
        cls = chain.member(name)
        cls.supported_parameters = frozenset() if name == "first" else frozenset({"country"})
        chain.tool.register(name, cls)
    auto = chain.auto()
    original = auto._member

    def member(position: int) -> Any:
        if position == 0:
            raise ConfigurationError("setup failure")
        return original(position)

    monkeypatch.setattr(auto, "_member", member)
    priced.update(first=(0.0, 0.0), second=(0.001, 1.0), third=(0.002, 2.0))
    record = WebRouter(prefer=prefer).search("q", country="US", provider=auto).decision
    assert record.merged == ["search:second", "search:third"]
    assert record.ran == 0
    assert record.decision_id and record.prompt_sha256
