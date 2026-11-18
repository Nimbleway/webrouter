"""The merge rule with a decision's ``attempts``: they replace ``tools`` and ``providers``, vendor modes included,
exclusions reach a backend's modes, and a cost cap keeps their order. Offline: nothing is sent.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.attempts_helpers import DEEP, INSTANT, TAVILY, fake_backends, routed
from web_router import Budget, Candidate, RouteDecision, RouteRequest
from web_router.routing.candidates import CandidateSet
from web_router.routing.composite import decides_nothing
from web_router.routing.decide import _base_order
from web_router.routing.merge import merge
from web_router.routing.merge_shape import well_formed
from web_router.routing.url_policy import any_web_url

CANDIDATES = (
    Candidate(tool="search", provider="exa"),
    Candidate(tool="search", provider="exa", mode="exa-instant"),
    Candidate(tool="search", provider="exa", mode="exa-deep"),
    Candidate(tool="search", provider="tavily"),
    Candidate(tool="search", provider="brave"),
    Candidate(tool="extract", provider="jina"),
)
STATIC = {"search": ("brave", "exa", "tavily"), "extract": ("jina",)}


def _merged(decision: RouteDecision, budget: Budget | None = None) -> Any:
    req = RouteRequest(prompt="q", candidates=CANDIDATES, deadline_s=1.0, budget=budget)
    return merge(req, decision, STATIC, url_check=any_web_url)


def test_the_attempts_rank_the_method_modes_included_and_the_other_backends_follow_in_static_order() -> None:
    merged = _merged(RouteDecision(attempts=[INSTANT, DEEP, TAVILY], tools=["extract"], providers={"search": ["exa"]}))
    assert merged.tool == "search", "attempts replace tools"
    assert merged.providers == ("exa:exa-instant", "exa:exa-deep", "tavily", "brave", "exa")
    assert merged.record()["merged"][:2] == [INSTANT, DEEP]
    assert not {INSTANT, DEEP} & set(merged.notes.ignored), "a mode attempts run is not noted as not run"


def test_the_first_attempt_picks_the_method_and_an_unknown_one_is_noted() -> None:
    urls = {"extract": {"urls": ["https://a.example.com/page"]}}
    merged = _merged(RouteDecision(attempts=["search:nope", "extract:jina", INSTANT], params=urls))
    assert (merged.tool, merged.providers) == ("extract", ("jina",))
    assert "search:nope" in merged.notes.ignored


def test_excluding_a_backend_excludes_every_mode_of_it() -> None:
    merged = _merged(RouteDecision(attempts=[INSTANT, DEEP, TAVILY], exclude={"search:exa": "too slow"}))
    assert merged.providers == ("tavily", "brave")


def test_excluding_a_mode_excludes_that_mode_only() -> None:
    merged = _merged(RouteDecision(attempts=[INSTANT, DEEP, TAVILY], exclude={DEEP: "too dear"}))
    assert merged.providers == ("exa:exa-instant", "tavily", "brave", "exa")


def test_a_methods_other_options_still_apply_with_attempts() -> None:
    urls = ["https://a.example.com/page"]
    merged = _merged(RouteDecision(attempts=["extract:jina"], params={"extract": {"urls": urls}}))
    assert (merged.tool, merged.params) == ("extract", {"urls": urls})
    merged = _merged(RouteDecision(attempts=[INSTANT], params={"search": {"max_results": 3}}))
    assert merged.params == {"max_results": 3}


def test_attempts_alone_decide_something_and_must_be_a_list_of_ids() -> None:
    assert not decides_nothing(RouteDecision(attempts=[INSTANT]))
    assert well_formed(RouteDecision(attempts=[INSTANT]))
    assert not well_formed(RouteDecision(attempts=INSTANT))  # a bare string is no ranking


def test_empty_attempts_are_unset_so_the_decisions_providers_still_rank() -> None:
    merged = _merged(RouteDecision(attempts=[], providers={"search": ["tavily"]}))
    assert merged.providers[0] == "tavily" and not merged.notes.empty


def test_attempts_count_as_a_ranking_so_the_cost_order_does_not_replace_the_static_order() -> None:
    found = CandidateSet(candidates=CANDIDATES, cost_order={"search": ["tavily", "exa", "brave"]})
    assert _base_order(found, RouteDecision(attempts=[INSTANT]))["search"] == ["exa", "tavily", "brave"]
    assert _base_order(found, RouteDecision())["search"] == ["tavily", "exa", "brave"], "ranked by none: cost order"
    dropped = RouteDecision(attempts=["search:exa:dropped-by-the-cap"])
    assert _base_order(found, dropped)["search"] == ["tavily", "exa", "brave"], "no attempt left: cost order"


def test_under_a_cost_cap_the_routers_attempts_keep_their_order(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = fake_backends(monkeypatch, {"results": []})
    with routed(DEEP, INSTANT, TAVILY) as wr:
        plan = wr.plan("q", tool="search", budget=Budget(max_cost_usd=1.0))
        assert plan.providers["search"][:3] == ("exa:exa-deep", "exa:exa-instant", "tavily")
        response = wr.run(plan, budget=Budget(max_cost_usd=1.0))
    assert (calls.exa, response.provider) == (["deep", "instant"], "exa")


def test_with_only_mode_candidates_the_attempts_give_a_runnable_order() -> None:
    modes = tuple(c for c in CANDIDATES if c.mode is not None)
    req = RouteRequest(prompt="q", candidates=modes, deadline_s=1.0)
    merged = merge(req, RouteDecision(attempts=[DEEP, INSTANT]), STATIC, url_check=any_web_url)
    assert (merged.tool, merged.providers) == ("search", ("exa:exa-deep", "exa:exa-instant"))
