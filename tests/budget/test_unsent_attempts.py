"""A member ``auto`` passes over before sending it anything costs the budget nothing, so a capped call still answers."""

from __future__ import annotations

import httpx
import pytest

from tests.budget.conftest import Chain, Table
from web_router import Budget, WebRouter
from web_router.exceptions import ProviderError, UnsupportedParameterError

PRICE = 0.04
CAP = Budget(max_cost_usd=0.06)
"""Room for one member at :data:`PRICE`, not two: a phantom charge for the first leaves none for the second."""


def _throttled() -> ProviderError:
    request = httpx.Request("GET", "https://x.test")
    response = httpx.Response(429, request=request)
    return ProviderError(
        "first", "rate limited", original=httpx.HTTPStatusError("429", request=request, response=response)
    )


@pytest.mark.parametrize("tool", ["search"], indirect=True)
def test_a_member_cooling_down_is_not_charged_and_the_next_answers_within_the_cap(chain: Chain, priced: Table) -> None:
    priced.update(first=(PRICE, None), second=(PRICE, None), third=(PRICE, None))
    router = WebRouter()
    chain.raises["first"] = _throttled()
    router.search("q", provider=chain.auto())  # a 429 puts first in its cooldown
    del chain.raises["first"]
    chain.called.clear()

    response = router.search("q", provider=chain.auto(), budget=CAP)

    assert (chain.called, response.provider) == (["second"], "second")
    attempts = [(attempt.id, attempt.est_cost_usd) for attempt in response.decision.attempts]
    assert attempts == [("search:first", 0.0), ("search:second", PRICE)], "passed over, so listed at $0"
    assert [(hop.id, hop.error) for hop in response.decision.hops or []] == [("search:first", "cooling_down")]
    with router.budget(CAP) as shared:
        shared.search("q", provider=chain.auto())
        assert shared.status().spent_usd == pytest.approx(PRICE), "only the member that was sent a request"


@pytest.mark.parametrize("tool", ["answer"], indirect=True)
def test_a_member_refusing_an_option_before_sending_is_not_charged(chain: Chain, priced: Table) -> None:
    priced.update(first=(PRICE, None), second=(PRICE, None), third=(PRICE, None))
    chain.raises["first"] = UnsupportedParameterError("first", {"recency"}, detail="refuses this recency")

    response = WebRouter().answer("q", provider=chain.auto(), budget=CAP)

    assert response.provider == "second", "the refusal's estimate no longer crowds out the member that can answer"
    attempts = [(attempt.id, attempt.est_cost_usd) for attempt in response.decision.attempts]
    assert attempts == [("answer:first", 0.0), ("answer:second", PRICE)]
