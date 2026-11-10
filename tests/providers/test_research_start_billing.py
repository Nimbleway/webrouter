"""A research start that created a run never reads as one that did not: `auto` would bill a second run on it.

Nimble's and Parallel's real providers, over their real clients and a scripted transport.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from web_router.exceptions import ProviderError
from web_router.providers._chain import _nothing_started
from web_router.providers.parallel import ParallelResearchProvider
from web_router.types import ResearchRequest

pytest.importorskip("nimble_python", reason="nimble extra not installed")

from nimble_python import AsyncNimble, Nimble

from tests.providers._nimble_research_stub import AGENT_ID, RUN_ID, research_run
from web_router.providers.nimble import NimbleResearchProvider

QUERY = ResearchRequest(query="who founded it")
NIMBLE_RESULT = {"run": research_run("completed"), "output": {"type": "text", "content": "In 2019."}}
PARALLEL_RUN = {"run_id": "trun_1", "status": "completed", "is_active": False}
PARALLEL_RESULT = {"run": PARALLEL_RUN, "output": {"type": "text", "content": "Paris", "basis": []}}

Handler = Callable[[httpx.Request], httpx.Response]


def _nimble(handle: Handler, *, max_retries: int = 0) -> NimbleResearchProvider:
    provider = NimbleResearchProvider(api_key="test-key")
    provider._client = Nimble(
        api_key="test-key", max_retries=max_retries, http_client=httpx.Client(transport=httpx.MockTransport(handle))
    )
    return provider


def _async_nimble(handle: Handler, *, max_retries: int = 0) -> NimbleResearchProvider:
    provider = NimbleResearchProvider(api_key="test-key")
    transport = httpx.MockTransport(handle)
    provider._async_client = AsyncNimble(
        api_key="test-key", max_retries=max_retries, http_client=httpx.AsyncClient(transport=transport)
    )
    return provider


def _parallel(monkeypatch: pytest.MonkeyPatch, handle: Handler) -> ParallelResearchProvider:
    monkeypatch.setenv("PARALLEL_API_KEY", "test-key")
    provider = ParallelResearchProvider()
    transport = httpx.MockTransport(handle)
    provider._sync = httpx.Client(**provider._client_kwargs, transport=transport)
    provider._async = httpx.AsyncClient(**provider._client_kwargs, transport=transport)
    return provider


class _Script:
    """Answers the start with a finished run, and fails the answer's fetch until ``fixed``."""

    def __init__(self, run: dict[str, Any], result: dict[str, Any]) -> None:
        self.run, self.result, self.fixed = run, result, False
        self.posts = 0

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            self.posts += 1
            return httpx.Response(200, json=self.run)
        if request.url.path.endswith("/result"):
            return httpx.Response(200, json=self.result) if self.fixed else httpx.Response(404, json={"error": "gone"})
        return httpx.Response(200, json=self.run)


def test_nimble_keeps_a_finished_run_whose_answer_could_not_be_fetched() -> None:
    script = _Script(research_run("completed"), NIMBLE_RESULT)
    provider = _nimble(script.handle)

    job = provider.submit(QUERY)
    assert job.job_id.endswith(RUN_ID) and AGENT_ID in job.job_id
    assert job.progress.state == "running", "a refused answer fetch is not a run that never started"

    script.fixed = True
    assert provider.poll(QUERY, job.job_id).response.answer == "In 2019."  # type: ignore[union-attr]
    assert script.posts == 1


def test_parallel_keeps_a_finished_run_whose_answer_could_not_be_fetched(monkeypatch: pytest.MonkeyPatch) -> None:
    script = _Script(PARALLEL_RUN, PARALLEL_RESULT)
    provider = _parallel(monkeypatch, script.handle)

    job = provider.submit(QUERY)
    assert job.job_id == "trun_1"
    assert job.progress.state == "running"

    script.fixed = True
    assert provider.poll(QUERY, job.job_id).response.answer == "Paris"  # type: ignore[union-attr]
    assert script.posts == 1


@pytest.mark.parametrize(
    ("failure", "nothing_started"),
    [
        (httpx.RemoteProtocolError("Server disconnected without sending a response."), False),
        (httpx.ConnectError("connection refused"), True),
    ],
)
def test_only_a_connection_never_made_lets_auto_try_another_backend(failure: Exception, nothing_started: bool) -> None:
    """The SDK reports every transport failure as one error; a drop after the POST went out may have created the run."""

    def handle(request: httpx.Request) -> httpx.Response:
        raise failure

    with pytest.raises(ProviderError) as raised:
        _nimble(handle, max_retries=2).submit(QUERY)

    assert _nothing_started(raised.value) is nothing_started


def test_nimble_never_retries_the_call_that_creates_a_run() -> None:
    """A 5xx can come after the backend created the run; a retry would create, and bill, another."""
    posts: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        posts.append(request.url.path)
        return httpx.Response(503, json={"error": "busy"})

    with pytest.raises(ProviderError):
        _nimble(handle, max_retries=2).submit(QUERY)

    assert len(posts) == 1


async def test_the_async_start_keeps_a_finished_run_whose_answer_could_not_be_fetched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nimble_script = _Script(research_run("completed"), NIMBLE_RESULT)
    parallel_script = _Script(PARALLEL_RUN, PARALLEL_RESULT)

    nimble_job = await _async_nimble(nimble_script.handle).asubmit(QUERY)
    parallel_job = await _parallel(monkeypatch, parallel_script.handle).asubmit(QUERY)

    assert nimble_job.job_id.endswith(RUN_ID) and nimble_job.progress.state == "running"
    assert parallel_job.job_id == "trun_1" and parallel_job.progress.state == "running"
    assert nimble_script.posts == parallel_script.posts == 1


async def test_the_async_start_never_retries_the_call_that_creates_a_run() -> None:
    posts: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        posts.append(request.url.path)
        return httpx.Response(503, json={"error": "busy"})

    with pytest.raises(ProviderError):
        await _async_nimble(handle, max_retries=2).asubmit(QUERY)

    assert len(posts) == 1
