"""Skyvern browse provider tests.

The mapping here is derived from Skyvern's published OpenAPI spec
(skyvern.com/docs/api-reference/openapi.json) and its SDK reference, not from a
live call -- no account was available during development. The recorded
payloads below are hand-written from the spec's ``TaskRunResponse`` and the
polling example in its run-a-task guide, and only ``start_url`` is mapped
because nothing else could be confirmed. The integration test below is the
check that matters; run it once a key is available. See CONTRIBUTING.md on
verifying against the live API.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
import pytest

from web_router.exceptions import ProviderError, UnsupportedParameterError
from web_router.providers import base
from web_router.providers.skyvern import SkyvernBrowseProvider
from web_router.types import BrowseRequest

KEY = "test-key"
RUN_ID = "tsk_v2_486305187432193504"
RECORDING = "https://skyvern-artifacts.s3.amazonaws.com/v1/production/o_1/recording.webm?X-Amz-Signature=abc"
APP_URL = "https://app.skyvern.com/runs/wr_486305187432193510"

TASK = "Get the title of the top post"
START = "https://news.ycombinator.com"


def _run(status: str, **fields: Any) -> dict[str, Any]:
    """A task run, as the start call and the get-run call both return one."""
    return {
        "run_id": RUN_ID,
        "status": status,
        "output": None,
        "downloaded_files": None,
        "recording_url": None,
        "screenshot_urls": None,
        "failure_reason": None,
        "created_at": "2026-01-20T11:52:29.276851",
        "modified_at": "2026-01-20T11:52:29.484284",
        "app_url": APP_URL,
        "run_type": "task_v2",
        **fields,
    }


COMPLETED = _run(
    "completed",
    output={"top_post_title": "Linux kernel framework for PCIe device emulation, in userspace"},
    downloaded_files=[],
    recording_url=RECORDING,
    screenshot_urls=["https://skyvern-artifacts.s3.amazonaws.com/o_1/screenshot_final.png?X-Amz-Signature=def"],
    errors=[],
    step_count=2,
    finished_at="2026-01-20T11:54:08.821985",
)


class _Skyvern:
    """A recorded Skyvern API: answers each call from a script and remembers what it was sent."""

    def __init__(self, started: dict[str, Any], polled: list[dict[str, Any]] | None = None) -> None:
        self.started = started
        self.polled = polled or [COMPLETED]
        self.calls: list[tuple[str, str]] = []
        self.bodies: list[dict[str, Any]] = []
        self.headers: list[httpx.Headers] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.raw_path.decode()))
        self.headers.append(request.headers)
        if request.url.path.endswith("/cancel"):
            return httpx.Response(200, json=None)
        if request.method == "POST":
            self.bodies.append(json.loads(request.content))
            return httpx.Response(200, json=self.started)
        return httpx.Response(200, json=self.polled.pop(0) if len(self.polled) > 1 else self.polled[0])


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _asleep(seconds: float) -> None:
        return None

    monkeypatch.setattr(base.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(base.asyncio, "sleep", _asleep)


@pytest.fixture
def key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SKYVERN_API_KEY", KEY)


def _provider(api: _Skyvern) -> SkyvernBrowseProvider:
    return SkyvernBrowseProvider(transport=httpx.MockTransport(api))


# -- What gets sent ----------------------------------------------------------


@pytest.mark.usefixtures("key")
def test_the_task_is_the_prompt_and_the_start_url_is_the_url() -> None:
    api = _Skyvern(started=_run("queued"))
    _provider(api).submit(BrowseRequest(task=TASK, start_url=START))

    assert api.calls == [("POST", "/v1/run/tasks")]
    assert api.bodies == [{"prompt": TASK, "url": START}]
    assert api.headers[0]["x-api-key"] == KEY
    assert "authorization" not in api.headers[0]


@pytest.mark.usefixtures("key")
def test_a_task_with_no_start_url_is_started_without_one() -> None:
    """Skyvern's agent begins from a blank page, so the URL is optional and unset means unset."""
    assert SkyvernBrowseProvider.missing_parameters(BrowseRequest(task=TASK)) == frozenset()

    api = _Skyvern(started=_run("queued"))
    _provider(api).submit(BrowseRequest(task=TASK))

    assert api.bodies == [{"prompt": TASK}]


