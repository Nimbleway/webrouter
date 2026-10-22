"""The Browser Use browse provider, over a stubbed v2 Tasks API.

The mapping is from Browser Use's published v2 OpenAPI reference
(docs.browser-use.com/cloud/api-v2/tasks), checked against live tasks on
2026-10-21: a prose task, ``structuredOutput``, ``maxSteps`` and
``allowedDomains`` each behaved as mapped here, and every step came back with
an empty ``nextGoal``, its note in ``memory`` and a single action. The payloads
below follow the ``TaskCreatedResponse``, ``TaskStatusView`` and ``TaskView``
schemas in that shape. The integration tests below repeat those checks.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
import pytest

from web_router.exceptions import ProviderError
from web_router.providers.browser_use import BrowserUseBrowseProvider
from web_router.types import BrowseRequest

TASK_ID = "3f1c2b9e-8a4d-4c1e-9b7a-2d5e6f708192"
SESSION_ID = "b7e2a1c4-5d6f-4a8b-9c0d-1e2f3a4b5c6d"
TASKS = "/api/v2/tasks"

CREATED = {"id": TASK_ID, "sessionId": SESSION_ID}

STEP = {
    "number": 1,
    "memory": "On the Hacker News front page.",
    "evaluationPreviousGoal": "Success - the page loaded.",
    "nextGoal": "Read the title of the top story.",
    "url": "https://news.ycombinator.com/",
    "screenshotUrl": "https://cdn.browser-use.com/screenshots/step-1.png",
    "actions": ['{"go_to_url": {"url": "https://news.ycombinator.com"}}'],
    "duration": 3.2,
}

SCHEMA = {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]}


def _status(status: str, output: str | None = None) -> dict[str, Any]:
    """A task as the lightweight status call reports it."""
    return {
        "id": TASK_ID,
        "status": status,
        "output": output,
        "finishedAt": "2026-09-23T10:00:12" if status in ("finished", "failed", "stopped") else None,
        "isSuccess": True if status == "finished" else None,
        "cost": "0.02",
    }


def _task(status: str, output: str | None = None, steps: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """A task as the full read reports it."""
    return {
        "id": TASK_ID,
        "sessionId": SESSION_ID,
        "llm": "browser-use-2.0",
        "task": "What is the top story on Hacker News?",
        "status": status,
        "createdAt": "2026-09-23T10:00:00",
        "startedAt": "2026-09-23T10:00:01",
        "finishedAt": "2026-09-23T10:00:12",
        "metadata": {},
        "steps": [STEP] if steps is None else steps,
        "output": output,
        "outputFiles": [],
        "browserUseVersion": "0.9.0",
        "isSuccess": True,
        "judgement": None,
        "judgeVerdict": None,
        "cost": "0.02",
        "suggestions": None,
    }


class _Api:
    """Stands in for the Tasks API: answers from a script and records every call."""

    def __init__(self, states: list[dict[str, Any]], task: dict[str, Any] | None) -> None:
        self._states = states
        self._task = task
        self.calls: list[tuple[str, str]] = []
        self.bodies: list[dict[str, Any]] = []
        self.headers: list[httpx.Headers] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path))
        self.headers.append(request.headers)
        if request.method in ("POST", "PATCH"):
            self.bodies.append(json.loads(request.content))
        if request.method == "POST":
            return httpx.Response(202, json=CREATED)
        if request.method == "PATCH":
            return httpx.Response(200, json=_task("stopped"))
        if request.url.path.endswith("/status"):
            return httpx.Response(200, json=self._states.pop(0) if len(self._states) > 1 else self._states[0])
        assert self._task is not None, "the provider read the full task of one that never finished"
        return httpx.Response(200, json=self._task)


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> BrowserUseBrowseProvider:
    monkeypatch.setenv("BROWSER_USE_API_KEY", "test-key")
    return BrowserUseBrowseProvider()


def _stub(
    provider: BrowserUseBrowseProvider,
    *,
    states: list[dict[str, Any]] | None = None,
    task: dict[str, Any] | None = None,
) -> _Api:
    """Answer this provider's HTTP calls from a script, through its real client settings."""
    api = _Api(states or [_status("finished", "Show HN")], task)
    transport = httpx.MockTransport(api.handle)
    provider._sync = httpx.Client(**provider._client_kwargs, transport=transport)
    provider._async = httpx.AsyncClient(**provider._client_kwargs, transport=transport)
    return api


