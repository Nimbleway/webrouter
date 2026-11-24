"""The data-driven router's provider table: the shipped rows and how the router orders search's backends by them."""

from __future__ import annotations

from typing import Any

import pytest

from tests.candidates_helpers import clear_keys, set_keys
from web_router import Budget, RouteRequest, WebRouter
from web_router.exceptions import ConfigurationError
from web_router.routing.contract import Candidate
from web_router.routing.data_driven import DataDrivenRouter
from web_router.routing.data_driven.providers import shipped_providers
from web_router.types import SearchRequest

KEYED = ("exa", "parallel", "keenable", "you", "nimble", "tavily", "brave")
COST_LINE = ["parallel:parallel-turbo+fast", "exa:exa-lite+fast", "parallel:parallel-advanced+fast"]
COST_LINE += ["exa:exa-deep-lite+fast"]
SPEED_LINE = ["keenable:keenable-realtime+fast", "keenable:keenable-pro+fast", "exa:exa-lite+fast"]
SPEED_LINE += ["exa:exa-deep-lite+fast"]
KNEE_FIRST = [COST_LINE[2], COST_LINE[3], COST_LINE[1], COST_LINE[0]]
"""Best first, after the knee: Parallel's advanced tier overlaps Exa's deep-lite and costs less."""
BEST_FIRST = "by measured quality, the cheapest of the best first by benchmark prices"
ROW = "data-driven 0.1.2: search for verb=question, url_target=none, plural=no, kw_research=no, ui_action=no"


@pytest.fixture
def keyed(monkeypatch: pytest.MonkeyPatch) -> None:
    set_keys(clear_keys(monkeypatch), *KEYED)


def test_the_router_asks_to_be_shown_the_modes_its_rows_name() -> None:
    shipped = shipped_providers()
    assert (len(shipped.search), shipped.measured) == (10, "2026-11-23")
    asked = DataDrivenRouter().extra_modes
    assert asked["parallel"] == ["parallel-turbo+fast", "parallel-advanced+fast"]
    assert asked["nimble"] == ["fast"] and "brave" not in asked, "a row at the backend's default names no mode"


@pytest.mark.parametrize(
    ("config", "first"),
    [
        ({}, KNEE_FIRST),
        ({"prefer": "accurate"}, KNEE_FIRST),
        ({"prefer": "cheap"}, COST_LINE),
        ({"prefer": "fast"}, SPEED_LINE),
    ],
)
def test_plan_tries_the_measured_search_backends_in_the_preferences_order(
    config: dict[str, Any], first: list[str], keyed: None
) -> None:
    with WebRouter(router="data-driven", router_config=config) as router:
        decision = router.plan("who won the 2022 world cup").decision
    assert decision.merged[: len(first)] == [f"search:{entry}" for entry in first]
    order = {"cheap": "cheapest first by benchmark prices", "fast": "fastest first"}.get(
        config.get("prefer"), BEST_FIRST
    )
    assert decision.prefer == config.get("prefer")
    assert decision.reason == f"{ROW}; search backends {order}, by the 2026-11-23 measures"


def test_a_cost_cap_drops_what_the_price_list_says_does_not_fit_and_the_rest_keep_their_order(keyed: None) -> None:
    with WebRouter(router="data-driven", router_config={"prefer": "cheap"}) as router:
        merged = router.plan("who won the 2022 world cup", budget=Budget(max_cost_usd=0.006)).decision.merged
    assert merged[:2] == ["search:parallel:parallel-turbo+fast", "search:parallel:parallel-advanced+fast"]
    assert not any(entry.startswith("search:exa:") for entry in merged)


def test_a_callers_route_modes_false_leaves_one_row_which_is_no_ranking(keyed: None) -> None:
    with WebRouter(router="data-driven", route_modes=False) as router:
        decision = router.plan("who won the 2022 world cup").decision
    assert decision.reason == ROW, "only Brave's row is at a default, so the router ranks no backends"
    assert decision.merged[0] != "search:brave" and not any(entry.count(":") == 2 for entry in decision.merged)


def test_a_pinned_search_is_ordered_and_another_method_is_left_alone(keyed: None) -> None:
    with WebRouter(router="data-driven", route_tools=("search",)) as router:
        req, _ = router._route_request(router.get_provider("auto"), SearchRequest(query="who won the cup"), None)
    assert req.pinned_tool == "search"
    assert DataDrivenRouter().route(req).attempts == [f"search:{entry}" for entry in KNEE_FIRST]


MEASURED = tuple(Candidate(tool="search", provider=name, mode="fast") for name in ("you", "nimble"))
RESEARCHY = "compare the pricing plans of the top 5 CRM tools"


def test_backends_are_ordered_only_when_search_is_the_method_that_runs() -> None:
    router, research = DataDrivenRouter(), Candidate(tool="research", provider="parallel")

    def attempts(*shown: Candidate, pinned: Any = None) -> Any:
        return router.route(RouteRequest(prompt=RESEARCHY, candidates=shown, deadline_s=1, pinned_tool=pinned)).attempts

    assert router.route(RouteRequest(prompt=RESEARCHY, candidates=(), deadline_s=1)).tools == ["research", "search"]
    assert attempts(*MEASURED, research) is None, "research runs: its backends keep auto's order"
    assert attempts(*MEASURED) == ["search:you:fast"], "no research backend, so the call searches"
    assert attempts(*MEASURED, research, pinned="search") == ["search:you:fast"]
    assert attempts(*MEASURED, research, pinned="research") is None


def test_the_reason_says_when_search_runs_because_the_first_method_has_no_backend() -> None:
    router, tail = DataDrivenRouter(), f"search backends {BEST_FIRST}, by the 2026-11-23 measures"
    row = "data-driven 0.1.2: research for verb=analyze, url_target=none"
    decision = router.route(RouteRequest(prompt=RESEARCHY, candidates=MEASURED, deadline_s=1))
    assert decision.reason == f"{row}; research has no backend here, so search runs; {tail}"
    one = router.route(RouteRequest(prompt=RESEARCHY, candidates=MEASURED[:1], deadline_s=1))
    assert (one.attempts, one.reason) == (None, f"{row}; research has no backend here, so search runs")
    pinned = RouteRequest(prompt=RESEARCHY, candidates=MEASURED, deadline_s=1, pinned_tool="search")
    assert router.route(pinned).reason == f"{row}; {tail}", "a pinned search is the caller's choice, not a fallback"


def test_with_no_measured_backend_shown_it_ranks_methods_only() -> None:
    shown = (Candidate(tool="search", provider="first"), Candidate(tool="search", provider="exa", measured=False))
    decision = DataDrivenRouter().route(RouteRequest(prompt="who won the cup", candidates=shown, deadline_s=1))
    assert (decision.attempts, decision.prefer) == (None, None) and "measures" not in decision.reason


def test_a_request_s_own_preference_is_used_when_the_router_has_none() -> None:
    shown = tuple(Candidate(tool="search", provider=name, mode="fast") for name in ("you", "nimble", "tavily"))
    request = RouteRequest(prompt="who won", candidates=shown, deadline_s=1, prefer="fast")
    assert DataDrivenRouter().route(request).attempts == ["search:nimble:fast", "search:you:fast"]
    assert DataDrivenRouter(prefer="cheap").route(request).prefer == "cheap"
    with pytest.raises(ConfigurationError, match="prefer takes"):
        DataDrivenRouter(prefer="balanced")  # type: ignore[arg-type]
