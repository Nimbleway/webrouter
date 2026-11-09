"""Research's shared budget effort fits every backend that can run at all, and ignores one that cannot.

A backend with no price at any effort never fits a cost cap, so it never
runs; it must not stop the effort that fits every other backend from being
sent.
"""

from __future__ import annotations

from typing import Any

import pytest

from web_router import Budget
from web_router.routing.params import shared_effort

CAPPED = Budget(max_cost_usd=1.0)


@pytest.mark.parametrize("order", [["unpriced", "priced"], ["priced", "unpriced"]])
def test_a_backend_that_cannot_run_at_any_effort_does_not_cancel_the_effort(
    research_backends: Any, order: list[str]
) -> None:
    research_backends("priced")
    research_backends("unpriced", unpriced=True)
    choice = shared_effort(order, CAPPED)
    assert choice is not None and (choice.effort, choice.source) == ("low", "budget"), "priced's own pick runs"
