"""examples/router_decider.py: a custom decider in a few lines, registered by name. It calls nothing."""

from __future__ import annotations

import pytest

from tests.decider_helpers import CANDIDATES, request
from tests.examples.helpers import load_example
from web_router import DeciderRouter, RouteDecision, unregister_router

decider = load_example("router_decider")


def test_it_ranks_the_priced_options_cheapest_first_under_the_ceiling() -> None:
    decision = DeciderRouter(decider.Cheapest(ceiling_usd=0.05)).route(request(candidates=CANDIDATES))
    assert decision == RouteDecision(
        tools=["answer", "search"],
        providers={"answer": ["perplexity"], "search": ["tavily"]},
        reason="cheapest: answer:perplexity",
    )


def test_main_plans_through_the_registered_name(capsys: pytest.CaptureFixture[str]) -> None:
    try:
        decider.main()
    finally:
        unregister_router("cheapest")
    out = capsys.readouterr().out
    assert out.count("(cheapest: ") == len(decider.PROMPTS)
