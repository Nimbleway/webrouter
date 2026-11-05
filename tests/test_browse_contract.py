"""Guards on the browse contract itself.

Browse is the second method on the job core: a hosted browser agent takes a
task, works through it for minutes, and answers when it is done. The waiting is
the core's and research's contract tests pin it in detail, so these pin what
browse adds on top -- the start_url a backend may need, the schema a backend may
refuse, the step list and payload a task answers with -- and prove the core's
submit, poll, result, cancel and timeout reach browse unchanged, sync and async.

The registry-wide guards run over every registered browse backend plus the
conftest fake, so a new backend is covered without this module being edited.

Nothing here sleeps: the clock inside `providers.base` is swapped for a fake.
"""

from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from tests.conftest import FakeBrowseProvider
from web_router import AsyncWebRouter, WebRouter
from web_router.constants import BROWSE_ENV_BY_PROVIDER, BROWSE_TIMEOUT
from web_router.exceptions import (
    BrowseTimeoutError,
    MissingParameterError,
    ProviderError,
    ProviderNotFoundError,
    UnsupportedParameterError,
)
from web_router.providers import base
from web_router.providers.base import BrowseJob, BrowseProvider
from web_router.registry import (
    available_browse_providers,
    configured_browse_providers,
    get_browse_provider_class,
)
from web_router.types import BROWSE_ALWAYS_SENT, BROWSE_PARAMETERS, BrowseRequest, BrowseResponse, BrowseStep

ROUTER_ONLY_ARGUMENTS = {"self", "task", "provider", "preset"}

PUBLIC_METHODS = (
    "submit",
    "asubmit",
    "browse",
    "abrowse",
    "poll",
    "apoll",
    "cancel",
    "acancel",
    "validate",
)
"""Everything a caller reaches, all of which enforce something on the backend's behalf."""

TASK = "find the cheapest nonstop flight from SFO to JFK next friday"
START_URL = "https://www.google.com/flights"

PROVIDER_CLASSES = [FakeBrowseProvider, *(get_browse_provider_class(name) for name in available_browse_providers())]


def _request(**options: Any) -> BrowseRequest:
    return BrowseRequest(task=TASK, **options)


def _fake(**overrides: Any) -> Any:
    """A fake browse backend with one behaviour swapped out."""
    return type("Swapped", (FakeBrowseProvider,), overrides)()


def _one_shot(**overrides: Any) -> Any:
    """A backend whose task call blocks until the agent is done."""
    return _fake(resumable=False, _fetch=BrowseProvider._fetch, _afetch=BrowseProvider._afetch, **overrides)


class _Clock:
    """Stands in for `time` inside providers.base, so waiting costs no wall time."""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds

    async def asleep(self, seconds: float) -> None:
        self.sleep(seconds)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    fake = _Clock()
    monkeypatch.setattr(base, "time", fake)
    monkeypatch.setattr(
        base,
        "asyncio",
        SimpleNamespace(sleep=fake.asleep, gather=asyncio.gather, Semaphore=asyncio.Semaphore),
    )
    return fake


# -- The shared surface ------------------------------------------------------


@pytest.mark.parametrize("method", [WebRouter.browse, AsyncWebRouter.browse], ids=["sync", "async"])
def test_router_browse_exposes_every_shared_option(method: object) -> None:
    """The two browse signatures restate BrowseRequest's fields, so guard the copies."""
    exposed = set(inspect.signature(method).parameters) - ROUTER_ONLY_ARGUMENTS - {"timeout"}
    assert exposed == BROWSE_PARAMETERS | BROWSE_ALWAYS_SENT - {"task"}


@pytest.mark.parametrize("method", [WebRouter.submit_browse, AsyncWebRouter.submit_browse], ids=["sync", "async"])
def test_submitting_takes_the_same_request_minus_the_wait(method: object) -> None:
    exposed = set(inspect.signature(method).parameters) - ROUTER_ONLY_ARGUMENTS
    assert exposed == BROWSE_PARAMETERS | BROWSE_ALWAYS_SENT - {"task"}


def test_the_request_carries_no_model_choice() -> None:
    """One vendor's agent model is not comparable to another's, so it is a provider option."""
    assert "model" not in BrowseRequest.model_fields


def test_every_provider_declares_where_its_credentials_come_from() -> None:
    assert set(BROWSE_ENV_BY_PROVIDER) == set(available_browse_providers()) - {"fake"}


