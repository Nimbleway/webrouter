"""Research's shared budget effort fits every backend that can run at all, and ignores one that cannot.

A backend with no price at any effort never fits a cost cap, so it never
runs; it must not stop the effort that fits every other backend from being
sent.
"""

from __future__ import annotations

from typing import Any

import pytest

from web_router import Budget
from web_router.routing import params
from web_router.routing.params import EffortChoice, shared_effort

PRICED = EffortChoice(effort="medium", source="budget", reason="priced")
CAPPED = Budget(max_cost_usd=1.0)


@pytest.fixture(autouse=True)
def _one_priced_one_not(monkeypatch: pytest.MonkeyPatch) -> None:
    """``priced`` fits the cap at every effort and picks ``medium``; ``unpriced`` fits at none, its default included."""

    def mapped(provider: str, budget: Budget | None, effort: Any = None) -> EffortChoice:
        return PRICED if provider == "priced" else EffortChoice(effort=None, source=None, reason="", misfit="no price")

    monkeypatch.setattr(params, "map_effort", mapped)
    monkeypatch.setattr(params, "effort_misfit", lambda p, e, b: None if p == "priced" else "no known price")


@pytest.mark.parametrize("order", [["unpriced", "priced"], ["priced", "unpriced"]])
def test_a_backend_that_cannot_run_at_any_effort_does_not_cancel_the_effort(order: list[str]) -> None:
    assert shared_effort(order, CAPPED) == PRICED
