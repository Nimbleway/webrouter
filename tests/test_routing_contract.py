"""The router contract's types: frozen, keyword-only, every decision field optional."""

from __future__ import annotations

import dataclasses
from typing import get_args

import pytest

import web_router
from web_router import Budget, Candidate, DecisionRecord, Plan, RouteDecision, RouteRequest, ToolName
from web_router.routing import attempts
from web_router.routing.attempts import Attempt, CallRoute, admitted, call_route, in_call_order
from web_router.types import SearchRequest


def test_the_tools_are_the_five_routable_methods() -> None:
    assert set(get_args(ToolName)) == {"search", "extract", "answer", "research", "browse"}


def test_the_contract_is_exported_from_the_package_root() -> None:
    for name in ("Budget", "Candidate", "Plan", "RouteDecision", "RouteRequest", "ToolName"):
        assert name in web_router.__all__
        assert getattr(web_router, name) is getattr(web_router.routing, name)


def test_budget_has_its_final_shape_and_caps_nothing_by_default() -> None:
    assert [f.name for f in dataclasses.fields(Budget)] == ["max_cost_usd", "time_limit_s", "strict"]
    assert Budget() == Budget(max_cost_usd=None, time_limit_s=None, strict=False)


@pytest.mark.parametrize("cls", [Budget, Candidate, RouteRequest, RouteDecision, Plan])
def test_every_type_is_frozen_and_keyword_only(cls: type) -> None:
    params = cls.__dataclass_params__  # type: ignore[attr-defined]
    assert params.frozen
    assert all(f.kw_only for f in dataclasses.fields(cls))


def test_a_candidate_is_named_by_its_tool_and_provider() -> None:
    candidate = Candidate(tool="search", provider="exa", est_cost_usd=0.005, est_latency_s=1.2)
    assert candidate.id == "search:exa"
    assert Candidate(tool="search", provider="exa", id="search:exa").id == "search:exa"
    assert Candidate(tool="answer", provider="you").est_cost_usd is None


def test_a_candidate_id_that_disagrees_with_its_fields_is_refused() -> None:
    with pytest.raises(ValueError, match="extract:exa"):
        Candidate(tool="extract", provider="exa", id="search:exa")


def test_a_route_request_needs_only_its_prompt_candidates_and_deadline() -> None:
    candidates = (Candidate(tool="extract", provider="jina"), Candidate(tool="extract", provider="firecrawl"))
    request = RouteRequest(prompt="https://a.test", candidates=candidates, deadline_s=1.0)
    assert (request.urls, request.budget, request.pinned_tool) == ((), None, None)
    full = RouteRequest(
        prompt="https://a.test",
        urls=("https://a.test",),
        candidates=candidates,
        budget=Budget(time_limit_s=10),
        pinned_tool="extract",
        deadline_s=1.0,
    )
    assert full.budget == Budget(time_limit_s=10)


def test_a_decision_with_no_fields_has_no_opinion() -> None:
    decision = RouteDecision()
    assert (decision.tools, decision.providers, decision.params, decision.confidence, decision.reason) == (
        None,
        None,
        None,
        None,
        None,
    )
    assert decision.exclude == {}
    full = RouteDecision(
        tools=["extract", "search"],
        providers={"extract": ["jina", "firecrawl"]},
        exclude={"search:exa": "too slow"},
        params={"research": {"effort": "low"}},
        confidence=0.8,
        reason="the prompt is a URL",
    )
    assert full.providers == {"extract": ["jina", "firecrawl"]}


def test_a_plan_carries_the_same_record_as_a_response() -> None:
    plan = Plan(prompt="q", tool="search")
    assert plan.decision == DecisionRecord()
    assert (plan.params, plan.providers) == ({}, {})
    routed = Plan(prompt="q", tool="answer", providers={"answer": ["perplexity"]}, decision=DecisionRecord(ran=0))
    assert routed.decision.ran == 0


_CHAIN = [(0, "tavily"), (1, "exa"), (2, "tavily"), (3, "jina")]


def test_with_no_route_the_order_is_the_chain_and_every_member_is_admitted() -> None:
    assert in_call_order("search", list(_CHAIN)) == _CHAIN
    assert admitted("search", "exa", SearchRequest(query="q"))


def test_a_call_order_reorders_drops_what_it_leaves_out_and_keeps_shared_names_in_chain_order() -> None:
    with call_route(CallRoute(order={"search": ["jina", "tavily"]})):
        assert in_call_order("search", list(_CHAIN)) == [(3, "jina"), (0, "tavily"), (2, "tavily")]
        assert in_call_order("extract", list(_CHAIN)) == _CHAIN
    assert in_call_order("search", list(_CHAIN)) == _CHAIN


def test_the_hook_sees_each_attempt_and_its_answer_admits_or_passes_over() -> None:
    seen: list[Attempt] = []
    request = SearchRequest(query="q")

    def hook(attempt: Attempt) -> bool:
        seen.append(attempt)
        return attempt.provider != "exa"

    with call_route(CallRoute(before_attempt=hook)):
        assert admitted("search", "tavily", request)
        assert not admitted("search", "exa", request)
    assert [(a.tool, a.provider, a.request) for a in seen] == [
        ("search", "tavily", request),
        ("search", "exa", request),
    ]
    assert attempts._ROUTE.get() is None


def test_a_provider_named_twice_in_the_order_keeps_its_first_place() -> None:
    from web_router.routing.attempts import CallRoute, call_route, in_call_order

    eligible = [(0, "first"), (1, "second")]
    with call_route(CallRoute(order={"search": ["second", "first", "second"]})):
        assert in_call_order("search", eligible) == [(1, "second"), (0, "first")]
