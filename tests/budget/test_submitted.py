"""A job started inside a shared budget: its record carries what the budget charged the start, as the method's does."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from tests.budget.conftest import ARGS, Chain, Table
from tests.conftest import FakeResearchProvider
from web_router import AsyncWebRouter, Budget, WebRouter
from web_router.registry import register_research_provider, unregister_research_provider
from web_router.types_decision import DecisionRecord

BUDGET = Budget(max_cost_usd=0.01, time_limit_s=400)

JOBS = pytest.mark.parametrize("tool", ["research", "browse"], indirect=True)
"""The two methods that start a job."""

MODES = pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])


def _charged(record: DecisionRecord | None) -> tuple[Any, ...]:
    """What a record says the budget charged: the estimate, its label, each attempt's id and estimate; the effort."""
    assert record is not None
    attempts = [(attempt.id, attempt.est_cost_usd) for attempt in record.attempts or ()]
    return record.estimated_cost_usd, record.cost_is_estimate, attempts, record.params


async def _started(
    tool: str, budget: Budget | None, provider: str, asynchronous: bool
) -> tuple[Any, Any, float | None]:
    """The job ``provider`` starts for ``tool``, its answer, and what the shared budget spent; none with no budget."""
    if asynchronous:
        aclient = AsyncWebRouter()
        asubmit = getattr(aclient, f"submit_{tool}")
        if budget is None:
            job = await asubmit(ARGS[tool], provider=provider)
            return job, await job.aresult(), None
        async with aclient.budget(budget) as ascope:
            job = await asubmit(ARGS[tool], provider=provider)
            return job, await job.aresult(), ascope.status().spent_usd
    client = WebRouter()
    submit = getattr(client, f"submit_{tool}")
    if budget is None:
        job = submit(ARGS[tool], provider=provider)
        return job, job.result(), None
    with client.budget(budget) as scope:
        job = submit(ARGS[tool], provider=provider)
        return job, job.result(), scope.status().spent_usd


@JOBS
@MODES
async def test_a_budgeted_start_records_the_cost_the_budget_charged_it(
    chain: Chain, priced: Table, asynchronous: bool
) -> None:
    priced["first"] = (0.005, 10.0)
    job, answer, spent = await _started(chain.tool.name, BUDGET, "first", asynchronous)
    expected = (0.005, True, [(f"{chain.tool.name}:first", 0.005)], None)
    assert spent == pytest.approx(0.005)
    assert _charged(job.decision) == expected, "the start's record lacks what the budget charged it"
    assert _charged(answer.decision) == expected


@JOBS
@MODES
async def test_a_start_records_the_same_charge_as_the_method_called_under_that_budget(
    chain: Chain, priced: Table, asynchronous: bool
) -> None:
    priced["first"] = (0.005, 10.0)
    job, _, _ = await _started(chain.tool.name, BUDGET, "first", asynchronous)
    called = getattr(WebRouter(), chain.tool.name)(ARGS[chain.tool.name], provider="first", budget=BUDGET)
    assert _charged(job.decision) == _charged(called.decision)


@JOBS
@MODES
async def test_a_start_with_no_budget_records_nothing(chain: Chain, priced: Table, asynchronous: bool) -> None:
    priced["first"] = (0.005, 10.0)
    job, answer, _ = await _started(chain.tool.name, None, "first", asynchronous)
    assert job.decision is None and answer.decision is None


@pytest.fixture
def nimble() -> Iterator[None]:
    """A fake research backend registered as ``nimble``, so the real price list and effort table apply to it."""
    register_research_provider("nimble", type("FakeNimble", (FakeResearchProvider,), {"name": "nimble"}))
    yield
    unregister_research_provider("nimble")


@MODES
async def test_a_budgeted_start_records_its_cost_beside_the_effort_the_budget_mapped(
    nimble: None, asynchronous: bool
) -> None:
    job, _, spent = await _started("research", Budget(max_cost_usd=0.10), "nimble", asynchronous)
    assert job.decision.params == {"effort": "low"}, "nimble's default has no price: the budget maps low"
    assert spent and job.decision.estimated_cost_usd == pytest.approx(spent)
    assert [attempt.id for attempt in job.decision.attempts] == ["research:nimble"]
