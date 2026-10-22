"""Browserbase browse provider tests.

The mapping here is derived from Browserbase's Agents API reference -- Run an
Agent, Get a Run and Stop a Run -- and the recorded payloads below are
hand-written from that reference's ``AgentRun`` schema, with one correction
from a live run on 2026-09-30, and confirmed by another on 2026-10-21: with no
``resultSchema``, the ``result`` came back as an object carrying the answer as
its ``summary``, not as prose. Only the task is mapped. The API has no
start URL, so ``start_url`` is refused. ``output_schema`` is refused too until a
live call confirms ``resultSchema``; a schema sent through ``provider_options``
comes back as an object ``result``, which is ``structured``; no live run has
checked that yet. See CONTRIBUTING.md on verifying against the live API.
"""

from __future__ import annotations

import json
import os
import time
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from web_router.exceptions import ProviderError, UnsupportedParameterError
from web_router.providers import base
from web_router.providers.browserbase import BrowserbaseBrowseProvider
from web_router.types import BrowseRequest

KEY = "test-key"
RUN_ID = "5d1b0c52-7f3e-4a2b-9c1d-2e6f8a9b0c1d"
TASK = "Find the pricing page on example.com."
ANSWER = "The pricing page is https://example.com/pricing."


def _run(status: str, **extra: Any) -> dict[str, Any]:
    """An ``AgentRun``, as the start, status and stop calls all return one."""
    return {
        "runId": RUN_ID,
        "agentId": "agent-created-for-this-run",
        "task": TASK,
        "status": status,
        "sessionId": "session-driving-this-run",
        "createdAt": "2026-09-23T10:00:00.000Z",
        "updatedAt": "2026-09-23T10:00:05.000Z",
        **extra,
    }


COMPLETED = _run(
    "COMPLETED",
    result=ANSWER,
    startedAt="2026-09-23T10:00:01.000Z",
    endedAt="2026-09-23T10:02:00.000Z",
)


class _Browserbase:
    """A recorded Agents API: answers each call from a script and remembers what it was sent."""

    def __init__(self, states: list[dict[str, Any]], stop_status: int = 202) -> None:
        self.calls: list[tuple[str, str]] = []
        self.bodies: list[Any] = []
        self.headers: list[httpx.Headers] = []
        self.states = states
        self.stop_status = stop_status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.raw_path.decode()))
        self.headers.append(request.headers)
        if request.url.path == "/v1/agents/runs":
            self.bodies.append(json.loads(request.content))
            return httpx.Response(201, json=_run("PENDING"))
        if request.url.path.endswith("/stop"):
            if self.stop_status == 409:
                return httpx.Response(409, json={"error": "run has already finished"})
            return httpx.Response(self.stop_status, json=_run("RUNNING"))
        return httpx.Response(200, json=self.states.pop(0) if len(self.states) > 1 else self.states[0])


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def asleep(_: float) -> None:
        return None

    monkeypatch.setattr(base, "time", SimpleNamespace(sleep=lambda _: None, monotonic=time.monotonic))
    monkeypatch.setattr(base, "asyncio", SimpleNamespace(sleep=asleep))


def _provider(api: _Browserbase) -> BrowserbaseBrowseProvider:
    return BrowserbaseBrowseProvider(api_key=KEY, transport=httpx.MockTransport(api))


# -- What gets sent ----------------------------------------------------------


def test_the_task_is_all_that_is_sent_with_the_key_in_browserbases_header() -> None:
    api = _Browserbase([COMPLETED])
    _provider(api).browse(BrowseRequest(task=TASK))

    assert api.bodies == [{"task": TASK}]
    assert all(headers["x-bb-api-key"] == KEY for headers in api.headers)
    assert "authorization" not in api.headers[0]


def test_provider_options_reach_browserbase_untranslated() -> None:
    api = _Browserbase([COMPLETED])
    options = {"agentId": "agent-1", "browserSettings": {"proxies": True}}
    _provider(api).browse(BrowseRequest(task=TASK, provider_options=options))

    assert api.bodies == [{"task": TASK, **options}]


