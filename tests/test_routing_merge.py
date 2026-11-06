"""The merge rule: a router's decision and the static order, made into one method, one order and checked options.

One table per rule. Prices come from the shipped price list: research on
nimble is $0.025 at low and $0.5 at high, parallel's is $0.1 at high.
"""

from __future__ import annotations

from typing import Any

import pytest

from web_router import Budget, Candidate, DecisionRecord, RouteDecision, RouteRequest
from web_router.routing.merge import Merged, merge

PAIRS = (
    ("search", "exa"),
    ("search", "brave"),
    ("search", "tavily"),
    ("search", "nimble"),
    ("research", "nimble"),
    ("research", "parallel"),
    ("answer", "perplexity"),
    ("extract", "jina"),
    ("browse", "skyvern"),
)
STATIC = {
    "search": ("tavily", "brave", "exa", "nimble"),
    "research": ("parallel", "nimble"),
    "answer": ("perplexity",),
    "extract": ("jina",),
    "browse": ("skyvern",),
}


def _request(pinned: Any = None, budget: Budget | None = None, urls: tuple[str, ...] = (), **_: Any) -> RouteRequest:
    candidates = tuple(Candidate(tool=t, provider=p) for t, p in PAIRS)  # type: ignore[arg-type]
    return RouteRequest(prompt="q", candidates=candidates, deadline_s=1.0, urls=urls, budget=budget, pinned_tool=pinned)


def _private(url: str) -> str | None:
    return "a private address" if "//10." in url else None


def _merge(decision: object, **kwargs: Any) -> Merged:
    options = {k: kwargs.pop(k) for k in ("failure", "caller_set", "url_check") if k in kwargs}
    return merge(_request(**kwargs), decision, STATIC, **options)


@pytest.mark.parametrize(
    ("decision", "pinned", "tool"),
    [
        (RouteDecision(tools=["research", "search"]), None, "research"),
        (RouteDecision(tools=["research"]), "answer", "answer"),
        (RouteDecision(tools=["made-up", "answer"]), None, "answer"),
        (RouteDecision(tools=["answer"], exclude={"answer:perplexity": "down"}), None, "search"),
        (RouteDecision(tools=["answer"], exclude={"answer:perplexity": "down"}, providers={}), "answer", "answer"),
        (RouteDecision(), None, "search"),
        (RouteDecision(tools=[]), None, "search"),
    ],
)
def test_rule_1_the_tool_is_pinned_else_the_routers_first_with_a_candidate_else_search(
    decision: RouteDecision, pinned: Any, tool: str
) -> None:
    assert _merge(decision, pinned=pinned).tool == tool


@pytest.mark.parametrize(
    ("ranked", "expected"),
    [
        (None, ("tavily", "brave", "exa", "nimble")),
        (["exa"], ("exa", "tavily", "brave", "nimble")),
        (["nimble", "exa"], ("nimble", "exa", "tavily", "brave")),
        (["exa", "exa", "brave"], ("exa", "brave", "tavily", "nimble")),
    ],
)
def test_rule_2_the_routers_ranking_comes_first_then_the_static_order(
    ranked: list[str] | None, expected: tuple[str, ...]
) -> None:
    providers = None if ranked is None else {"search": ranked}
    merged = _merge(RouteDecision(providers=providers), pinned="search")
    assert merged.providers == expected
    assert merged.notes.merged == tuple(f"search:{p}" for p in expected)
    assert merged.notes.ranked == tuple(f"search:{p}" for p in dict.fromkeys(ranked or ()))


def test_rule_2_a_candidate_the_static_order_leaves_out_still_comes_last() -> None:
    merged = merge(_request(pinned="search"), RouteDecision(), {"search": ("brave",)})
    assert merged.providers == ("brave", "exa", "tavily", "nimble"), "then in candidate order"


