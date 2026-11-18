"""A submitted job's answer is written to the spend log once, as the method called directly writes it. Offline."""

from __future__ import annotations

from typing import Any

import pytest

from tests.spend_helpers import Kept
from web_router import AsyncWebRouter, Budget, WebRouter


@pytest.mark.parametrize("method", ["research", "browse"])
def test_a_submitted_job_is_logged_once(
    method: str, fake_research_provider: Any, fake_browse_provider: Any, clock: Any
) -> None:
    router, kept = WebRouter("fake"), Kept()
    router.log_spend(kept)
    job = getattr(router, f"submit_{method}")("q")
    job.result()
    job.result()
    assert [(entry.method, entry.provider) for entry in kept.entries] == [(method, "fake")]
    getattr(router, method)("q")
    assert len(kept.entries) == 2, "the control: the method called directly logs one more"


async def test_an_async_submitted_job_is_logged_once(fake_research_provider: Any, clock: Any) -> None:
    router, kept = AsyncWebRouter("fake"), Kept()
    router.log_spend(kept)
    job = await router.submit_research("q")
    await job.aresult()
    await job.aresult()
    assert [entry.method for entry in kept.entries] == ["research"]


def test_a_budgeted_job_logs_its_decision(fake_research_provider: Any, clock: Any) -> None:
    router, kept = WebRouter("fake"), Kept()
    router.log_spend(kept)
    with router.budget(Budget(time_limit_s=600)) as scoped:
        answer = scoped.submit_research("q").result()
    assert answer.decision is not None
    assert [entry.decision_id for entry in kept.entries] == [answer.decision.decision_id]
