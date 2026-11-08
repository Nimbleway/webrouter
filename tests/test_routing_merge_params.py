"""The merge rule's rule 5: a router's options, checked against the allowlist, the caller, the backends and the budget.

Prices come from the shipped price list: research on nimble is $0.025 at low
and $0.5 at high, parallel's is $0.1 at high.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.merge_helpers import merge_with
from web_router import Budget, Candidate, RouteDecision, RouteRequest
from web_router.constants_merge import MSG_PARAM_NOT_ALLOWED
from web_router.routing.merge import merge


def _private(url: str) -> str | None:
    return "a private address" if "//10." in url else None


@pytest.mark.parametrize(
    ("tool", "params", "budget", "kept"),
    [
        ("search", {"max_results": 3, "search_depth": "deep"}, None, {"max_results": 3}),
        ("search", {"max_results": 0}, None, {}),
        ("search", {"max_results": True}, None, {}),
        ("search", {"search_depth": "bottomless"}, None, {}),
        ("research", {"effort": "high"}, None, {"effort": "high"}),
        ("research", {"effort": "extreme"}, None, {}),
        ("research", {"effort": "low"}, Budget(max_cost_usd=0.2), {"effort": "low"}),
        ("research", {"effort": "high"}, Budget(max_cost_usd=0.2), {}),
        ("search", {"search_depth": "deep"}, Budget(max_cost_usd=1.0), {}),
        ("answer", {"max_results": 3}, None, {}),
    ],
)
def test_rule_5_a_routers_options_are_checked_and_each_one_dropped_is_noted(
    tool: str, params: dict[str, Any], budget: Budget | None, kept: dict[str, Any]
) -> None:
    merged = merge_with(RouteDecision(params={tool: params}), pinned=tool, budget=budget)
    assert dict(merged.params) == kept
    assert set(merged.notes.ignored) == {f"params.{tool}.{k}" for k in params if k not in kept}


@pytest.mark.parametrize(
    "key", ["provider", "provider_options", "timeout", "output_schema", "allowed_domains", "max_steps"]
)
@pytest.mark.parametrize("tool", ["search", "research", "extract", "browse", "answer"])
def test_rule_5_a_denied_key_is_dropped(tool: str, key: str) -> None:
    merged = merge_with(RouteDecision(params={tool: {key: "x"}}), pinned=tool)
    assert merged.params == {}
    assert merged.notes.ignored[f"params.{tool}.{key}"] == MSG_PARAM_NOT_ALLOWED.format(tool=tool, key=key)


def test_rule_5_an_effort_must_fit_on_every_backend_in_the_order() -> None:
    budget = Budget(max_cost_usd=0.2)
    high = merge_with(RouteDecision(params={"research": {"effort": "high"}}), pinned="research", budget=budget)
    assert high.params == {}, "parallel's high is $0.1, but nimble's, later in the order, is $0.5"
    merged = merge_with(RouteDecision(params={"research": {"effort": "medium"}}), pinned="research", budget=budget)
    assert merged.params == {"effort": "medium"}, "parallel's and nimble's medium are within $0.2"


NO_UNDECLARED = {"search:exa": "no depth", "search:brave": "no depth"}
"""Leaves tavily and nimble, which both take ``search_depth``."""


def test_rule_5_an_option_is_kept_only_when_every_backend_in_the_order_takes_it() -> None:
    depth = {"search": {"search_depth": "deep"}}
    merged = merge_with(RouteDecision(params=depth), pinned="search")
    assert merged.params == {}
    assert merged.notes.ignored["params.search.search_depth"] == "search:brave does not take search_depth"
    kept = merge_with(RouteDecision(params=depth, exclude=NO_UNDECLARED), pinned="search")
    assert kept.providers == ("tavily", "nimble") and kept.params == {"search_depth": "deep"}


def test_rule_5_an_unknown_price_never_fits_a_cost_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    from web_router import prices

    monkeypatch.setattr(prices, "estimate", lambda *_: None)
    budget = Budget(max_cost_usd=100.0)
    decision = RouteDecision(params={"search": {"search_depth": "lite"}}, exclude=NO_UNDECLARED)
    merged = merge_with(decision, pinned="search", budget=budget)
    assert merged.params == {} and "no known price" in merged.notes.ignored["params.search.search_depth"]


def test_rule_5_a_router_never_changes_an_option_the_caller_set() -> None:
    merged = merge_with(
        RouteDecision(params={"research": {"effort": "high"}}), pinned="research", caller_set={"effort"}
    )
    assert merged.params == {} and "caller" in merged.notes.ignored["params.research.effort"]


@pytest.mark.parametrize(
    ("tool", "key", "value", "url_check", "kept"),
    [
        ("extract", "urls", ["https://a.example"], None, False),
        ("extract", "urls", ["https://a.example"], lambda u: None, True),
        ("extract", "urls", ["https://a.example", "http://10.0.0.1"], _private, False),
        ("extract", "urls", "https://a.example", lambda u: None, False),
        ("browse", "start_url", "https://a.example", lambda u: None, True),
        ("browse", "start_url", "https://a.example", lambda u: "private", False),
    ],
)
def test_rule_5_a_url_passes_only_through_the_url_policy(
    tool: str, key: str, value: Any, url_check: Any, kept: bool
) -> None:
    merged = merge_with(RouteDecision(params={tool: {key: value}}), pinned=tool, url_check=url_check)
    assert (key in merged.params) is kept


@pytest.mark.parametrize(
    ("tool", "key", "value"),
    [
        ("research", "max_results", 3),
        ("research", "urls", ["https://a.example"]),
        ("search", "effort", "low"),
        ("answer", "search_depth", "deep"),
        ("extract", "start_url", "https://a.example"),
        ("browse", "urls", ["https://a.example"]),
    ],
)
def test_rule_5_a_key_another_method_takes_is_dropped(tool: str, key: str, value: Any) -> None:
    merged = merge_with(RouteDecision(params={tool: {key: value}}), pinned=tool, url_check=lambda u: None)
    assert merged.params == {} and "may not set" in merged.notes.ignored[f"params.{tool}.{key}"]


def test_a_backend_that_cannot_be_looked_up_is_not_said_to_refuse_the_option() -> None:
    request = RouteRequest(
        prompt="q",
        candidates=(Candidate(tool="search", provider="tavily"), Candidate(tool="search", provider="no-such")),
        deadline_s=1.0,
        pinned_tool="search",
    )
    merged = merge(request, RouteDecision(params={"search": {"search_depth": "deep"}}), {})
    why = merged.notes.ignored["params.search.search_depth"]
    assert merged.params == {} and why.startswith("search:no-such could not be checked for search_depth")


@pytest.mark.parametrize(
    ("latency", "budget", "kept"),
    [
        ((1.0, 30.0), Budget(time_limit_s=10.0), False),
        ((1.0, 5.0), Budget(time_limit_s=10.0), True),
        (None, Budget(time_limit_s=10.0), True),
        (None, Budget(time_limit_s=10.0, strict=True), False),
    ],
)
def test_rule_5_a_search_depth_must_fit_the_time_cap_too(
    monkeypatch: pytest.MonkeyPatch, latency: tuple[float, float] | None, budget: Budget, kept: bool
) -> None:
    from web_router import prices

    monkeypatch.setattr(prices, "estimate_latency", lambda *_: latency)
    decision = RouteDecision(params={"search": {"search_depth": "deep"}}, exclude=NO_UNDECLARED)
    merged = merge_with(decision, pinned="search", budget=budget)
    assert ("search_depth" in merged.params) is kept
