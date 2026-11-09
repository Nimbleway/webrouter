"""Presets with a router and a budget: a preset's value is the call's own, and the response carries both records."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from tests.presets_helpers import REF, definition
from tests.routing_helpers import MEMBERS, TOOLS, Table
from web_router import AsyncWebRouter, Budget, BudgetExceededError, RouteDecision, RouteRequest, ToolName, WebRouter
from web_router.routing import params
from web_router.routing.params import EffortChoice

RESEARCH = next(tool for tool in TOOLS if tool.name == "research")
SEARCH = next(tool for tool in TOOLS if tool.name == "search")


def _logging(tool: Any, log: list[Any]) -> None:
    for name in MEMBERS:

        def entry(self: Any, request: Any, _name: str = name) -> Any:
            log.append((_name, request))
            return getattr(tool.fake, tool.entry)(self, request)

        tool.register(name, type(f"Sent_{name}", (tool.fake,), {"name": name, tool.entry: entry}))


@pytest.fixture
def researched() -> Iterator[list[Any]]:
    log: list[Any] = []
    _logging(RESEARCH, log)
    yield log
    for name in MEMBERS:
        RESEARCH.unregister(name)


@pytest.fixture
def searched() -> Iterator[list[Any]]:
    log: list[Any] = []
    _logging(SEARCH, log)
    yield log
    for name in MEMBERS:
        SEARCH.unregister(name)


def _routed(tool: ToolName, options: dict[str, Any], cls: type = WebRouter, **kwargs: Any) -> Any:
    def route(req: RouteRequest) -> RouteDecision:
        return RouteDecision(providers={tool: ["third"]}, params={tool: options})

    return cls(provider_config={"auto": {"chain": list(MEMBERS)}}, router=route, **kwargs)


@pytest.mark.parametrize("cls", [WebRouter, AsyncWebRouter], ids=["sync", "async"])
async def test_a_router_never_replaces_a_presets_value(researched: list[Any], cls: type) -> None:
    router = _routed("research", {"effort": "low"}, cls)
    router.presets.add(definition(components={"research": {"defaults": {"effort": "high"}}}))
    called = router.research("q", preset=REF)
    response = await called if cls is AsyncWebRouter else called
    assert [request.effort for _, request in researched] == ["high"]
    assert response.decision is not None and response.decision.params is None
    assert response.meta is not None and response.meta.preset.effective == {"effort": "high"}


def test_a_budget_maps_no_effort_over_a_presets(researched: list[Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        params, "map_effort", lambda *a, **k: EffortChoice(effort="medium", source="budget", reason="m")
    )
    router = _routed("research", {})
    router.presets.add(definition(components={"research": {"defaults": {"effort": "high"}}}))
    response = router.research("q", budget=Budget(time_limit_s=600.0), preset=REF)
    assert [request.effort for _, request in researched] == ["high"]
    assert response.decision is None or response.decision.params is None, "the record claims an effort it did not send"


def test_a_routed_call_under_the_routers_preset_carries_both_records(searched: list[Any]) -> None:
    router = _routed("search", {"max_results": 2}, preset=REF)
    router.presets.add(definition(components={"search": {"defaults": {"max_results": 7}}}))
    response = router.search("q")
    assert [request.max_results for _, request in searched] == [7]
    assert list(response.decision.ignored or {}) == ["params.search.max_results"]
    assert response.meta.preset.resolved[0].source == "client"


def test_a_presets_provider_pins_the_call_and_the_router_is_not_asked(searched: list[Any]) -> None:
    asked: list[RouteRequest] = []

    def route(req: RouteRequest) -> RouteDecision:
        asked.append(req)
        return RouteDecision(providers={"search": ["third"]})

    router = WebRouter(provider_config={"auto": {"chain": list(MEMBERS)}}, router=route)
    router.presets.add(definition(components={"search": {"defaults": {"provider": "first"}}}))
    router.search("q", preset=REF)
    assert asked == [] and [name for name, _ in searched] == ["first"]


@pytest.mark.parametrize(
    ("setting", "value"), [("provider_options", {"mode": "x"}), ("effort", "high")], ids=["provider_options", "effort"]
)
def test_a_budget_refusing_a_call_a_preset_shaped_names_the_preset(
    setting: str, value: Any, searched: list[Any], priced: Table
) -> None:
    """Every member is priced, so only the preset's setting -- a vendor mode or tier -- leaves the price unknown."""
    for name in MEMBERS:
        priced[name] = (0.001, 1.0)
    router = WebRouter(provider_config={"auto": {"chain": list(MEMBERS)}})
    defaults = {setting: value, "country": "US"}
    router.presets.add(definition(components={"search": {"defaults": defaults}}))

    router.search("q", budget=Budget(max_cost_usd=1.0))
    assert [name for name, _ in searched] == ["first"]
    with pytest.raises(BudgetExceededError, match=rf"{setting}: set by this call's presets \({REF}\)") as caught:
        router.search("q", budget=Budget(max_cost_usd=1.0), preset=REF)
    assert "country" not in str(caught.value), "a budget prices no country, so its refusal must not name it"

    assert [name for name, _ in searched] == ["first"]
