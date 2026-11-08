"""The merge rule: a router's decision and the static order, made into one method, one order and checked options.

One table per rule; rule 5, a router's options, is in ``test_routing_merge_params.py``.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.merge_helpers import STATIC, merge_with, request_for
from web_router import Candidate, DecisionRecord, RouteDecision, RouteRequest
from web_router.routing.merge import merge


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
    assert merge_with(decision, pinned=pinned).tool == tool


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
    merged = merge_with(RouteDecision(providers=providers), pinned="search")
    assert merged.providers == expected
    assert merged.notes.merged == tuple(f"search:{p}" for p in expected)
    assert merged.notes.ranked == tuple(f"search:{p}" for p in dict.fromkeys(ranked or ()))


def test_rule_2_a_candidate_the_static_order_leaves_out_still_comes_last() -> None:
    merged = merge(request_for(pinned="search"), RouteDecision(), {"search": ("brave",)})
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
    merged = merge_with(RouteDecision(exclude=exclude), pinned="search")
    assert merged.providers == providers
    assert dict(merged.notes.excluded) == (exclude if excluded is None else excluded)
    assert set(merged.notes.ignored) == ignored


def test_rule_3_a_ranked_backend_that_is_also_excluded_stays_out() -> None:
    merged = merge_with(RouteDecision(providers={"search": ["exa"]}, exclude={"search:exa": "down"}), pinned="search")
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
    merged = merge_with(decision)
    assert set(merged.notes.ignored) == ignored and merged.notes.fallback is None and merged.tool == "search"
    assert set(merged.providers) == {"exa", "brave", "tavily", "nimble"}


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
    merged = merge_with(decision, pinned="search", failure=failure)
    assert merged.providers == STATIC["search"] and merged.params == {}
    assert merged.notes.fallback == reason and merged.record()["reason"] == reason
    assert merged.record()["router_timeout"] is (True if reason == "router_timeout" else None)
    assert merge_with(decision, failure=failure).tool == "search", "unpinned, the last resort"


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
    merged = merge_with(RouteDecision(tools=["extract"], params=params), urls=urls, url_check=url_check)
    assert merged.tool == tool
    assert ("extract" in merged.notes.ignored) is (tool == "search")


def test_rule_7_never_moves_a_pinned_extract() -> None:
    assert merge_with(RouteDecision(tools=["search"]), pinned="extract").tool == "extract"


def test_the_record_fills_the_decision_records_fields() -> None:
    merged = merge_with(
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
    assert merge_with(RouteDecision(), pinned="search").record()["params_source"] is None


@pytest.mark.parametrize(
    "decision",
    [RouteDecision(), RouteDecision(reason="no opinion"), RouteDecision(tools=[], providers={"search": []})],
)
def test_an_empty_decision_gets_the_static_order_recorded_as_router_empty_not_a_failure(
    decision: RouteDecision,
) -> None:
    merged = merge_with(decision, pinned="search")
    assert merged.providers == STATIC["search"] and merged.notes.fallback is None and merged.notes.empty
    assert DecisionRecord(**merged.record()).reason == "router_empty"
    assert not merge_with(RouteDecision(providers={"search": ["exa"]}), pinned="search").notes.empty


@pytest.mark.parametrize("confidence", [-0.1, 1.5, float("nan"), float("inf"), True])
def test_a_confidence_outside_0_to_1_is_router_invalid(confidence: float) -> None:
    assert merge_with(RouteDecision(confidence=confidence), pinned="search").notes.fallback == "router_invalid"
    assert merge_with(RouteDecision(confidence=1), pinned="search").notes.fallback is None


def test_rule_1_a_tool_set_aside_for_want_of_a_candidate_is_noted() -> None:
    searches = tuple(Candidate(tool="search", provider=p) for p in ("exa", "brave"))  # the budget dropped extract's
    request = RouteRequest(prompt="read https://example.com/", candidates=searches, deadline_s=1.0, urls=("x",))
    merged = merge(request, RouteDecision(tools=["extract", "search"]), STATIC)
    assert merged.tool == "search" and "no candidate is left" in merged.notes.ignored["extract"]
    excluded = merge_with(RouteDecision(tools=["answer"], exclude={"answer:perplexity": "down"}))
    assert excluded.tool == "search" and "answer" in excluded.notes.ignored
    assert "research" not in merge_with(RouteDecision(tools=["research", "answer"])).notes.ignored, "chosen: no note"