@pytest.mark.parametrize(
    ("exclude", "providers", "excluded", "ignored"),
    [
        ({"search:tavily": "slow"}, ("brave", "exa", "nimble"), {"search:tavily": "slow"}, set()),
        ({"search:tavily": "slow", "research:nimble": "dear"}, ("brave", "exa", "nimble"), None, set()),
        (
            {f"search:{p}": "no" for p in ("exa", "brave", "tavily", "nimble")},
            ("tavily", "brave", "exa", "nimble"),
            {},
            {"exclude"},
        ),
        ({"search:made-up": "x"}, ("tavily", "brave", "exa", "nimble"), {}, {"search:made-up"}),
    ],
)
def test_rule_3_only_exclude_removes_a_candidate_and_never_the_last_one(
    exclude: dict[str, str], providers: tuple[str, ...], excluded: dict[str, str] | None, ignored: set[str]
) -> None:
    merged = _merge(RouteDecision(exclude=exclude), pinned="search")
    assert merged.providers == providers
    assert dict(merged.notes.excluded) == (exclude if excluded is None else excluded)
    assert set(merged.notes.ignored) == ignored


def test_rule_3_a_ranked_backend_that_is_also_excluded_stays_out() -> None:
    merged = _merge(RouteDecision(providers={"search": ["exa"]}, exclude={"search:exa": "down"}), pinned="search")
    assert "exa" not in merged.providers and merged.notes.excluded == {"search:exa": "down"}


@pytest.mark.parametrize(
    ("decision", "ignored"),
    [
        (RouteDecision(providers={"search": ["made-up", "exa"]}), {"search:made-up"}),
        (RouteDecision(tools=["telepathy"]), {"telepathy"}),
        (RouteDecision(exclude={"search:made-up": "why"}), {"search:made-up"}),
    ],
)
def test_rule_4_an_unknown_id_is_set_aside_and_noted_not_an_error(decision: RouteDecision, ignored: set[str]) -> None:
    merged = _merge(decision)
    assert set(merged.notes.ignored) == ignored and merged.notes.fallback is None and merged.tool == "search"
    assert set(merged.providers) == {"exa", "brave", "tavily", "nimble"}


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
    merged = _merge(RouteDecision(params={tool: params}), pinned=tool, budget=budget)
    assert dict(merged.params) == kept
    assert set(merged.notes.ignored) == {f"params.{tool}.{k}" for k in params if k not in kept}


@pytest.mark.parametrize(
    "key", ["provider", "provider_options", "timeout", "output_schema", "allowed_domains", "max_steps"]
)
@pytest.mark.parametrize("tool", ["search", "research", "extract", "browse", "answer"])
def test_rule_5_a_denied_key_is_dropped(tool: str, key: str) -> None:
    merged = _merge(RouteDecision(params={tool: {key: "x"}}), pinned=tool)
    assert merged.params == {} and f"params.{tool}.{key}" in merged.notes.ignored


def test_rule_5_an_effort_must_fit_on_every_backend_in_the_order() -> None:
    budget = Budget(max_cost_usd=0.2)
    high = _merge(RouteDecision(params={"research": {"effort": "high"}}), pinned="research", budget=budget)
    assert high.params == {}, "parallel's high is $0.1, but nimble's, later in the order, is $0.5"
    merged = _merge(RouteDecision(params={"research": {"effort": "medium"}}), pinned="research", budget=budget)
    assert merged.params == {"effort": "medium"}, "parallel's and nimble's medium are within $0.2"


NO_UNDECLARED = {"search:exa": "no depth", "search:brave": "no depth"}
"""Leaves tavily and nimble, which both take ``search_depth``."""


def test_rule_5_an_option_is_kept_only_when_every_backend_in_the_order_takes_it() -> None:
    depth = {"search": {"search_depth": "deep"}}
    merged = _merge(RouteDecision(params=depth), pinned="search")
    assert merged.params == {}
    assert merged.notes.ignored["params.search.search_depth"] == "search:brave does not take search_depth"
    kept = _merge(RouteDecision(params=depth, exclude=NO_UNDECLARED), pinned="search")
    assert kept.providers == ("tavily", "nimble") and kept.params == {"search_depth": "deep"}


