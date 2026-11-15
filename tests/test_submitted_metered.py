"""A submitted job's answer carries what the run took and cost, as ``research()``'s does. Offline."""

from __future__ import annotations

from typing import Any

import pytest

from tests.candidates_helpers import clear_keys, set_keys
from web_router import WebRouter
from web_router.providers.nimble import NimbleResearchProvider
from web_router.types import ResearchProgress, ResearchRequest, ResearchResponse


@pytest.fixture(autouse=True)
def nimble_answers_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    set_keys(clear_keys(monkeypatch), "nimble")

    def start(self: Any, request: ResearchRequest) -> ResearchProgress:
        response = ResearchResponse(provider="nimble", query=request.query, answer="a")
        return ResearchProgress(job_id="job-1", state="completed", response=response)

    monkeypatch.setattr(NimbleResearchProvider, "_start", start)


def test_a_submitted_job_answers_with_its_time_and_cost() -> None:
    answer = WebRouter().submit_research("q", provider="nimble", effort="low").result()
    assert answer.elapsed_s is not None and answer.elapsed_s >= 0
    assert answer.cost_usd is not None and answer.cost_source == "estimated"


def test_asking_again_keeps_the_first_clock() -> None:
    job = WebRouter().submit_research("q", provider="nimble")
    assert job.result().elapsed_s == job.result().elapsed_s


def test_a_polled_answer_carries_its_time_too() -> None:
    job = WebRouter().submit_research("q", provider="nimble", effort="low")
    assert job.status().response is not None and job.status().response.elapsed_s is not None
    assert job.progress.response is not None and job.progress.response.elapsed_s == job.result().elapsed_s
