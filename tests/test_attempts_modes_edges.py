"""Edge cases of routing on vendor modes: exclusions, the static tail, the cost order, the ledger and mode pricing.

Offline: the fake Exa and Tavily of :mod:`tests.attempts_helpers` answer; nothing is sent.
"""

from __future__ import annotations

import pytest

from tests.attempts_helpers import INSTANT, TAVILY, fake_backends, routed
from web_router import Budget, Candidate, RouteDecision, RouteRequest
from web_router.routing.merge import merge
from web_router.routing.url_policy import any_web_url

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


def test_an_empty_answer_in_a_mode_moves_on_without_calling_the_backend_again(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = fake_backends(monkeypatch, {"results": []})
    with routed(INSTANT) as wr:
        response = wr.search("q")
    assert (calls.exa, response.provider) == (["instant"], "tavily")
