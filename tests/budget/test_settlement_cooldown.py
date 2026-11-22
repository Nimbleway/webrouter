"""A backend ``auto`` passes over while it cools down after timeouts was never called: a budget charges it nothing."""

from __future__ import annotations

import pytest

from tests.hop_helpers import _Hangs, hop_members, hop_router
from tests.routing_helpers import Table
from web_router import Budget
from web_router.constants_production import TIMEOUT_COOLDOWN_AFTER

pytestmark = pytest.mark.usefixtures(hop_members.__name__)


def test_a_backend_passed_over_while_it_cools_down_is_charged_nothing(priced: Table) -> None:
    priced.update(hangs=(0.01, None), healthy=(0.02, None))
    router = hop_router(hop_timeout=0.1)
    for _ in range(TIMEOUT_COOLDOWN_AFTER):
        router.search("q", timeout=5)
    with router.budget(Budget(max_cost_usd=1.0)) as shared:
        response = router.search("q", timeout=5)
    assert _Hangs.calls == TIMEOUT_COOLDOWN_AFTER, "not called while cooling"
    assert [hop.error for hop in response.decision.hops] == ["cooling_down"]
    assert response.decision.estimated_cost_usd == pytest.approx(0.02)
    assert [attempt.est_cost_usd for attempt in response.decision.attempts] == [0.0, 0.02]
    assert shared.status().spent_usd == pytest.approx(0.02)