def test_no_browse_backend_is_usable_without_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every task vendor bills for the run, so an empty environment configures only the chooser.

    `auto` is listed as it is for research: with nothing to choose from, it is
    the one that says which variables would fix that.
    """
    for env_vars in BROWSE_ENV_BY_PROVIDER.values():
        for env_var in env_vars:
            monkeypatch.delenv(env_var, raising=False)

    assert configured_browse_providers() == ["auto"]


def test_a_browse_backend_is_unknown_to_the_other_methods(fake_browse_provider: type) -> None:
    """Each method has its own registry; a browse backend does not research."""
    router = WebRouter("fake")

    assert isinstance(router.get_browse_provider(), fake_browse_provider)
    with pytest.raises(ProviderNotFoundError):
        router.get_research_provider()


@pytest.mark.parametrize("provider_cls", PROVIDER_CLASSES, ids=lambda cls: cls.name)
def test_public_browse_methods_are_always_the_base_implementation(provider_cls: type[BrowseProvider]) -> None:
    """Validation, error wrapping and the whole wait-and-poll cycle live on the base class."""
    for method in PUBLIC_METHODS:
        assert getattr(provider_cls, method) is getattr(BrowseProvider, method), (
            f"{provider_cls.name} overrides {method}() and would skip what the base class enforces"
        )


@pytest.mark.parametrize("provider_cls", PROVIDER_CLASSES, ids=lambda cls: cls.name)
def test_providers_declare_only_real_options(provider_cls: type[BrowseProvider]) -> None:
    """A required option the backend does not also map could never be sent."""
    assert provider_cls.supported_parameters <= BROWSE_PARAMETERS
    assert provider_cls.required_parameters <= provider_cls.supported_parameters
    assert not inspect.isabstract(provider_cls)


# -- What BrowseProvider guarantees on every backend's behalf ----------------


def test_an_option_the_backend_does_not_map_is_rejected_not_dropped() -> None:
    provider = _fake(supported_parameters=frozenset({"start_url"}))

    provider.browse(_request(start_url=START_URL))

    with pytest.raises(UnsupportedParameterError, match="max_steps"):
        provider.browse(_request(max_steps=10))
    assert len(provider.requests) == 1


def test_output_schema_is_refused_by_a_backend_with_no_structured_output() -> None:
    """Prose under a schema's name is the one answer a caller could not tell was wrong."""
    provider = _fake(supported_parameters=frozenset({"start_url"}))

    with pytest.raises(UnsupportedParameterError, match="output_schema"):
        provider.browse(_request(output_schema={"type": "object"}))

    assert provider.requests == [], "the task must be refused before it is started"


def test_a_missing_start_url_raises_before_any_task_starts() -> None:
    """Folding "start at X" into the task would rewrite the caller's words for one backend."""
    provider = _fake(required_parameters=frozenset({"start_url"}))

    with pytest.raises(MissingParameterError, match="start_url") as caught:
        provider.submit(_request())

    assert caught.value.provider == "fake"
    assert caught.value.parameters == ["start_url"]
    assert provider.requests == [], "nothing may be billed for a task that could never start"

    assert provider.browse(_request(start_url=START_URL)).answer == "the answer"


def test_the_start_url_check_can_be_asked_of_the_class_without_building_it() -> None:
    """One check for validate() and for a chooser that must rule a backend out before paying for it."""
    needs_url = type("NeedsUrl", (FakeBrowseProvider,), {"required_parameters": frozenset({"start_url"})})

    assert needs_url.missing_parameters(_request()) == {"start_url"}
    assert needs_url.missing_parameters(_request(start_url=START_URL)) == frozenset()
    assert FakeBrowseProvider.missing_parameters(_request()) == frozenset()


def test_an_empty_domain_list_is_refused_rather_than_read_two_ways() -> None:
    """ "Allow nothing" to one backend and "allow anything" to another; neither is what was meant."""
    with pytest.raises(ValidationError, match="allowed_domains"):
        _request(allowed_domains=[])
    with pytest.raises(ValidationError):
        _request(allowed_domains=[""])

    assert _request(allowed_domains=["google.com"]).allowed_domains == ["google.com"]


def test_a_step_carries_no_number_the_backend_did_not_give() -> None:
    """List order is the order; a backend's own numbering is kept in extra, not invented."""
    assert "index" not in BrowseStep.model_fields
    assert "live_view_url" not in BrowseResponse.model_fields, "a control URL must not sit in a plain field"


def test_partial_is_part_of_the_type_but_never_the_default() -> None:
    assert BrowseResponse(provider="fake", task=TASK).outcome == "complete"
    assert BrowseResponse(provider="fake", task=TASK, outcome="partial").outcome == "partial"


