"""Deciders read vendor modes: each mode's option says what it does, and a ranked mode becomes an attempt.

Offline: the deciders are fakes or answer on httpx's mock transport, and no key is real.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from tests.candidates_helpers import exa_tavily_fixture  # noqa: F401  (fixture)
from tests.decider_helpers import chat, serve, systemone
from web_router import Candidate, RouteDecision, RouteRequest, WebRouter
from web_router.constants_mode_parts import EXA_TIERS
from web_router.routing.decider_llm import llm_router
from web_router.routing.decider_question import decision_for, describe
from web_router.routing.decider_systemone import systemone_router
from web_router.routing.preferred import PreferredRouter

INSTANT, DEEP, TAVILY, EXA = "search:exa:exa-instant", "search:exa:exa-deep", "search:tavily", "search:exa"
CANDIDATES = (
    Candidate(tool="search", provider="exa", est_cost_usd=0.007),
    Candidate(tool="search", provider="exa", mode="exa-instant", est_cost_usd=0.005),
    Candidate(tool="search", provider="exa", mode="exa-deep", est_cost_usd=0.012),
    Candidate(tool="search", provider="tavily", est_cost_usd=0.008),
)
CHAIN = {"auto": {"chain": {"search": ["exa", "tavily"]}}}


def _request(candidates: tuple[Candidate, ...] = CANDIDATES) -> RouteRequest:
    return RouteRequest(prompt="q", candidates=candidates, deadline_s=1.0, pinned_tool="search")


def test_ranked_modes_become_attempts_in_the_deciders_order() -> None:
    decision = decision_for(_request(), [INSTANT, DEEP, TAVILY], None, None, "fixed")
    assert decision.attempts == [INSTANT, DEEP, TAVILY]
    assert decision.tools is None and decision.providers is None
    assert decision.reason == f"fixed: {INSTANT}"


def test_with_no_mode_ranked_the_decision_is_tools_and_providers_as_before() -> None:
    backends = (CANDIDATES[0], CANDIDATES[3])
    decision = decision_for(_request(backends), [TAVILY, EXA], {TAVILY: 0.6, EXA: 0.4}, 0.5, "fixed")
    expected = RouteDecision(
        tools=["search"], providers={"search": ["tavily", "exa"]}, confidence=0.5, reason="fixed: search:tavily, p=0.60"
    )
    assert decision == expected and decision.attempts is None


def test_attempts_rank_by_backend_score_times_the_modes_share() -> None:
    scores = {EXA: 0.6, TAVILY: 0.4, INSTANT: 0.7, DEEP: 0.3}
    decision = decision_for(_request(), [EXA, TAVILY, INSTANT, DEEP], scores, 0.9, "fixed")
    assert decision.attempts == [INSTANT, TAVILY, DEEP], "0.42, 0.40, 0.18: Exa's default runs in its modes only"
    assert decision.reason == f"fixed: {INSTANT}, p=0.42", "the product the attempts ranked on, not the raw 0.70"


def test_a_modes_share_is_of_its_backends_mode_scores() -> None:
    scores = {EXA: 0.5, TAVILY: 0.3, INSTANT: 0.15, DEEP: 0.05}  # one calibrated spread over all four
    decision = decision_for(_request(), [EXA, TAVILY, INSTANT, DEEP], scores, None, "fixed")
    assert decision.attempts == [INSTANT, TAVILY, DEEP], "0.375, 0.30, 0.125"


def test_a_backends_split_score_never_hands_the_call_to_a_method_ranked_below_it() -> None:
    answer = Candidate(tool="answer", provider="perplexity")
    scores = {EXA: 0.4, INSTANT: 0.5, DEEP: 0.5, "answer:perplexity": 0.3}
    ranked = [EXA, "answer:perplexity", INSTANT, DEEP]
    attempts = decision_for(_request((*CANDIDATES, answer)), ranked, scores, None, "fixed").attempts
    assert attempts == [INSTANT, DEEP, "answer:perplexity"], "search first: 0.2 each, under answer's 0.3"


@pytest.mark.parametrize(
    ("ranked", "scores"),
    [([INSTANT, TAVILY], {INSTANT: 0.9, TAVILY: 0.8}), ([TAVILY, INSTANT, EXA], {TAVILY: 0.9, EXA: 0.2})],
    ids=["mode-backend-unranked", "a-ranked-id-unscored"],
)
def test_scores_that_cannot_be_multiplied_keep_the_deciders_order(ranked: list[str], scores: dict[str, float]) -> None:
    assert decision_for(_request(), ranked, scores, None, "fixed").attempts == ranked


def test_each_modes_option_says_what_it_does_and_no_two_read_the_same() -> None:
    modes = [Candidate(tool="search", provider="exa", mode=mode, est_cost_usd=0.01) for mode in EXA_TIERS]
    lines = [describe(candidate) for candidate in modes]
    for mode, line in zip(EXA_TIERS, lines, strict=True):
        assert EXA_TIERS[mode].does and EXA_TIERS[mode].does in line and mode in line
    assert len(set(lines)) == len(lines)
    combined = describe(Candidate(tool="search", provider="exa", mode="exa-instant+lite"))
    assert EXA_TIERS["exa-instant"].does in combined and "lite depth" in combined
    assert describe(CANDIDATES[0]) == "search on exa, $0.007, unknown latency", "a backend reads as before"


def test_a_mode_of_a_backend_not_registered_reads_by_name() -> None:
    line = describe(Candidate(tool="search", provider="not-here", mode="not-here-x"))
    assert line == "search on not-here in mode not-here-x (not-here-x), unknown cost, unknown latency"


def test_preferred_shown_modes_answers_every_mode_at_its_own_price() -> None:
    decision = PreferredRouter("cheap").route(_request())
    assert decision.attempts == [INSTANT, EXA, TAVILY, DEEP] and decision.providers is None


def test_preferred_keeps_providers_when_the_method_it_runs_has_no_mode() -> None:
    req = RouteRequest(prompt="q", candidates=(*CANDIDATES, Candidate(tool="answer", provider="x")), deadline_s=1.0)
    assert PreferredRouter("cheap").route(req).attempts == [INSTANT, EXA, TAVILY, DEEP], (
        "unpinned, the method is search"
    )
    pinned = RouteRequest(prompt="q", candidates=req.candidates, deadline_s=1.0, pinned_tool="answer")
    assert PreferredRouter("cheap").route(pinned).providers == {"search": ["exa", "tavily"], "answer": ["x"]}


def _llm(monkeypatch: pytest.MonkeyPatch) -> Any:
    def handler(req: httpx.Request) -> httpx.Response:
        offered = req.content.decode()
        assert EXA_TIERS["exa-instant"].does in offered, "the model reads what each mode does"
        return chat({"ranked": [INSTANT, TAVILY], "scores": [], "confidence": 0.5})(req)

    serve(monkeypatch, handler)
    return llm_router(base_url="http://127.0.0.1:8000/v1", model="m")


def _systemone(monkeypatch: pytest.MonkeyPatch) -> Any:
    serve(monkeypatch, systemone(INSTANT))
    return systemone_router(base_url="http://localhost:9000", model="m")


@pytest.mark.parametrize(
    "build", [_llm, _systemone, lambda _: PreferredRouter("cheap")], ids=["llm", "systemone", "preferred"]
)
def test_a_callers_route_modes_shows_a_built_in_modes_and_its_plan_runs_them(keys: Any, build: Any) -> None:
    with WebRouter(provider_config=CHAIN, router=build(keys), route_modes=True) as wr:
        plan = wr.plan("q", tool="search")
    order = plan.providers["search"]
    assert "exa:exa-instant" in order and "exa" not in order[: order.index("exa:exa-instant")], "run in its mode first"


def test_the_default_router_shown_modes_still_picks_the_method_only(keys: Any) -> None:
    with WebRouter(provider_config=CHAIN, router="data-driven", route_modes=True) as wr:
        plan = wr.plan("q")
    assert plan.tool == "search" and plan.providers["search"][:2] == ("exa", "tavily")
