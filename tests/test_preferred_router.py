"""The built-in ``preferred`` router: each call's backends by its ``prefer``, cheap, fast or accurate."""

from __future__ import annotations

from typing import Any

import pytest

from tests.routing_helpers import Table, Tool, order, wr
from web_router import AsyncWebRouter, Budget, PreferredRouter, WebRouter
from web_router.exceptions import ConfigurationError
from web_router.routing.contract import Candidate, RouteRequest

SCORES = {"search": {"a": 0.2, "b": 0.5, "c:c-deep": 0.9}}


def _req(*candidates: Candidate) -> RouteRequest:
    return RouteRequest(prompt="q", candidates=candidates, deadline_s=1.0)


def _c(provider: str, cost: float | None = None, p90: float | None = None, **kw: Any) -> Candidate:
    return Candidate(tool="search", provider=provider, est_cost_usd=cost, est_latency_s=p90, **kw)


@pytest.fixture(autouse=True)
def _known(monkeypatch: pytest.MonkeyPatch) -> None:
    """Score the fake backends a, b and c, and c's one mode, as the registry and price list would."""
    monkeypatch.setattr("web_router.registry.available_providers", lambda: ["a", "b", "c"])
    monkeypatch.setattr("web_router.prices_modes.canonical_parts", lambda provider, method, parts: "+".join(parts))


@pytest.mark.parametrize(
    ("prefer", "expected"),
    [("cheap", ["b", "a", "c", "u"]), ("fast", ["c", "a", "b", "u"]), ("accurate", ["b", "a", "u", "c"])],
)
def test_it_orders_by_its_preference_unknowns_last(prefer: Any, expected: list[str]) -> None:
    offered = (_c("u"), _c("a", 0.01, 2.0), _c("b", 0.0, 3.0), _c("c", 0.02, 1.0))
    decision = PreferredRouter(prefer, accuracy=SCORES if prefer == "accurate" else None).route(_req(*offered))
    assert (decision.providers, decision.tools) == ({"search": expected}, None), "it never changes the method"
    assert (decision.prefer, decision.reason) == (prefer, f"{prefer} first")


def test_ties_keep_the_order_offered() -> None:
    offered = (_c("b", 0.01), _c("a", 0.01), _c("c", 0.01))
    assert PreferredRouter("cheap").route(_req(*offered)).providers == {"search": ["b", "a", "c"]}


def test_each_method_is_ordered_on_its_own() -> None:
    offered = (_c("a", 0.5), Candidate(tool="extract", provider="b", est_cost_usd=0.1), _c("c", 0.0))
    assert PreferredRouter("cheap").route(_req(*offered)).providers == {"search": ["c", "a"], "extract": ["b"]}


def test_a_mode_is_scored_on_its_own_and_a_member_that_is_not_the_measured_backend_is_unknown() -> None:
    offered = (_c("a"), _c("b", measured=False), _c("c", mode="c-deep"))
    router = PreferredRouter("accurate", accuracy=SCORES)
    assert router.route_modes is True, "scores for a mode show the router modes"
    assert router.route(_req(*offered)).attempts == ["search:c:c-deep", "search:a", "search:b"]


def test_it_needs_a_known_preference() -> None:
    with pytest.raises(ConfigurationError, match="preferred router takes prefer="):
        PreferredRouter("balanced")  # type: ignore[arg-type]


@pytest.mark.parametrize("accuracy", [None, {}])
def test_accurate_needs_scores(accuracy: Any) -> None:
    with pytest.raises(ConfigurationError, match="holds none"):
        PreferredRouter("accurate", accuracy=accuracy)


@pytest.mark.parametrize("accuracy", [SCORES, {}])
@pytest.mark.parametrize("prefer", ["cheap", "fast"])
def test_scores_with_a_preference_that_never_reads_them_are_refused(prefer: Any, accuracy: Any) -> None:
    with pytest.raises(ConfigurationError, match="never reads accuracy scores"):
        PreferredRouter(prefer, accuracy=accuracy)


def test_it_reads_scores_from_a_file_and_shows_modes_only_when_one_is_scored(tmp_path: Any) -> None:
    path = tmp_path / "scores.json"
    path.write_text('{"search": {"a": 0.4}}', encoding="utf-8")
    router = PreferredRouter("accurate", accuracy=str(path))
    assert (router.scores, router.route_modes) == ({"search": {"a": 0.4}}, False)


@pytest.mark.parametrize("router_cls", [WebRouter, AsyncWebRouter])
async def test_built_by_name_it_runs_the_fastest_member_first(
    router_cls: type, tool: Tool, called: list[str], priced: Table
) -> None:
    priced.update(first=(0.001, 3.0), second=(0.003, 1.0), third=(0.002, 2.0))
    router = wr(router_cls, router="preferred", router_config={"prefer": "fast"})
    arg = "https://a.test" if tool.name == "extract" else "q"
    result = getattr(router, tool.name)(arg, budget=Budget(max_cost_usd=1.0))
    response = await result if router_cls is AsyncWebRouter else result
    assert order(called)[0] == "second"
    assert response.decision.merged == [f"{tool.name}:{name}" for name in ("second", "third", "first")]
    assert (response.decision.router, response.decision.prefer) == ("preferred", "fast")