@pytest.mark.parametrize(
    "option",
    [
        {"start_url": "https://example.com"},
        {"output_schema": {"type": "object"}},
        {"max_steps": 10},
        {"allowed_domains": ["example.com"]},
    ],
    ids=lambda option: next(iter(option)),
)
def test_an_option_browserbase_has_no_confirmed_field_for_raises_before_a_run_is_billed(option: dict[str, Any]) -> None:
    api = _Browserbase([COMPLETED])
    with pytest.raises(UnsupportedParameterError):
        _provider(api).browse(BrowseRequest(task=TASK, **option))
    assert api.calls == []


def test_a_start_url_is_refused_rather_than_required() -> None:
    """The Agents API finds its own first page, so a missing start_url is never the reason a run cannot start."""
    assert BrowserbaseBrowseProvider.missing_parameters(BrowseRequest(task=TASK)) == frozenset()


# -- What comes back ---------------------------------------------------------


def test_the_run_is_started_then_polled_until_it_finishes() -> None:
    api = _Browserbase([_run("RUNNING"), COMPLETED])

    response = _provider(api).browse(BrowseRequest(task=TASK))

    assert api.calls == [
        ("POST", "/v1/agents/runs"),
        ("GET", f"/v1/agents/runs/{RUN_ID}"),
        ("GET", f"/v1/agents/runs/{RUN_ID}"),
    ]
    assert response.provider == "browserbase"
    assert response.task == TASK
    assert response.answer == ANSWER
    assert response.structured is None
    assert response.steps == [], "the transcript is a separate endpoint this provider does not read"
    assert response.outcome == "complete", "nothing Browserbase returns says a run stopped early"
    assert response.request_id == RUN_ID
    assert response.raw == [COMPLETED]


async def test_the_async_path_answers_the_same_way() -> None:
    api = _Browserbase([_run("RUNNING"), COMPLETED])

    response = await _provider(api).abrowse(BrowseRequest(task=TASK))

    assert [method for method, _ in api.calls] == ["POST", "GET", "GET"]
    assert response.answer == ANSWER


def test_the_session_the_run_drove_stays_out_of_every_plain_field() -> None:
    response = _provider(_Browserbase([COMPLETED])).browse(BrowseRequest(task=TASK))

    assert "session-driving-this-run" not in response.model_dump_json()
    assert response.raw is not None and response.raw[0]["sessionId"] == "session-driving-this-run"


def test_an_object_result_with_no_schema_asked_for_is_its_summary_not_structured_output() -> None:
    """What a live run returned on 2026-09-30, with no resultSchema: the answer sits in an object as its summary."""
    finished = _run("COMPLETED", result={"summary": ANSWER, "stepsTaken": 2, "taskDuration": 3756})

    response = _provider(_Browserbase([finished])).browse(BrowseRequest(task=TASK))

    assert response.answer == ANSWER
    assert response.structured is None
    assert response.raw == [finished]


def test_an_object_result_is_the_structured_answer_to_a_schema_sent_through_provider_options() -> None:
    """Browserbase returns an object result only for a resultSchema, and then conforming to it."""
    schema = {"type": "object", "properties": {"url": {"type": "string"}}}
    finished = _run("COMPLETED", result={"url": "https://example.com/pricing"})
    api = _Browserbase([finished])

    response = _provider(api).browse(BrowseRequest(task=TASK, provider_options={"resultSchema": schema}))

    assert api.bodies == [{"task": TASK, "resultSchema": schema}]
    assert response.structured == {"url": "https://example.com/pricing"}
    assert response.answer is None
    assert response.raw == [finished]


def test_an_unfinished_run_has_no_answer_yet() -> None:
    progress = _provider(_Browserbase([_run("RUNNING")])).poll(BrowseRequest(task=TASK), RUN_ID)

    assert progress.state == "running"
    assert progress.response is None


