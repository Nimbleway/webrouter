"""The options a routed call runs with: the ones a router set that pass the merge rule, and research's budget effort."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from tests.test_auto_seam import MEMBERS, TOOLS, _Tool
from web_router import Budget, RouteDecision, RouteRequest, WebRouter
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


def test_with_no_effort_from_the_router_the_budget_maps_one(sent: list[Any], monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[tuple[str, Budget | None]] = []

    def mapped(provider: str, budget: Budget | None, effort: Any = None) -> EffortChoice:
        asked.append((provider, budget))
        return EffortChoice(effort="medium", source="budget", reason="mapped")

    monkeypatch.setattr(decide, "map_effort", mapped)
    budget = Budget(time_limit_s=600.0)
    response = _router({}).research("q", budget=budget)
    assert asked[0] == ("third", budget), "the backend tried first picks first"
    assert [request.effort for _, request in sent] == ["medium"]
    assert (response.decision.params, response.decision.params_source) == ({"effort": "medium"}, "budget")


@pytest.mark.parametrize("tool", [tool for tool in TOOLS if tool.name != "research"], ids=lambda tool: tool.name)
def test_the_budget_maps_no_option_on_any_other_method(tool: _Tool, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(decide, "map_effort", lambda *_: pytest.fail("only research has an effort"))
    assert decide._budget_effort(tool.name, ("x",), {}, (), Budget(max_cost_usd=1.0)) is False  # type: ignore[arg-type]


def _two_backends(monkeypatch: pytest.MonkeyPatch, picks: dict[str, str], fits: dict[str, set[str]]) -> None:
    """``third`` then ``first``: each backend's own pick, and the efforts that fit the budget on each."""
    from web_router.routing import merge_params

    monkeypatch.setattr(
        decide, "map_effort", lambda p, b, e=None: EffortChoice(effort=picks.get(p), source=None, reason="")
    )  # type: ignore[arg-type]
    monkeypatch.setattr(merge_params, "effort_misfit", lambda p, e, b: None if e in fits.get(p, set()) else "no fit")


def _two(params: dict[str, Any]) -> WebRouter:
    def route(req: RouteRequest) -> RouteDecision:
        return RouteDecision(providers={"research": ["third", "first"]}, params={"research": params})

    return WebRouter(provider_config={"auto": {"chain": ["third", "first"]}}, router=route)


def test_the_budget_effort_fits_every_backend_in_the_order(sent: list[Any], monkeypatch: pytest.MonkeyPatch) -> None:
    # third's pick, low, does not fit first; first's pick, high, fits both, so every attempt runs at high.
    _two_backends(monkeypatch, {"third": "low", "first": "high"}, {"third": {"low", "high"}, "first": {"high"}})
    response = _two({}).research("q", budget=Budget(time_limit_s=600.0))
    assert [request.effort for _, request in sent] == ["high"]
    assert response.decision.params == {"effort": "high"}


def test_with_no_effort_that_fits_every_backend_none_is_set(sent: list[Any], monkeypatch: pytest.MonkeyPatch) -> None:
    _two_backends(monkeypatch, {"third": "low", "first": "high"}, {"third": {"low"}, "first": {"high"}})
    response = _two({}).research("q", budget=Budget(time_limit_s=600.0))
    assert [request.effort for _, request in sent] == [None], "each backend runs at its own default"
    assert response.decision.params is None