async def test_a_missing_start_url_raises_on_the_async_path_too() -> None:
    provider = _fake(required_parameters=frozenset({"start_url"}))

    with pytest.raises(MissingParameterError):
        await provider.asubmit(_request())
    assert provider.requests == []


def test_whatever_a_backend_raises_is_wrapped() -> None:
    def _start(self: Any, request: BrowseRequest) -> Any:
        raise RuntimeError("upstream exploded")

    with pytest.raises(ProviderError, match="browse task failed: upstream exploded") as caught:
        _fake(_start=_start).browse(_request())

    assert isinstance(caught.value.__cause__, RuntimeError)


# -- The job: submit, poll, result -------------------------------------------


def test_submitting_a_task_does_not_wait_for_it(clock: _Clock) -> None:
    provider = _fake(polls_before_done=3)

    job = provider.submit(_request())

    assert isinstance(job, BrowseJob)
    assert (job.state, job.job_id, job.provider) == ("running", "task-1", "fake")
    assert provider.polls == 0 and clock.slept == []


def test_polling_reports_where_the_task_is_until_it_finishes(clock: _Clock) -> None:
    provider = _fake(polls_before_done=2)
    job = provider.submit(_request())

    assert job.status().state == "running"
    finished = job.status()

    assert finished.state == "completed"
    assert finished.response is not None and finished.response.answer == "the answer"
    assert provider.polls == 2


def test_the_result_waits_on_the_cores_schedule_and_is_kept(clock: _Clock) -> None:
    provider = _fake(polls_before_done=4)
    job = provider.submit(_request())

    first = job.result()
    polled = provider.polls

    assert first.answer == "the answer"
    assert clock.slept == sorted(clock.slept) and len(clock.slept) == 3
    assert job.result() is first
    assert provider.polls == polled, "a finished task must not be polled again"


async def test_the_async_job_submits_polls_and_answers_the_same_way(clock: _Clock) -> None:
    provider = _fake(polls_before_done=2)
    job = await provider.asubmit(_request())

    assert (await job.astatus()).state == "running"
    first = await job.aresult()

    assert first.answer == "the answer"
    assert await job.aresult() is first
    assert provider.polls == 2


def test_the_answer_is_the_normalized_response(clock: _Clock) -> None:
    provider = _fake(polls_before_done=1)

    response = provider.browse(_request(start_url=START_URL, output_schema={"type": "object"}))

    assert isinstance(response, BrowseResponse)
    assert (response.provider, response.task, response.request_id) == ("fake", TASK, "task-1")
    assert response.structured == {"ok": True}
    assert [step.url for step in response.steps] == [START_URL]
    assert response.outcome == "complete"


def test_the_raw_payload_never_reaches_a_log_line_or_a_dump(clock: _Clock) -> None:
    """A task payload can carry the URL of the session the agent ran in, which is a credential."""
    response = _fake().browse(_request())

    assert response.raw is not None and "SEKRIT" in str(response.raw)
    assert "SEKRIT" not in repr(response)
    assert "raw" not in response.model_dump()
    assert "SEKRIT" not in response.model_dump_json()


def test_a_status_the_table_does_not_list_raises_rather_than_polling_forever(clock: _Clock) -> None:
    provider = _fake(polls_before_done=1)
    job = provider.submit(_request())
    provider.reported = "astonished"

    with pytest.raises(ProviderError, match="unrecognized run state 'astonished'"):
        job.result()


async def test_an_unknown_status_raises_on_the_async_path_too(clock: _Clock) -> None:
    with pytest.raises(ProviderError, match="unrecognized run state"):
        await _fake(reported="astonished").abrowse(_request())


def test_a_failed_task_carries_the_backends_own_reason(clock: _Clock) -> None:
    with pytest.raises(ProviderError, match="the agent is failed"):
        _fake(reported="FAILED").browse(_request())


def test_a_timeout_raises_and_leaves_the_task_going(clock: _Clock) -> None:
    provider = _fake(polls_before_done=1000)

    with pytest.raises(BrowseTimeoutError) as caught:
        provider.browse(_request(), timeout=10.0)

    assert (caught.value.provider, caught.value.job_id) == ("fake", "task-1")
    assert "submit_browse()" in str(caught.value)
    assert provider.cancelled == [], "a timeout must not cancel the task"
    assert sum(clock.slept) <= 10.0


async def test_the_async_timeout_behaves_identically(clock: _Clock) -> None:
    provider = _fake(polls_before_done=1000)

    with pytest.raises(BrowseTimeoutError):
        await provider.abrowse(_request(), timeout=10.0)
    assert provider.cancelled == []


