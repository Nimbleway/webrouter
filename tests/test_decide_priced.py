"""A router's option is priced on the request the call would run: the candidates' own, the caller's options on it."""

from __future__ import annotations

import pytest

from tests.candidates_helpers import clear_keys, set_keys
from web_router import Budget, RouteDecision, RouteRequest, WebRouter
from web_router.routing.candidates import build_candidates
from web_router.routing.decide import decided
from web_router.routing.invoke import RouterOutcome
from web_router.types import SearchRequest


def test_a_routers_max_results_is_priced_on_the_callers_search_depth(monkeypatch: pytest.MonkeyPatch) -> None:
    set_keys(clear_keys(monkeypatch), "nimble")
    budget = Budget(max_cost_usd=1.0)
    found = build_candidates(WebRouter(), {"search": SearchRequest(query="q", search_depth="deep")}, budget=budget)
    assert "search:nimble" in [c.id for c in found.candidates]
    req = RouteRequest(prompt="q", candidates=found.candidates, deadline_s=1.0, budget=budget, pinned_tool="search")
    outcome = RouterOutcome(decision=RouteDecision(params={"search": {"max_results": 11}}), failure=None, elapsed_ms=1)
    out = decided(req, found, outcome, router="r", caller_set={"search_depth"})
    # Nimble's deep search is priced for ten results; at its default depth it charges none per result.
    assert "max_results" not in out.params
    assert out.record.params is None