@pytest.mark.usefixtures("key")
def test_provider_options_reach_skyvern_untranslated() -> None:
    api = _Skyvern(started=_run("queued"))
    _provider(api).submit(BrowseRequest(task=TASK, provider_options={"engine": "skyvern-2.0", "max_steps": 5}))

    assert api.bodies[0] == {"prompt": TASK, "engine": "skyvern-2.0", "max_steps": 5}


@pytest.mark.parametrize(
    "option",
    [
        {"output_schema": {"type": "object"}},
        {"max_steps": 10},
        {"allowed_domains": ["example.com"]},
    ],
)
@pytest.mark.usefixtures("key")
def test_an_option_no_live_call_confirmed_is_refused_before_anything_is_billed(option: dict[str, Any]) -> None:
    api = _Skyvern(started=_run("queued"))

    with pytest.raises(UnsupportedParameterError):
        _provider(api).submit(BrowseRequest(task=TASK, **option))
    assert api.calls == []


# -- Following a task --------------------------------------------------------


@pytest.mark.usefixtures("key")
def test_a_task_is_started_then_polled_until_it_answers() -> None:
    api = _Skyvern(started=_run("created"), polled=[_run("queued"), _run("running"), COMPLETED])

    response = _provider(api).browse(BrowseRequest(task=TASK, start_url=START))

    assert api.calls == [("POST", "/v1/run/tasks")] + [("GET", f"/v1/runs/{RUN_ID}")] * 3
    assert response.provider == "skyvern"
    assert response.task == TASK
    assert response.structured is None, "the agent chose these keys; no schema was asked for"
    assert response.answer is None
    assert response.raw is not None
    assert response.raw[0]["output"] == COMPLETED["output"]
    assert response.request_id == RUN_ID
    assert response.steps == [], "Skyvern reports a step count, not the steps"
    assert response.outcome == "complete"
    assert response.raw == [COMPLETED]


@pytest.mark.usefixtures("key")
def test_the_dashboard_link_stays_out_of_every_plain_field() -> None:
    api = _Skyvern(started=_run("queued"))

    response = _provider(api).browse(BrowseRequest(task=TASK))

    assert APP_URL not in json.dumps(response.model_dump())
    assert APP_URL not in repr(response)
    assert response.raw is not None and response.raw[0]["app_url"] == APP_URL


@pytest.mark.usefixtures("key")
def test_the_presigned_recording_url_stays_in_raw_alone() -> None:
    """Skyvern's recording link carries X-Amz credentials, so it never reaches a plain field."""
    api = _Skyvern(started=_run("queued"))

    response = _provider(api).browse(BrowseRequest(task=TASK))

    assert response.recording_url is None
    assert RECORDING not in json.dumps(response.model_dump())
    assert RECORDING not in repr(response)
    assert response.raw is not None and response.raw[0]["recording_url"] == RECORDING


@pytest.mark.usefixtures("key")
def test_a_prose_output_is_the_answer() -> None:
    api = _Skyvern(started=_run("completed", output="The top post is about PCIe."))

    response = _provider(api).browse(BrowseRequest(task=TASK))

    assert response.answer == "The top post is about PCIe."
    assert response.structured is None
    assert api.calls == [("POST", "/v1/run/tasks")], "a run that starts finished is not polled"


@pytest.mark.usefixtures("key")
async def test_the_async_path_starts_polls_and_answers_the_same_way() -> None:
    api = _Skyvern(started=_run("queued"), polled=[_run("running"), COMPLETED])

    response = await _provider(api).abrowse(BrowseRequest(task=TASK))

    assert api.calls == [("POST", "/v1/run/tasks")] + [("GET", f"/v1/runs/{RUN_ID}")] * 2
    assert response.structured is None
    assert response.raw is not None and response.raw[0]["output"] == COMPLETED["output"]


@pytest.mark.parametrize(
    ("status", "state", "detail"),
    [("paused", "running", "paused"), ("created", "queued", None), ("running", "running", None)],
)
@pytest.mark.usefixtures("key")
def test_a_run_that_has_not_finished_carries_no_answer(status: str, state: str, detail: str | None) -> None:
    """``paused`` reads as running, and says so in ``detail`` so a caller can tell the two apart."""
    api = _Skyvern(started=_run("queued"), polled=[_run(status)])

    progress = _provider(api).poll(BrowseRequest(task=TASK), RUN_ID)

    assert progress.state == state
    assert progress.detail == detail
    assert progress.response is None
    assert not progress.is_terminal


