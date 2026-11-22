"""Edge cases of routing on vendor modes: exclusions, the static tail, the cost order, the ledger and mode pricing.

Offline: the fake Exa and Tavily of :mod:`tests.attempts_helpers` answer; nothing is sent.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.attempts_helpers import INSTANT, TAVILY, fake_backends, routed
from tests.candidates_helpers import clear_keys, set_keys
from web_router import AllProvidersFailedError, Budget, Candidate, RouteDecision, RouteRequest
from web_router.providers._pinning import pinned_request
from web_router.registry import get_provider_class
from web_router.routing.attempts import Attempt, admits, refunded
from web_router.routing.budget import budget_scope
from web_router.routing.candidates import CandidateSet
from web_router.routing.decide import _base_order
from web_router.routing.merge import merge
from web_router.routing.url_policy import any_web_url
from web_router.types import SearchRequest

EXA, EXA_INSTANT = (
    Candidate(tool="search", provider="exa"),
    Candidate(tool="search", provider="exa", mode="exa-instant"),
)
OTHERS = (Candidate(tool="search", provider="tavily"), Candidate(tool="search", provider="brave"))
STATIC = {"search": ("brave", "exa", "tavily")}


def _merged(candidates: tuple[Candidate, ...], decision: RouteDecision, budget: Budget | None = None) -> object:
    req = RouteRequest(prompt="q", candidates=candidates, deadline_s=1.0, budget=budget)
    return merge(req, decision, STATIC, url_check=any_web_url)


def test_a_backend_wide_exclusion_holds_when_only_its_modes_are_offered() -> None:
    merged = _merged((EXA_INSTANT, *OTHERS), RouteDecision(attempts=[INSTANT, TAVILY], exclude={"search:exa": "slow"}))
    assert merged.providers == ("tavily", "brave")  # type: ignore[attr-defined]
    assert "search:exa" not in merged.notes.ignored  # type: ignore[attr-defined]


def test_a_backend_named_in_a_mode_is_not_appended_again_at_its_default_mode() -> None:
    merged = _merged((EXA, EXA_INSTANT, *OTHERS), RouteDecision(attempts=[INSTANT]))
    assert merged.providers == ("exa:exa-instant", "brave", "tavily")  # type: ignore[attr-defined]
    both = _merged((EXA, EXA_INSTANT, *OTHERS), RouteDecision(attempts=[INSTANT, "search:exa"]))
    assert both.providers == ("exa:exa-instant", "exa", "brave", "tavily"), "named bare too: it runs bare"  # type: ignore[attr-defined]


def test_a_backend_whose_only_named_mode_is_excluded_does_not_run_bare() -> None:
    merged = _merged((EXA, EXA_INSTANT, *OTHERS), RouteDecision(attempts=[INSTANT], exclude={INSTANT: "too thin"}))
    assert merged.providers == ("brave", "tavily")  # type: ignore[attr-defined]


def test_an_excluded_sole_mode_of_the_only_backend_still_runs_in_that_mode() -> None:
    merged = _merged((EXA, EXA_INSTANT), RouteDecision(attempts=[INSTANT], exclude={INSTANT: "too thin"}))
    assert merged.providers == ("exa:exa-instant",), "rule 3 ignores the exclusion; a mode-only backend is not run bare"  # type: ignore[attr-defined]


def test_lifting_an_excluded_sole_mode_keeps_every_other_exclusion() -> None:
    exclude = {INSTANT: "too thin", "search:tavily": "slow", "search:brave": "down"}
    merged = _merged((EXA, EXA_INSTANT, *OTHERS), RouteDecision(attempts=[INSTANT], exclude=exclude))
    assert merged.providers == ("exa:exa-instant",)  # type: ignore[attr-defined]
    assert merged.notes.excluded == {"search:tavily": "slow", "search:brave": "down"}  # type: ignore[attr-defined]


def test_an_excluded_sole_mode_runs_when_only_a_sibling_mode_is_left() -> None:
    fast = Candidate(tool="search", provider="exa", mode="exa-fast")
    merged = _merged((EXA_INSTANT, fast), RouteDecision(attempts=[INSTANT], exclude={INSTANT: "too thin"}))
    assert merged.providers == ("exa:exa-instant",)  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("candidates", "exclude"),
    [
        ((EXA, EXA_INSTANT, *OTHERS), {INSTANT: " "}),
        ((EXA, EXA_INSTANT, *OTHERS), {"search:exa": "no", "search:tavily": "no", "search:brave": "no"}),
        ((EXA, EXA_INSTANT), {INSTANT: "too thin"}),
        ((EXA, EXA_INSTANT, *OTHERS), {INSTANT: "too thin"}),
        ((EXA, EXA_INSTANT, *OTHERS), {INSTANT: "too thin", "search:tavily": "no", "search:brave": "no"}),
    ],
    ids=["blank-reason", "would-empty", "sole-mode-of-only-backend", "excluded-mode", "sole-mode-and-others"],
)
def test_the_cost_order_ranks_a_method_exactly_when_the_merge_keeps_an_attempt(
    candidates: tuple[Candidate, ...], exclude: dict[str, str]
) -> None:
    decision = RouteDecision(attempts=[INSTANT], exclude=exclude)
    found = CandidateSet(candidates=candidates, cost_order={"search": ["by-cost"]})  # marks where it applies
    kept = "exa:exa-instant" in _merged(candidates, decision).providers  # type: ignore[attr-defined]
    assert (_base_order(found, decision)["search"] != ["by-cost"]) is kept


def test_an_empty_answer_in_a_mode_moves_on_without_calling_the_backend_again(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = fake_backends(monkeypatch, {"results": []})
    with routed(INSTANT) as wr:
        response = wr.search("q")
    assert (calls.exa, response.provider) == (["instant"], "tavily")


@pytest.mark.parametrize("excluded", ["search:exa", INSTANT], ids=["backend", "mode"])
def test_an_excluded_attempt_is_no_ranking_so_the_cost_order_applies(excluded: str) -> None:
    found = CandidateSet(candidates=(EXA, EXA_INSTANT, *OTHERS), cost_order={"search": ["tavily", "exa", "brave"]})
    decision = RouteDecision(attempts=[INSTANT], exclude={excluded: "no"})
    assert _base_order(found, decision)["search"] == ["tavily", "exa", "brave"]
    assert _base_order(found, RouteDecision(attempts=[INSTANT]))["search"] == ["exa", "tavily", "brave"], "control"


@pytest.mark.parametrize("exclude", [{INSTANT: " "}, {"search:exa": "no", "search:tavily": "no", "search:brave": "no"}])
def test_an_exclusion_the_merge_ignores_does_not_unrank_an_attempt(exclude: dict[str, str]) -> None:
    found = CandidateSet(candidates=(EXA, EXA_INSTANT, *OTHERS), cost_order={"search": ["tavily", "exa", "brave"]})
    assert _base_order(found, RouteDecision(attempts=[INSTANT], exclude=exclude))["search"] == [
        "exa",
        "tavily",
        "brave",
    ]


def test_a_refused_mode_under_a_cap_is_not_reported_as_a_budget_skip(monkeypatch: pytest.MonkeyPatch) -> None:
    set_keys(clear_keys(monkeypatch), "exa")
    exa = get_provider_class("exa")()
    request = SearchRequest(query="q")
    default = Attempt(tool="search", provider="exa", request=request)
    instant = Attempt(
        tool="search", provider="exa", request=pinned_request(exa, request, "exa-instant"), mode="exa-instant"
    )
    refused = AllProvidersFailedError("auto", "exa:exa-instant: HTTP 422", {})
    with (
        pytest.raises(AllProvidersFailedError),
        budget_scope(Budget(max_cost_usd=0.015), "search", SimpleNamespace(name="auto"), request),
    ):
        assert not admits(default), "the default mode is over the cap"
        assert admits(instant)
        refunded(instant)  # sent, and refused: the call ends with its own error, not the budget's
        raise refused


def test_a_depth_is_not_priced_on_a_backend_that_runs_only_in_modes() -> None:
    decision = RouteDecision(attempts=[INSTANT], params={"search": {"search_depth": "deep"}})
    merged = _merged((EXA_INSTANT,), decision, Budget(max_cost_usd=0.015))
    assert merged.params == {"search_depth": "deep"}  # type: ignore[attr-defined]
    bare = _merged((EXA,), RouteDecision(attempts=["search:exa"], params=decision.params), Budget(max_cost_usd=0.015))
    assert "params.search.search_depth" in bare.notes.ignored, "control: the default mode is priced"  # type: ignore[attr-defined]
