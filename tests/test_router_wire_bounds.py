"""The router wire format's bounds on a decision: the confidence range and the entry limit, each held by the client."""

from __future__ import annotations

import pytest

from web_router.routing.wire import WireError, decision_from_wire


@pytest.mark.parametrize("confidence", [2, -0.1])
def test_a_decision_whose_confidence_is_outside_zero_to_one_is_refused(confidence: float) -> None:
    with pytest.raises(WireError, match="does not fit"):
        decision_from_wire({"schema": 1, "confidence": confidence})


@pytest.mark.parametrize("confidence", [0, 1])
def test_a_decision_whose_confidence_is_zero_or_one_is_kept(confidence: float) -> None:
    assert decision_from_wire({"schema": 1, "confidence": confidence}).confidence == confidence
