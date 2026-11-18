"""Edge cases of routing on vendor modes: exclusions, the static tail, the cost order, the ledger and mode pricing.

Offline: the fake Exa and Tavily of :mod:`tests.attempts_helpers` answer; nothing is sent.
"""

from __future__ import annotations

from tests.attempts_helpers import INSTANT, TAVILY
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
