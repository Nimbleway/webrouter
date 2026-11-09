"""For tests/budget/: a job started under a shared budget and a preset carries its decision and its meta."""

from __future__ import annotations

from typing import Any

import pytest

from tests.budget.conftest import ARGS, Chain, Table
from tests.presets_helpers import REF, definition
from web_router import AsyncWebRouter, Budget, WebRouter

BUDGET = Budget(max_cost_usd=0.01, time_limit_s=400)


@pytest.mark.parametrize("tool", ["research", "browse"], indirect=True)
@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
async def test_a_budgeted_job_carries_its_decision_and_its_presets(
    chain: Chain, priced: Table, asynchronous: bool
) -> None:
    priced["first"] = (0.005, 10.0)
    tool = chain.tool.name
    section = {tool: {"defaults": {"max_steps": 3} if tool == "browse" else {"effort": "high"}}}
    client: Any = AsyncWebRouter() if asynchronous else WebRouter()
    client.presets.add(definition(components=section))
    if asynchronous:
        async with client.budget(BUDGET) as scope:
            job = await getattr(client, f"submit_{tool}")(ARGS[tool], provider="first", preset=REF)
            answer, status = await job.aresult(), await job.astatus()
    else:
        with client.budget(BUDGET) as scope:
            job = getattr(client, f"submit_{tool}")(ARGS[tool], provider="first", preset=REF)
            answer, status = job.result(), job.status()
    assert job.decision is not None and job.meta is not None
    assert answer.decision is not None and answer.meta.preset.validation.state == "passed"
    assert status.response.decision is not None and status.response.meta is not None
    assert scope.status().spent_usd == pytest.approx(0.005)