# -- What gets sent ----------------------------------------------------------


def test_the_task_and_start_url_are_the_whole_body(provider: BrowserUseBrowseProvider) -> None:
    request = BrowseRequest(task="find the top story", start_url="https://news.ycombinator.com")
    assert provider._build_payload(request) == {
        "task": "find the top story",
        "startUrl": "https://news.ycombinator.com",
    }


def test_an_unset_start_url_is_not_sent(provider: BrowserUseBrowseProvider) -> None:
    """Browser Use finds its own first page, so the URL is optional there too."""
    assert provider._build_payload(BrowseRequest(task="t")) == {"task": "t"}
    assert BrowserUseBrowseProvider.missing_parameters(BrowseRequest(task="t")) == frozenset()


def test_provider_options_pass_through(provider: BrowserUseBrowseProvider) -> None:
    payload = provider._build_payload(BrowseRequest(task="t", provider_options={"llm": "gpt-4.1"}))
    assert payload["llm"] == "gpt-4.1"


def test_the_step_limit_and_domain_list_keep_their_shape(provider: BrowserUseBrowseProvider) -> None:
    request = BrowseRequest(task="t", max_steps=5, allowed_domains=["example.com", "iana.org"])
    assert provider._build_payload(request) == {
        "task": "t",
        "maxSteps": 5,
        "allowedDomains": ["example.com", "iana.org"],
    }


def test_the_schema_is_sent_as_a_json_string(provider: BrowserUseBrowseProvider) -> None:
    """Browser Use's `structuredOutput` is a string field, not an object."""
    payload = provider._build_payload(BrowseRequest(task="t", output_schema=SCHEMA))
    assert json.loads(payload["structuredOutput"]) == SCHEMA


def test_an_empty_schema_is_still_a_schema(provider: BrowserUseBrowseProvider) -> None:
    """`{}` is a valid schema that accepts any JSON; only an unset one sends nothing."""
    assert provider._build_payload(BrowseRequest(task="t", output_schema={}))["structuredOutput"] == "{}"
    assert "structuredOutput" not in provider._build_payload(BrowseRequest(task="t"))


def test_every_browse_option_reaches_the_wire(provider: BrowserUseBrowseProvider) -> None:
    """Through a real submit, so an option dropped from the supported set or the field map fails here."""
    api = _stub(provider)
    provider.submit(
        BrowseRequest(
            task="t",
            start_url="https://example.com",
            output_schema=SCHEMA,
            max_steps=5,
            allowed_domains=["example.com"],
        )
    )
    (body,) = api.bodies
    assert body["startUrl"] == "https://example.com"
    assert json.loads(body["structuredOutput"]) == SCHEMA
    assert body["maxSteps"] == 5
    assert body["allowedDomains"] == ["example.com"]


def test_the_key_goes_in_browser_uses_own_header(provider: BrowserUseBrowseProvider) -> None:
    api = _stub(provider, task=_task("finished", "Show HN"))
    provider.submit(BrowseRequest(task="t"))
    assert api.headers[0]["X-Browser-Use-API-Key"] == "test-key"
    assert "authorization" not in api.headers[0]


# -- What comes back ---------------------------------------------------------


def test_the_task_is_started_then_polled_then_read_once_in_full(provider: BrowserUseBrowseProvider) -> None:
    api = _stub(provider, states=[_status("finished", "Show HN")], task=_task("finished", "Show HN"))

    response = provider.browse(BrowseRequest(task="What is the top story on Hacker News?"))

    assert api.calls == [
        ("POST", TASKS),
        ("GET", f"{TASKS}/{TASK_ID}/status"),
        ("GET", f"{TASKS}/{TASK_ID}"),
    ]
    assert response.provider == "browser-use"
    assert response.task == "What is the top story on Hacker News?"
    assert response.answer == "Show HN"
    assert response.structured is None
    assert response.request_id == TASK_ID
    assert response.outcome == "complete", "nothing Browser Use returns says a task stopped early"
    assert response.recording_url is None
    assert response.raw == [_status("finished", "Show HN"), _task("finished", "Show HN")]


