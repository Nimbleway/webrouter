"""The one pricing rule every routing part reads: a candidate is priced as the ledger charges it, and a fit is
judged the same way wherever it is asked."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.candidates_helpers import clear_keys, one, set_keys
from tests.merge_helpers import merge_with
from web_router import Budget, RouteDecision, WebRouter, prices
from web_router.routing.attempts import Attempt
from web_router.routing.candidates import build_candidates
from web_router.routing.candidates_priced import over_budget
from web_router.routing.ledger import Ledger
from web_router.routing.params import effort_misfit
from web_router.routing.pricing import attempt_p90
from web_router.types import SearchRequest
from web_router.types_answer import AnswerRequest

CAP = Budget(max_cost_usd=0.01)


def test_a_floor_row_is_an_unknown_price_for_a_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    set_keys(clear_keys(monkeypatch), "brave")
    assert prices.estimate("brave", "answer") is not None  # the row has a figure, but only a floor
    found = build_candidates(WebRouter(), {"answer": AnswerRequest(query="q")})
    assert one(found, "answer:brave").est_cost_usd is None
    capped = build_candidates(WebRouter(), {"answer": AnswerRequest(query="q")}, budget=Budget(max_cost_usd=10.0))
    assert "no known price" in capped.dropped["answer:brave"]


def test_a_floor_row_fits_no_cost_cap_for_a_mapped_effort_or_a_routers_depth(monkeypatch: pytest.MonkeyPatch) -> None:
    floor = SimpleNamespace(estimate_usd=0.001, estimate_is_floor=True, mode="low", results_assumed=None)
    monkeypatch.setattr(prices, "price", lambda *_: floor)
    assert "no known price" in (effort_misfit("parallel", "low", Budget(max_cost_usd=1000.0)) or "")
    decision = RouteDecision(
        params={"search": {"search_depth": "lite"}}, exclude={"search:exa": "no depth", "search:brave": "no depth"}
    )
    merged = merge_with(decision, pinned="search", budget=Budget(max_cost_usd=100.0))
    assert merged.params == {} and "no known price" in merged.notes.ignored["params.search.search_depth"]


@pytest.mark.parametrize(("cost", "fits"), [(0.01 + 5e-10, True), (0.01 + 1e-6, False)], ids=["in-tolerance", "over"])
def test_the_candidate_builder_and_the_ledger_draw_the_cap_at_one_line(cost: float, fits: bool) -> None:
    row = SimpleNamespace(estimate_usd=cost, estimate_is_floor=False, mode=None, results_assumed=None)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(prices, "price", lambda *_: row)
        patch.setattr(prices, "estimate_latency", lambda *_: None)
        admitted = Ledger(CAP).admit(Attempt(tool="search", provider="brave", request=SearchRequest(query="q")))
    assert (over_budget(cost, None, CAP) is None) is fits
    assert admitted is fits


def test_provider_options_make_the_p90_unknown_as_well_as_the_price() -> None:
    plain = Attempt(tool="search", provider="nimble", request=SearchRequest(query="q"))
    optioned = Attempt(tool="search", provider="nimble", request=SearchRequest(query="q", provider_options={"x": 1}))
    assert attempt_p90(plain) is not None
    assert attempt_p90(optioned) is None
