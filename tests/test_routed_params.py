"""The options a routed call runs with: the ones a router set that pass the merge rule, and research's budget effort."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from tests.routing_helpers import MEMBERS, TOOLS
from tests.routing_helpers import Tool as _Tool
from web_router import Budget, RouteDecision, RouteRequest, WebRouter
from web_router.constants_params import MSG_EFFORT_NONE_FITS_EVERY
from web_router.routing import params
from web_router.routing.params import EffortChoice

NONE_FITS = MSG_EFFORT_NONE_FITS_EVERY.format(efforts="low, high", providers="third, first")
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


def test_with_no_effort_from_the_router_the_budget_maps_one(sent: list[Any], monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[tuple[str, Budget | None]] = []

    def mapped(provider: str, budget: Budget | None, effort: Any = None) -> EffortChoice:
        asked.append((provider, budget))
        return EffortChoice(effort="medium", source="budget", reason="mapped")

    monkeypatch.setattr(params, "map_effort", mapped)
    budget = Budget(time_limit_s=600.0)
    response = _router({}).research("q", budget=budget)
    assert asked == [(name, budget) for name in MEMBERS], "chosen once, over the static order, before the router"
    assert [request.effort for _, request in sent] == ["medium"]
    assert (response.decision.params, response.decision.params_source) == ({"effort": "medium"}, "budget")
    assert response.decision.params_reason == "mapped", "the mapping's own reason, kept"


@pytest.mark.parametrize("tool", [tool for tool in TOOLS if tool.name != "research"], ids=lambda tool: tool.name)
@pytest.mark.usefixtures("called")
def test_the_budget_maps_no_option_on_any_other_method(tool: _Tool, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.routing_helpers import wr
    from web_router.routing import candidates

    monkeypatch.setattr(candidates, "shared_effort", lambda *_: pytest.fail("only research has an effort"))
    found = candidates.build_candidates(wr(), {tool.name: tool.request}, budget=Budget(time_limit_s=60.0))  # type: ignore[dict-item]
    assert found.effort is None


def _two_backends(monkeypatch: pytest.MonkeyPatch, picks: dict[str, str], fits: dict[str, set[str]]) -> None:
    """``third`` then ``first``: each backend's own pick, and the efforts that fit the budget on each."""
    monkeypatch.setattr(
        params, "map_effort", lambda p, b, e=None: EffortChoice(effort=picks.get(p), source=None, reason="")
    )  # type: ignore[arg-type]
    monkeypatch.setattr(params, "effort_misfit", lambda p, e, b: None if e in fits.get(p, set()) else "no fit")


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
    assert response.decision.params_reason == NONE_FITS, "and the record says why"


def test_with_no_router_and_no_effort_that_fits_every_backend_the_record_says_why(
    sent: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    _two_backends(monkeypatch, {"third": "low", "first": "high"}, {"third": {"low"}, "first": {"high"}})
    client = WebRouter(provider_config={"auto": {"chain": ["third", "first"]}})
    response = client.research("q", budget=Budget(time_limit_s=600.0))
    assert [request.effort for _, request in sent] == [None]
    assert (response.decision.params, response.decision.params_reason) == (None, NONE_FITS)


@pytest.fixture
def nimble_sent() -> Iterator[list[Any]]:
    """A fake research backend registered as ``nimble``, so the real price list and effort table apply to it."""
    log: list[Any] = []

    def entry(self: Any, request: Any) -> Any:
        log.append(request)
        return RESEARCH.fake._start(self, request)

    RESEARCH.register("nimble", type("SentNimble", (RESEARCH.fake,), {"name": "nimble", RESEARCH.entry: entry}))
    yield log
    RESEARCH.unregister("nimble")


def test_with_no_router_a_budget_still_maps_research_effort(nimble_sent: list[Any]) -> None:
    wr = WebRouter(provider_config={"auto": {"chain": ["nimble"]}})
    response = wr.research("q", budget=Budget(max_cost_usd=0.10))
    assert [request.effort for request in nimble_sent] == ["low"], "nimble's default has no price: unmapped, refused"
    assert (response.decision.params, response.decision.params_source) == ({"effort": "low"}, "budget")
    assert response.decision.params_reason and response.decision.decision_id, "with its reason, and stamped"


async def test_with_no_router_an_async_budgeted_call_maps_it_too(nimble_sent: list[Any]) -> None:
    from web_router import AsyncWebRouter

    wr = AsyncWebRouter(provider_config={"auto": {"chain": ["nimble"]}})
    await wr.research("q", budget=Budget(max_cost_usd=0.10))
    assert [request.effort for request in nimble_sent] == ["low"]


def test_with_no_router_and_no_budget_research_runs_unchanged(nimble_sent: list[Any]) -> None:
    response = WebRouter(provider_config={"auto": {"chain": ["nimble"]}}).research("q")
    assert [request.effort for request in nimble_sent] == [None] and response.decision is None


def test_a_named_research_backend_gets_the_budgets_effort(nimble_sent: list[Any]) -> None:
    response = WebRouter().research("q", provider="nimble", budget=Budget(max_cost_usd=0.10))
    assert [request.effort for request in nimble_sent] == ["low"], "named, not auto: still mapped, not refused"
    assert (response.decision.params, response.decision.params_source) == ({"effort": "low"}, "budget")


def test_a_research_candidate_is_offered_at_the_price_it_runs_at(monkeypatch: pytest.MonkeyPatch) -> None:
    from web_router.routing.attempts import Attempt
    from web_router.routing.pricing import attempt_cost, attempt_p90
    from web_router.types import ResearchRequest

    monkeypatch.setenv("NIMBLE_API_KEY", "test-key")
    monkeypatch.setenv("PARALLEL_API_KEY", "test-key")
    shown: list[RouteRequest] = []

    def parallel_first(req: RouteRequest) -> RouteDecision:
        shown.append(req)
        return RouteDecision(providers={"research": ["parallel", "nimble"]})

    config = {"auto": {"chain": {"research": ["nimble", "parallel"]}}}
    plan = WebRouter(provider_config=config, router=parallel_first).plan(
        "q", tool="research", budget=Budget(max_cost_usd=0.05)
    )
    runs = ResearchRequest(query="q", effort=plan.params["effort"])
    assert plan.providers["research"][0] == "parallel"
    for offered in shown[0].candidates:
        attempt = Attempt(tool="research", provider=offered.provider, request=runs)
        assert (offered.est_cost_usd, offered.est_latency_s) == (attempt_cost(attempt), attempt_p90(attempt))