def test_a_status_browserbase_has_never_reported_before_is_not_passed_through() -> None:
    with pytest.raises(ProviderError, match="SOMETHING_NEW"):
        _provider(_Browserbase([_run("SOMETHING_NEW")])).poll(BrowseRequest(task=TASK), RUN_ID)


def test_a_run_id_is_quoted_into_the_path_rather_than_rewriting_it() -> None:
    api = _Browserbase([_run("RUNNING")])
    _provider(api).poll(BrowseRequest(task=TASK), "../sessions")

    assert api.calls == [("GET", "/v1/agents/runs/..%2Fsessions")]


# -- Runs that end without an answer ------------------------------------------


def test_a_failed_run_reports_the_cause_browserbase_gave() -> None:
    failed = _run("FAILED", cause={"code": "RUNNER_HEARTBEAT_LOST", "message": "the runner stopped responding"})

    progress = _provider(_Browserbase([failed])).poll(BrowseRequest(task=TASK), RUN_ID)

    assert progress.state == "failed"
    assert progress.detail == "the runner stopped responding"


def test_waiting_on_a_failed_run_raises_with_that_reason() -> None:
    failed = _run("FAILED", cause={"code": "RUNNER_HEARTBEAT_LOST"})

    with pytest.raises(ProviderError, match="RUNNER_HEARTBEAT_LOST"):
        _provider(_Browserbase([failed])).browse(BrowseRequest(task=TASK))


def test_a_run_that_ran_out_of_browserbases_time_is_a_failure_not_a_partial_answer() -> None:
    progress = _provider(_Browserbase([_run("TIMED_OUT")])).poll(BrowseRequest(task=TASK), RUN_ID)

    assert progress.state == "failed"
    assert progress.response is None


def test_waiting_on_a_run_that_timed_out_with_no_cause_raises_naming_the_status() -> None:
    with pytest.raises(ProviderError, match="TIMED_OUT"):
        _provider(_Browserbase([_run("TIMED_OUT")])).browse(BrowseRequest(task=TASK))


def test_a_stopped_run_is_cancelled() -> None:
    progress = _provider(_Browserbase([_run("STOPPED")])).poll(BrowseRequest(task=TASK), RUN_ID)

    assert progress.state == "cancelled"


# -- Stopping a run ------------------------------------------------------------


def test_cancel_asks_browserbase_to_stop_the_run() -> None:
    api = _Browserbase([_run("RUNNING")])
    job = _provider(api).submit(BrowseRequest(task=TASK))

    assert job.cancel() is True
    assert api.calls[-1] == ("POST", f"/v1/agents/runs/{RUN_ID}/stop")


async def test_async_cancel_asks_browserbase_to_stop_the_run() -> None:
    api = _Browserbase([_run("RUNNING")])

    assert await _provider(api).acancel(RUN_ID) is True
    assert api.calls == [("POST", f"/v1/agents/runs/{RUN_ID}/stop")]


def test_stopping_a_run_that_already_finished_is_not_an_error() -> None:
    """Browserbase answers 409 there; the run has stopped, which is all a cancel could have done."""
    assert _provider(_Browserbase([COMPLETED], stop_status=409)).cancel(RUN_ID) is False


def test_any_other_refusal_to_stop_is_raised() -> None:
    with pytest.raises(ProviderError, match="500"):
        _provider(_Browserbase([_run("RUNNING")], stop_status=500)).cancel(RUN_ID)


# -- Live ----------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("BROWSERBASE_API_KEY"), reason="BROWSERBASE_API_KEY not set")
def test_live_browse_answers_a_task() -> None:
    from tests.conftest import billing_aware
    from web_router import WebRouter

    with WebRouter("browserbase") as router, billing_aware("browserbase"):
        response = router.browse("What is the title of the page at https://example.com?", timeout=600)

    assert response.provider == "browserbase"
    assert response.request_id
    assert response.answer, f"expected a prose result, got {response.raw!r}"
    assert response.raw is not None
    # The docs disagree on whether a run with no agentId creates an Agent; record what this one did.
    print(f"browserbase run carried agentId: {'agentId' in response.raw[-1]}")
