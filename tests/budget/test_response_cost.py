"""``elapsed_s``, ``cost_usd`` and ``cost_source`` on every routable response, budget or not.

Run through the public routers on an ``auto`` chain of three fake members whose
prices the test sets (see :mod:`tests.budget.conftest`).
"""

from __future__ import annotations

import pytest

from tests.budget.conftest import Chain, Table, acall, call
from web_router import Budget
from web_router.constants_cost import COST_ESTIMATED, COST_REPORTED
from web_router.exceptions import ConfigurationError


def test_a_call_with_no_budget_carries_its_time_and_the_answering_backends_estimate(
    chain: Chain, priced: Table
) -> None:
    priced.update(first=(0.004, None), second=(0.009, None))
    chain.sleeps["first"] = 0.05
    response = call(chain, None)
    assert response.provider == "first"
    assert response.elapsed_s >= 0.05
    per_call = 0.004  # extract is charged per URL; the test calls it with one
    assert (response.cost_usd, response.cost_source) == (pytest.approx(per_call), COST_ESTIMATED)


def test_the_cost_is_the_answering_backends_and_the_time_counts_the_failures_before_it(
    chain: Chain, priced: Table
) -> None:
    priced.update(first=(0.004, None), second=(0.009, None))
    chain.raises["first"] = ConfigurationError("first holds no key")  # every tool moves on past it
    chain.sleeps["first"] = 0.05
    response = call(chain, Budget())
    assert response.provider == "second"
    assert response.cost_usd == pytest.approx(0.009)
    assert response.elapsed_s >= 0.05
    assert [attempt.id for attempt in response.decision.attempts][-1].endswith(":second")


def test_an_unknown_price_leaves_no_cost(chain: Chain, priced: Table) -> None:
    response = call(chain, None)
    assert (response.cost_usd, response.cost_source) == (None, None)
    assert response.elapsed_s is not None


async def test_the_async_router_stamps_the_same_fields(chain: Chain, priced: Table) -> None:
    priced.update(first=(0.002, None))
    response = await acall(chain, None)
    assert (response.cost_usd, response.cost_source) == (pytest.approx(0.002), COST_ESTIMATED)
    assert response.elapsed_s is not None


STATES_COST = ("search", "answer")
"""The methods whose fake can state a cost on what it answers: a job's start answers progress, and an extract's
documents are gathered into a new response; no extract backend states a cost today."""


def test_a_cost_the_backend_stated_is_kept_and_labelled_reported(
    chain: Chain, priced: Table, monkeypatch: pytest.MonkeyPatch
) -> None:
    priced.update(first=(0.5, None))
    fake, entry = chain.tool.fake, chain.tool.entry
    real = getattr(fake, entry)
    states = chain.tool.name in STATES_COST

    def stated(self: object, *args: object) -> object:
        answered = real(self, *args)
        return answered.model_copy(update={"cost_usd": 0.0012, "cost_source": COST_REPORTED}) if states else answered

    monkeypatch.setattr(fake, entry, stated)
    response = call(chain, None)
    expected = (0.0012, COST_REPORTED) if states else (pytest.approx(0.5), COST_ESTIMATED)
    assert (response.cost_usd, response.cost_source) == expected