@pytest.mark.usefixtures("key")
def test_a_status_skyvern_has_never_reported_is_not_passed_through() -> None:
    """Treating an unknown state as "still running" would poll a finished task until the timeout."""
    api = _Skyvern(started=_run("thinking_about_it"))

    with pytest.raises(ProviderError, match="unrecognized"):
        _provider(api).submit(BrowseRequest(task=TASK))


@pytest.mark.parametrize("status", ["failed", "terminated", "timed_out"])
@pytest.mark.usefixtures("key")
def test_a_run_that_did_not_complete_fails_with_skyverns_own_reason(status: str) -> None:
    """``timed_out`` cannot tell the step limit from the clock, so it is a failure, never ``partial``."""
    stopped = _run(status, failure_reason="Reached the maximum steps (10)")
    api = _Skyvern(started=_run("queued"), polled=[stopped])
    provider = _provider(api)

    progress = provider.poll(BrowseRequest(task=TASK), RUN_ID)
    assert progress.state == "failed"
    assert progress.detail == "Reached the maximum steps (10)"
    assert progress.response is None

    with pytest.raises(ProviderError, match="maximum steps"):
        provider.browse(BrowseRequest(task=TASK))


# -- Cancelling ----------------------------------------------------------------


@pytest.mark.usefixtures("key")
def test_cancelling_a_running_task_posts_to_skyverns_cancel_endpoint() -> None:
    api = _Skyvern(started=_run("queued"), polled=[_run("running")])
    job = _provider(api).submit(BrowseRequest(task=TASK))

    assert job.cancel() is True
    assert api.calls[-1] == ("POST", f"/v1/runs/{RUN_ID}/cancel")


@pytest.mark.usefixtures("key")
async def test_cancelling_works_on_the_async_path_too() -> None:
    api = _Skyvern(started=_run("queued"))

    assert await _provider(api).acancel(RUN_ID) is True
    assert api.calls == [("POST", f"/v1/runs/{RUN_ID}/cancel")]


@pytest.mark.usefixtures("key")
def test_cancelling_a_run_that_already_finished_is_accepted_and_changes_nothing() -> None:
    """Skyvern answers a cancel of a finished run with a 200 no-op, so True means "accepted", not "stopped"."""
    api = _Skyvern(started=_run("queued"), polled=[COMPLETED])
    provider = _provider(api)

    assert provider.cancel(RUN_ID) is True
    assert provider.poll(BrowseRequest(task=TASK), RUN_ID).state == "completed"
    assert api.calls == [("POST", f"/v1/runs/{RUN_ID}/cancel"), ("GET", f"/v1/runs/{RUN_ID}")]


@pytest.mark.usefixtures("key")
def test_a_cancelled_run_reads_as_cancelled() -> None:
    api = _Skyvern(started=_run("queued"), polled=[_run("canceled")])

    progress = _provider(api).poll(BrowseRequest(task=TASK), RUN_ID)

    assert progress.state == "cancelled"
    assert progress.is_terminal


@pytest.mark.usefixtures("key")
def test_a_run_id_cannot_point_a_request_elsewhere() -> None:
    api = _Skyvern(started=_run("queued"), polled=[_run("running")])

    _provider(api).poll(BrowseRequest(task=TASK), "tsk_1/../../v1/credentials")

    assert api.calls == [("GET", "/v1/runs/tsk_1%2F..%2F..%2Fv1%2Fcredentials")]


# -- Live ----------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("SKYVERN_API_KEY"), reason="SKYVERN_API_KEY not set")
def test_live_browse_answers_from_the_page() -> None:
    from tests.conftest import billing_aware
    from web_router import WebRouter

    with WebRouter("skyvern") as router, billing_aware("skyvern"):
        response = router.browse(
            "Return the text of the page's main heading",
            provider="skyvern",
            start_url="https://example.com",
        )

    assert response.provider == "skyvern"
    assert response.request_id
    assert response.raw is not None
    assert response.raw[0]["output"]