def test_rule_5_an_unknown_price_never_fits_a_cost_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    from web_router import prices

    monkeypatch.setattr(prices, "price", lambda *_: None)
    budget = Budget(max_cost_usd=100.0)
    decision = RouteDecision(params={"search": {"search_depth": "lite"}}, exclude=NO_UNDECLARED)
    merged = _merge(decision, pinned="search", budget=budget)
    assert merged.params == {} and "no known price" in merged.notes.ignored["params.search.search_depth"]


def test_rule_5_a_depth_priced_only_as_a_floor_never_fits_a_cost_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from web_router import prices

    floor = SimpleNamespace(estimate_usd=0.001, estimate_is_floor=True, mode="lite")
    monkeypatch.setattr(prices, "price", lambda *_: floor)
    decision = RouteDecision(params={"search": {"search_depth": "lite"}}, exclude=NO_UNDECLARED)
    merged = _merge(decision, pinned="search", budget=Budget(max_cost_usd=100.0))
    assert merged.params == {} and "no known price" in merged.notes.ignored["params.search.search_depth"]


def test_rule_5_a_router_never_changes_an_option_the_caller_set() -> None:
    merged = _merge(RouteDecision(params={"research": {"effort": "high"}}), pinned="research", caller_set={"effort"})
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
    merged = _merge(RouteDecision(params={tool: {key: value}}), pinned=tool, url_check=url_check)
    assert (key in merged.params) is kept


@pytest.mark.parametrize(
    ("decision", "failure", "reason"),
    [
        (None, None, "router_invalid"),
        ("search:exa", None, "router_invalid"),
        (RouteDecision(tools="research"), None, "router_invalid"),  # type: ignore[arg-type]
        (RouteDecision(providers={"search": "exa"}), None, "router_invalid"),  # type: ignore[dict-item]
        (RouteDecision(exclude={"search:exa": 1}), None, "router_invalid"),  # type: ignore[dict-item]
        (RouteDecision(params={"search": 3}), None, "router_invalid"),  # type: ignore[dict-item]
        (RouteDecision(providers={"telepathy": ["exa"]}), None, "router_invalid"),  # type: ignore[dict-item]
        (RouteDecision(providers={1: ["exa"]}), None, "router_invalid"),  # type: ignore[dict-item]
        (RouteDecision(params={"telepathy": {"x": 1}}), None, "router_invalid"),  # type: ignore[dict-item]
        (RouteDecision(params={"search": {1: 3}}), None, "router_invalid"),  # type: ignore[dict-item]
        (RouteDecision(reason=["why"]), None, "router_invalid"),  # type: ignore[arg-type]
        (RouteDecision(confidence="high"), None, "router_invalid"),  # type: ignore[arg-type]
        (RouteDecision(providers={"search": ["exa"]}), "router_timeout", "router_timeout"),
        (RouteDecision(providers={"search": ["exa"]}), "router_error", "router_error"),
    ],
)
def test_rule_6_no_usable_decision_gets_the_static_order_with_the_reason(
    decision: object, failure: Any, reason: str
) -> None:
    merged = _merge(decision, pinned="search", failure=failure)
    assert merged.providers == STATIC["search"] and merged.params == {}
    assert merged.notes.fallback == reason and merged.record()["reason"] == reason
    assert merged.record()["router_timeout"] is (True if reason == "router_timeout" else None)
    assert _merge(decision, failure=failure).tool == "search", "unpinned, the last resort"


