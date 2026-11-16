"""A submitted job's answer carries what the run took and cost, as ``research()``'s does. Offline.

The real Nimble provider runs over the stubbed Web Search Agent client the
provider tests use, seated in the router as its ``nimble`` research backend.
"""

from __future__ import annotations

import pytest

from tests.candidates_helpers import clear_keys, set_keys
from tests.providers._nimble_research_stub import research_run, stub_client
from web_router import ProviderError, WebRouter

pytest.importorskip("nimble_python", reason="nimble extra not installed")

from web_router.providers.nimble import NimbleResearchProvider

RESULT = {"run": research_run("completed"), "output": {"type": "text", "content": "a", "trust": None, "sources": []}}


@pytest.fixture
def wr(monkeypatch: pytest.MonkeyPatch) -> WebRouter:
    """A router whose ``nimble`` research backend is the real provider over the stub, its run done at once."""
    set_keys(clear_keys(monkeypatch), "nimble")
    provider = NimbleResearchProvider()
    stub_client(provider, started=research_run("completed"), result=RESULT)
    router = WebRouter()
    router._research_instances["nimble"] = provider
    return router


def test_a_submitted_job_answers_with_its_time_and_cost(wr: WebRouter) -> None:
    answer = wr.submit_research("q", provider="nimble", effort="low").result()
    assert answer.elapsed_s is not None and answer.elapsed_s >= 0
    assert answer.cost_usd is not None and answer.cost_source == "estimated"


def test_asking_again_keeps_the_first_clock(wr: WebRouter) -> None:
    job = wr.submit_research("q", provider="nimble")
    assert job.result().elapsed_s == job.result().elapsed_s


def test_a_polled_answer_carries_its_time_too(wr: WebRouter) -> None:
    job = wr.submit_research("q", provider="nimble", effort="low")
    assert job.status().response is not None and job.status().response.elapsed_s is not None
    assert job.progress.response is not None and job.progress.response.elapsed_s == job.result().elapsed_s


def test_a_throttled_job_start_starts_the_backends_cooldown(wr: WebRouter, monkeypatch: pytest.MonkeyPatch) -> None:
    """A named backend's 429 on starting a job cools it for later ``auto`` calls, as a 429 on a call does."""
    throttled = ProviderError("nimble", "HTTP 429")
    throttled.status_code = 429

    def refused(request: object) -> None:
        raise throttled

    monkeypatch.setattr(wr._research_instances["nimble"], "submit", refused)
    with pytest.raises(ProviderError):
        wr.submit_research("q", provider="nimble")
    assert wr._runtime is not None and wr._runtime.cooldowns.remaining("nimble") is not None