def test_a_step_carries_only_what_browser_use_reported(provider: BrowserUseBrowseProvider) -> None:
    _stub(provider, task=_task("finished", "Show HN"))

    (step,) = provider.browse(BrowseRequest(task="t")).steps

    assert step.url == "https://news.ycombinator.com/"
    assert step.screenshot_url == "https://cdn.browser-use.com/screenshots/step-1.png"
    assert step.detail == "On the Hacker News front page."
    assert step.action == '{"go_to_url": {"url": "https://news.ycombinator.com"}}'
    assert step.extra["actions"] == STEP["actions"]
    assert step.extra["number"] == 1
    assert step.extra["nextGoal"] == STEP["nextGoal"]


def test_a_step_with_several_actions_has_no_single_action(provider: BrowserUseBrowseProvider) -> None:
    actions = ['{"click": {"index": 3}}', '{"done": {"text": "Show HN"}}']
    _stub(provider, task=_task("finished", "Show HN", steps=[{**STEP, "actions": actions}]))

    (step,) = provider.browse(BrowseRequest(task="t")).steps

    assert step.action is None, "joining them would be a format Browser Use never wrote"
    assert step.extra["actions"] == actions


def test_a_schema_answer_is_structured(provider: BrowserUseBrowseProvider) -> None:
    output = '{"title": "Example Domain"}'
    _stub(provider, states=[_status("finished", output)], task=_task("finished", output))

    response = provider.browse(BrowseRequest(task="t", output_schema=SCHEMA))

    assert response.structured == {"title": "Example Domain"}
    assert response.answer is None


@pytest.mark.parametrize("output", ["I could not find a title.", "42", '"Paris"'])
def test_a_schema_answer_that_is_not_json_stays_prose(provider: BrowserUseBrowseProvider, output: str) -> None:
    """The agent may fail to produce the schema; its words are kept rather than dropped.

    A bare JSON value is not an object or a list, so it stays in `answer` as JSON text.
    """
    _stub(provider, states=[_status("finished", output)], task=_task("finished", output))

    response = provider.browse(BrowseRequest(task="t", output_schema=SCHEMA))

    assert response.structured is None
    assert response.answer == output


def test_json_prose_is_not_structured_when_no_schema_was_asked_for(provider: BrowserUseBrowseProvider) -> None:
    output = '{"title": "Example Domain"}'
    _stub(provider, states=[_status("finished", output)], task=_task("finished", output))

    response = provider.browse(BrowseRequest(task="t"))

    assert response.structured is None
    assert response.answer == output


def test_a_task_that_reported_no_steps_has_none(provider: BrowserUseBrowseProvider) -> None:
    _stub(provider, task=_task("finished", "Show HN", steps=[]))
    assert provider.browse(BrowseRequest(task="t")).steps == []


async def test_the_async_path_answers_the_same_way(provider: BrowserUseBrowseProvider) -> None:
    api = _stub(provider, task=_task("finished", "Show HN"))

    response = await provider.abrowse(BrowseRequest(task="t"))

    assert response.answer == "Show HN"
    assert ("GET", f"{TASKS}/{TASK_ID}") in api.calls


# -- Following a task --------------------------------------------------------


def test_the_start_reports_the_task_as_accepted(provider: BrowserUseBrowseProvider) -> None:
    api = _stub(provider)

    job = provider.submit(BrowseRequest(task="t"))

    assert job.job_id == TASK_ID
    assert job.state == "queued"
    assert api.calls == [("POST", TASKS)], "submitting does not wait"


@pytest.mark.parametrize(("reported", "state"), [("created", "queued"), ("started", "running")])
def test_an_unfinished_task_is_not_read_in_full(provider: BrowserUseBrowseProvider, reported: str, state: str) -> None:
    api = _stub(provider, states=[_status(reported)])

    progress = provider.poll(BrowseRequest(task="t"), TASK_ID)

    assert progress.state == state
    assert progress.response is None
    assert progress.detail is None
    assert api.calls == [("GET", f"{TASKS}/{TASK_ID}/status")]


def test_a_status_browser_use_has_never_reported_is_not_passed_through(provider: BrowserUseBrowseProvider) -> None:
    """Treating an unknown status as "still running" would poll a finished task forever."""
    _stub(provider, states=[_status("paused")])

    with pytest.raises(ProviderError, match="paused"):
        provider.poll(BrowseRequest(task="t"), TASK_ID)