@pytest.mark.parametrize(
    ("urls", "params", "url_check", "tool"),
    [
        ((), None, None, "search"),
        (("https://a.example",), None, None, "extract"),
        ((), {"extract": {"urls": ["https://a.example"]}}, lambda u: None, "extract"),
        ((), {"extract": {"urls": ["https://a.example"]}}, None, "search"),
    ],
)
def test_rule_7_extract_with_no_url_searches_instead(
    urls: tuple[str, ...], params: Any, url_check: Any, tool: str
) -> None:
    merged = _merge(RouteDecision(tools=["extract"], params=params), urls=urls, url_check=url_check)
    assert merged.tool == tool
    assert ("extract" in merged.notes.ignored) is (tool == "search")


def test_rule_7_never_moves_a_pinned_extract() -> None:
    assert _merge(RouteDecision(tools=["search"]), pinned="extract").tool == "extract"


def test_the_record_fills_the_decision_records_fields() -> None:
    merged = _merge(
        RouteDecision(
            providers={"research": ["nimble"]},
            exclude={"research:parallel": "slow"},
            params={"research": {"effort": "low"}},
            reason="cheap first",
        ),
        pinned="research",
    )
    record = DecisionRecord(**merged.record())
    assert record.ranked == ["research:nimble"] and record.merged == ["research:nimble"]
    assert record.excluded == {"research:parallel": "slow"} and record.reason == "cheap first"
    assert record.params == {"effort": "low"} and record.params_source == "router"
    assert _merge(RouteDecision(), pinned="search").record()["params_source"] is None


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
    merged = _merge(RouteDecision(params={tool: {key: value}}), pinned=tool, url_check=lambda u: None)
    assert merged.params == {} and "may not set" in merged.notes.ignored[f"params.{tool}.{key}"]


@pytest.mark.parametrize(
    "decision",
    [RouteDecision(), RouteDecision(reason="no opinion"), RouteDecision(tools=[], providers={"search": []})],
)
def test_an_empty_decision_gets_the_static_order_recorded_as_router_empty_not_a_failure(
    decision: RouteDecision,
) -> None:
    merged = _merge(decision, pinned="search")
    assert merged.providers == STATIC["search"] and merged.notes.fallback is None and merged.notes.empty
    assert DecisionRecord(**merged.record()).reason == "router_empty"
    assert not _merge(RouteDecision(providers={"search": ["exa"]}), pinned="search").notes.empty


TOKEN_URL = "https://user:pw@a.example/page?session=s3cr3t-token#frag"


def test_a_url_is_kept_to_call_with_but_never_shown_whole() -> None:
    merged = _merge(RouteDecision(params={"browse": {"start_url": TOKEN_URL}}), pinned="browse", url_check=_none)
    assert merged.params == {"start_url": TOKEN_URL}, "the call gets the URL whole"
    assert merged.record()["params"] == {"start_url": "https://a.example"}
    extract = _merge(RouteDecision(params={"extract": {"urls": [TOKEN_URL]}}), pinned="extract", url_check=_none)
    assert extract.record()["params"] == {"urls": ["https://a.example"]}
    refused = _merge(RouteDecision(params={"extract": {"urls": [TOKEN_URL]}}), pinned="extract", url_check=_refuse)
    invalid = _merge(RouteDecision(params={"browse": {"start_url": [TOKEN_URL]}}), pinned="browse", url_check=_none)
    for shown in (repr(merged), repr(extract), repr(refused), repr(invalid), str(DecisionRecord(**merged.record()))):
        assert "s3cr3t" not in shown and "pw@" not in shown and "/page" not in shown
    assert "a.example" in refused.notes.ignored["params.extract.urls"]


def test_rule_7_a_url_the_caller_set_does_not_let_the_router_choose_extract() -> None:
    params = {"extract": {"urls": ["https://a.example"]}}
    merged = _merge(RouteDecision(tools=["extract"], params=params), url_check=_none, caller_set={"urls"})
    assert merged.tool == "search" and "extract" in merged.notes.ignored


def _none(url: str) -> None:
    return None


def _refuse(url: str) -> str:
    return "not allowed"
