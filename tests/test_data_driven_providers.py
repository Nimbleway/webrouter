"""The data-driven router's provider table: the shipped rows and how the router orders search's backends by them."""

from __future__ import annotations

from typing import Any

import pytest

from tests.candidates_helpers import clear_keys, set_keys
from web_router import Budget, RouteRequest, WebRouter
from web_router.constants_data_driven import DATA_DRIVEN_MANIFEST_FILE
from web_router.exceptions import ConfigurationError
from web_router.routing.contract import Candidate
from web_router.routing.data_driven import DataDrivenRouter
from web_router.routing.data_driven import providers as providers_module
from web_router.routing.data_driven.providers import ProviderTable, shipped_providers
from web_router.types import SearchRequest

KEYED = ("exa", "parallel", "keenable", "you", "nimble", "tavily", "brave")
COST_LINE = ["parallel:parallel-turbo+fast", "exa:exa-lite+fast", "parallel:parallel-advanced+fast"]
COST_LINE += ["exa:exa-deep-lite+fast"]
SPEED_LINE = ["keenable:keenable-realtime+fast", "keenable:keenable-pro+fast", "exa:exa-lite+fast"]
SPEED_LINE += ["exa:exa-deep-lite+fast"]
KNEE_FIRST = [COST_LINE[2], COST_LINE[3], COST_LINE[1], COST_LINE[0]]
"""Best first, after the knee: Parallel's advanced tier overlaps Exa's deep-lite and costs less."""


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
    order = {"cheap": "cheapest first", "fast": "fastest first"}.get(config.get("prefer"), "the cheapest of the best")
    assert decision.prefer == config.get("prefer")
    assert f"search backends {order}" in decision.reason.replace("by measured quality, ", "")
    assert decision.reason.endswith("by the 2026-11-23 measures")


def test_a_cost_cap_drops_what_the_price_list_says_does_not_fit_and_the_rest_keep_their_order(keyed: None) -> None:
    with WebRouter(router="data-driven", router_config={"prefer": "cheap"}) as router:
        merged = router.plan("who won the 2022 world cup", budget=Budget(max_cost_usd=0.006)).decision.merged
    assert merged[:2] == ["search:parallel:parallel-turbo+fast", "search:parallel:parallel-advanced+fast"]
    assert not any(entry.startswith("search:exa:") for entry in merged)


def test_a_callers_route_modes_false_leaves_only_the_rows_at_a_backends_default(keyed: None) -> None:
    with WebRouter(router="data-driven", route_modes=False) as router:
        merged = router.plan("who won the 2022 world cup").decision.merged
    assert merged[0] == "search:brave" and not any(entry.count(":") == 2 for entry in merged)


def test_a_pinned_search_is_ordered_and_another_method_is_left_alone(keyed: None) -> None:
    with WebRouter(router="data-driven", route_tools=("search",)) as router:
        req, _ = router._route_request(router.get_provider("auto"), SearchRequest(query="who won the cup"), None)
    assert req.pinned_tool == "search"
    assert DataDrivenRouter().route(req).attempts == [f"search:{entry}" for entry in KNEE_FIRST]
    research = RouteRequest(
        prompt="q", candidates=(Candidate(tool="research", provider="parallel"),), deadline_s=1, pinned_tool="research"
    )
    assert DataDrivenRouter().route(research).attempts is None


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


@pytest.mark.parametrize("row", [{"id": 1}, {"id": "search:a", "quality": "high"}, {"id": "search:a", "quality": -1}])
def test_a_row_without_an_id_and_four_measures_is_refused(row: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    full = {"quality": 1, "ci95": 1, "usd_per_1k": 1, "p50_s": 1} | row
    named = {"provider_table": {"file": "provider_table.json", "sha256": None}}
    files = {"manifest.json": named, "provider_table.json": {"measured": "v", "search": [full]}}
    monkeypatch.setattr(providers_module, "read_release", lambda name, **_: files[name])
    shipped_providers.cache_clear()
    try:
        with pytest.raises(ConfigurationError, match="needs an id and four measures"):
            shipped_providers()
        files["manifest.json"] = {}
        shipped_providers.cache_clear()
        assert shipped_providers() == ProviderTable(), "a release that names no provider table ranks no backends"
    finally:
        shipped_providers.cache_clear()


def test_a_provider_table_that_is_not_the_one_the_manifest_names_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    real = providers_module.read_release

    def altered(name: str, **kwargs: Any) -> Any:
        data = real(name, **kwargs)
        if name == DATA_DRIVEN_MANIFEST_FILE:
            data["provider_table"]["sha256"] = "0" * 64
        return data

    monkeypatch.setattr(providers_module, "read_release", altered)
    shipped_providers.cache_clear()
    try:
        with pytest.raises(ConfigurationError, match="checksum does not match"):
            shipped_providers()
    finally:
        shipped_providers.cache_clear()
