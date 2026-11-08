"""A research candidate is offered only at an effort it takes for the request as it will run."""

from __future__ import annotations

import pytest

from tests.candidates_helpers import clear_keys, set_keys
from web_router import Budget, WebRouter
from web_router.routing.candidates import build_candidates
from web_router.types import ResearchRequest

ENRICH = ResearchRequest(query="q", records=[{"name": "a"}], output_schema={"type": "object"})


def test_with_no_room_for_an_effort_it_takes_the_backend_is_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    set_keys(clear_keys(monkeypatch), "nimble")
    found = build_candidates(WebRouter(), {"research": ENRICH}, budget=Budget(max_cost_usd=0.03))
    assert found.effort is None, "only low fits $0.03, and an enrichment refuses it: the budget maps none"
    assert found.candidates == ()
    assert "research:nimble" in found.dropped


def test_with_room_for_an_effort_it_takes_the_backend_is_offered(monkeypatch: pytest.MonkeyPatch) -> None:
    set_keys(clear_keys(monkeypatch), "nimble")
    found = build_candidates(WebRouter(), {"research": ENRICH})
    assert [c.id for c in found.candidates] == ["research:nimble"]