def test_a_failed_task_reports_what_browser_use_said(provider: BrowserUseBrowseProvider) -> None:
    _stub(provider, states=[_status("failed", "the page could not be reached")])

    progress = provider.poll(BrowseRequest(task="t"), TASK_ID)

    assert progress.state == "failed"
    assert progress.is_terminal
    assert progress.detail == "the page could not be reached"


def test_waiting_on_a_failed_task_raises_with_that_reason(provider: BrowserUseBrowseProvider) -> None:
    _stub(provider, states=[_status("failed", "the page could not be reached")])

    with pytest.raises(ProviderError, match="could not be reached"):
        provider.browse(BrowseRequest(task="t"))


def test_a_stopped_task_reads_as_cancelled(provider: BrowserUseBrowseProvider) -> None:
    _stub(provider, states=[_status("stopped")])
    assert provider.poll(BrowseRequest(task="t"), TASK_ID).state == "cancelled"


def test_cancelling_stops_the_task_and_the_session_browser_use_made_for_it(
    provider: BrowserUseBrowseProvider,
) -> None:
    api = _stub(provider, states=[_status("started")])
    job = provider.submit(BrowseRequest(task="t"))

    assert job.cancel() is True
    assert api.calls[-1] == ("PATCH", f"{TASKS}/{TASK_ID}")
    assert api.bodies[-1] == {"action": "stop_task_and_session"}


def test_cancelling_a_task_in_the_callers_own_session_leaves_that_session_running(
    provider: BrowserUseBrowseProvider,
) -> None:
    api = _stub(provider, states=[_status("started")])
    job = provider.submit(BrowseRequest(task="t", provider_options={"sessionId": SESSION_ID}))

    assert job.cancel() is True
    assert api.bodies[0]["sessionId"] == SESSION_ID
    assert api.bodies[-1] == {"action": "stop"}


@pytest.mark.parametrize(
    ("options", "body"),
    [({}, {"action": "stop_task_and_session"}), ({"sessionId": SESSION_ID}, {"action": "stop"})],
)
async def test_cancelling_on_the_async_path_chooses_the_same_way(
    provider: BrowserUseBrowseProvider, options: dict[str, Any], body: dict[str, str]
) -> None:
    api = _stub(provider, states=[_status("started")])
    job = await provider.asubmit(BrowseRequest(task="t", provider_options=options))

    assert await job.acancel() is True
    assert api.bodies[-1] == body


def test_a_task_this_provider_did_not_start_is_stopped_alone(provider: BrowserUseBrowseProvider) -> None:
    """Its session may be the caller's, so only a task known to own its session takes the session with it."""
    api = _stub(provider)

    assert provider.cancel(TASK_ID) is True
    assert api.calls == [("PATCH", f"{TASKS}/{TASK_ID}")]
    assert api.bodies == [{"action": "stop"}]


# -- Live --------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("BROWSER_USE_API_KEY"), reason="BROWSER_USE_API_KEY not set")
def test_live_browse_answers_a_simple_task() -> None:
    from tests.conftest import billing_aware
    from web_router import WebRouter

    with WebRouter() as router, billing_aware("browser-use"):
        response = router.browse(
            "What is the title of this page? Answer with the title only.",
            provider="browser-use",
            start_url="https://example.com",
            timeout=600,
        )

    assert response.provider == "browser-use"
    assert response.answer
    assert "example" in response.answer.lower()
    assert response.request_id
    assert response.steps


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("BROWSER_USE_API_KEY"), reason="BROWSER_USE_API_KEY not set")
def test_live_browse_answers_in_the_schema_within_the_step_limit() -> None:
    from tests.conftest import billing_aware
    from web_router import WebRouter

    max_steps = 5
    with WebRouter() as router, billing_aware("browser-use"):
        response = router.browse(
            "Report the page's <title> text.",
            provider="browser-use",
            start_url="https://example.com",
            output_schema=SCHEMA,
            max_steps=max_steps,
            allowed_domains=["example.com"],
            timeout=600,
        )

    assert isinstance(response.structured, dict)
    assert "example" in str(response.structured.get("title", "")).lower()
    assert 0 < len(response.steps) <= max_steps


def test_an_empty_schema_answer_is_structured(provider: BrowserUseBrowseProvider) -> None:
    output = '{"title": "Example Domain"}'
    _stub(provider, states=[_status("finished", output)], task=_task("finished", output))

    response = provider.browse(BrowseRequest(task="t", output_schema={}))

    assert response.structured == {"title": "Example Domain"}
    assert response.answer is None
