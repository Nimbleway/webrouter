"""The candidate builder and research's effort: a research candidate is priced at the effort the budget maps."""

from __future__ import annotations

import pytest

from tests.candidates_helpers import clear_keys, one, set_keys
from web_router import Budget, WebRouter
from web_router.prices import estimate
from web_router.routing.candidates import build_candidates
from web_router.routing.params import map_effort
from web_router.types import ResearchRequest

REQUEST = {"research": ResearchRequest(query="q")}


@pytest.fixture
def keys(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """No key in the environment; a test sets the ones it needs."""
    return clear_keys(monkeypatch)


def test_research_without_effort_is_priced_at_the_effort_the_budget_maps(keys: pytest.MonkeyPatch) -> None:
    set_keys(keys, "parallel")
    assert estimate("parallel", "research") == 0.025  # its default, medium: over a one-cent cap
    cap = Budget(max_cost_usd=0.01)
    assert map_effort("parallel", cap).effort == "low"
    found = build_candidates(WebRouter(), REQUEST, budget=cap)
    assert one(found, "research:parallel").est_cost_usd == estimate("parallel", "research", "low")
    unbudgeted = build_candidates(WebRouter(), REQUEST)
    assert one(unbudgeted, "research:parallel").est_cost_usd == 0.025, "no cap: the default, as the call runs"


def test_research_with_an_unknown_default_price_is_kept_at_a_priced_effort_the_budget_maps(
    keys: pytest.MonkeyPatch,
) -> None:
    set_keys(keys, "nimble")
    assert estimate("nimble", "research") is None  # priced only per effort
    assert one(build_candidates(WebRouter(), REQUEST), "research:nimble").est_cost_usd is None
    cap = Budget(max_cost_usd=10.0)
    mapped = map_effort("nimble", cap).effort
    found = build_candidates(WebRouter(), REQUEST, budget=cap)
    assert mapped is not None and one(found, "research:nimble").est_cost_usd == estimate("nimble", "research", mapped)


def test_with_too_few_measured_efforts_a_measured_one_that_fits_replaces_a_default_that_does_not() -> None:
    assert map_effort("linkup", Budget(max_cost_usd=2.0)).effort is None, "the default fits: it stands"
    assert map_effort("linkup", Budget(max_cost_usd=1.0)).effort == "low"
    assert map_effort("linkup", Budget(max_cost_usd=0.1)).effort is None, "nothing measured fits: the default"