def test_a_status_poll_that_stalls_past_the_deadline_is_a_browse_timeout(clock: _Clock) -> None:
    """Browse builds its own timeout, so the job id must survive a stalled poll here too."""

    def stall(self: Any, request: BrowseRequest, job_id: str) -> Any:
        # The request was clamped to what the caller had left, so it fails as that runs out.
        clock.now += 10.0
        raise TimeoutError("read timed out")

    provider = _fake(polls_before_done=1000, _fetch=stall)

    with pytest.raises(BrowseTimeoutError) as caught:
        provider.browse(_request(), timeout=10.0)

    assert caught.value.job_id == "task-1"
    assert isinstance(caught.value.__cause__, ProviderError)


@pytest.mark.parametrize(
    "method",
    [WebRouter.browse, AsyncWebRouter.browse, BrowseProvider.browse, BrowseJob.result, BrowseJob.aresult],
)
def test_an_unset_timeout_visibly_waits_the_browse_default(method: object) -> None:
    """The default is read off the signature by editors and docs, so it must be the number, not a sentinel."""
    assert BrowseJob.default_timeout == BROWSE_TIMEOUT
    assert inspect.signature(method).parameters["timeout"].default == BROWSE_TIMEOUT


# -- Cancelling --------------------------------------------------------------


def test_cancelling_a_running_task_reaches_the_backend() -> None:
    provider = _fake(polls_before_done=3)

    assert provider.submit(_request()).cancel() is True
    assert provider.cancelled == ["task-1"]


def test_cancelling_a_finished_task_never_reaches_the_backend() -> None:
    provider = _fake()

    assert provider.submit(_request()).cancel() is False
    assert provider.cancelled == []


async def test_cancelling_behaves_the_same_on_the_async_path() -> None:
    running = _fake(polls_before_done=3)

    assert await (await running.asubmit(_request())).acancel() is True
    assert running.cancelled == ["task-1"]


def test_a_backend_that_cannot_cancel_says_so_rather_than_pretending() -> None:
    provider = _fake(polls_before_done=3, _cancel=BrowseProvider._cancel)

    assert provider.submit(_request()).cancel() is False


def test_a_task_the_backend_stopped_reads_as_cancelled(clock: _Clock) -> None:
    with pytest.raises(ProviderError, match="cancelled"):
        _fake(reported="STOPPED").browse(_request())


# -- Backends whose task call blocks until done ------------------------------


def test_a_blocking_backend_still_browses(clock: _Clock) -> None:
    provider = _one_shot()

    assert provider.browse(_request()).answer == "the answer"
    assert clock.slept == []


def test_a_task_that_cannot_be_resumed_refuses_to_be_polled() -> None:
    provider = _one_shot()
    job = provider.submit(_request())

    assert job.resumable is False
    with pytest.raises(ProviderError, match="Call browse\\(\\) and let it wait"):
        provider.poll(_request(), job.job_id)


async def test_a_task_that_cannot_be_resumed_refuses_the_async_poll_as_well() -> None:
    provider = _one_shot()
    job = await provider.asubmit(_request())

    with pytest.raises(ProviderError, match="answers inside the request"):
        await provider.apoll(_request(), job.job_id)


# -- Through the routers -----------------------------------------------------


def test_the_router_builds_the_request_and_waits(fake_browse_provider: type, clock: _Clock) -> None:
    from pydantic import BaseModel

    class Flight(BaseModel):
        price: float

    with WebRouter("fake") as router:
        response = router.browse(TASK, start_url=START_URL, output_schema=Flight, max_steps=20)
        sent = router.get_browse_provider().requests[-1]

    assert response.answer == "the answer"
    assert sent.output_schema == Flight.model_json_schema()
    assert (sent.start_url, sent.max_steps, sent.allowed_domains) == (START_URL, 20, None)


def test_the_router_hands_back_a_job_without_waiting(fake_browse_provider: type, clock: _Clock) -> None:
    router = WebRouter("fake")
    router.get_browse_provider().polls_before_done = 2

    job = router.submit_browse(TASK, allowed_domains=["google.com"])

    assert job.state == "running" and clock.slept == []
    assert job.result().answer == "the answer"


async def test_the_async_router_browses_and_submits(fake_browse_provider: type, clock: _Clock) -> None:
    async with AsyncWebRouter("fake") as router:
        response = await router.browse(TASK, start_url=START_URL)
        job = await router.submit_browse(TASK)
        provider = router.get_browse_provider()

    assert response.answer == "the answer"
    assert (await job.aresult()).answer == "the answer"
    assert provider.closed, "closing the router closes the browse providers it built"
