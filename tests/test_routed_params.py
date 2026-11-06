"""The options a routed call runs with: the ones a router set that pass the merge rule, and research's budget effort."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from tests.test_auto_seam import MEMBERS, TOOLS, _Tool
from web_router import Budget, RouteDecision, RouteRequest, WebRouter
from web_router.constants_params import MSG_EFFORT_NO_BUDGET
from web_router.routing import decide
from web_router.routing.params import EffortChoice

RESEARCH = next(tool for tool in TOOLS if tool.name == "research")


@pytest.fixture
def sent() -> Iterator[list[Any]]:
    """Research's three members, each logging the request it was sent; ``third`` answers."""
    log: list[Any] = []

    def member(name: str) -> type:
        def entry(self: Any, request: Any) -> Any:
            log.append((name, request))
            return RESEARCH.fake._start(self, request)

        return type(f"Sent_{name}", (RESEARCH.fake,), {"name": name, RESEARCH.entry: entry})

    for name in MEMBERS:
        RESEARCH.register(name, member(name))
    yield log
    for name in MEMBERS:
        RESEARCH.unregister(name)


def _router(params: dict[str, Any]) -> WebRouter:
    def route(req: RouteRequest) -> RouteDecision:
        return RouteDecision(providers={"research": ["third"]}, params={"research": params})

    return WebRouter(provider_config={"auto": {"chain": list(MEMBERS)}}, router=route)


def test_an_effort_the_router_set_is_sent_and_recorded(sent: list[Any]) -> None:
    response = _router({"effort": "low"}).research("q")
    assert [(name, request.effort) for name, request in sent] == [("third", "low")]
    assert (response.decision.params, response.decision.params_source) == ({"effort": "low"}, "router")


def test_an_effort_the_caller_set_is_never_changed_by_the_router(sent: list[Any]) -> None:
    response = _router({"effort": "low"}).research("q", effort="high")
    assert [request.effort for _, request in sent] == ["high"]
    assert response.decision.params is None


def test_an_option_the_router_may_not_set_is_dropped(sent: list[Any]) -> None:
    response = _router({"output_schema": {"type": "object"}}).research("q")
    assert sent[0][1].output_schema is None
    assert response.decision.params is None
    assert list(response.decision.ignored or {}) == ["params.research.output_schema"], "and the record says so"


def test_the_mappings_reason_is_kept_when_it_keeps_the_default(sent: list[Any]) -> None:
    response = _router({}).research("q")
    assert response.decision.params is None
    assert response.decision.params_reason == MSG_EFFORT_NO_BUDGET.format(provider="third")


def test_with_no_effort_from_the_router_the_budget_maps_one(sent: list[Any], monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[tuple[str, Budget | None]] = []

    def mapped(provider: str, budget: Budget | None, effort: Any = None) -> EffortChoice:
        asked.append((provider, budget))
        return EffortChoice(effort="medium", source="budget", reason="mapped")

    monkeypatch.setattr(decide, "map_effort", mapped)
    budget = Budget(time_limit_s=600.0)
    response = _router({}).research("q", budget=budget)
    assert asked == [("third", budget)], "mapped for the backend tried first"
    assert [request.effort for _, request in sent] == ["medium"]
    assert (response.decision.params, response.decision.params_source) == ({"effort": "medium"}, "budget")
    assert response.decision.params_reason == "mapped", "the mapping's own reason, kept"


@pytest.mark.parametrize("tool", [tool for tool in TOOLS if tool.name != "research"], ids=lambda tool: tool.name)
def test_the_budget_maps_no_option_on_any_other_method(tool: _Tool, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(decide, "map_effort", lambda *_: pytest.fail("only research has an effort"))
    assert decide._budget_effort(tool.name, ("x",), {}, (), Budget(max_cost_usd=1.0)) is None  # type: ignore[arg-type]
