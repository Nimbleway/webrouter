"""Guards on the job core, through a method that is not research.

`JobProvider` and `Job` are meant to carry any method whose backend runs a job,
so these build a second one here -- a tiny "chore" method with its own request,
progress, response, timeout error and messages, shipped nowhere -- and drive
the whole cycle through it: submit, poll, complete, time out, cancel, an
unrecognized status, and the budget a call inside the run is clamped to.

If one of these needs research to pass, the core is not generic yet.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest
from pydantic import BaseModel

from web_router.constants import MSG_JOB_FAILED, MSG_JOB_NOT_RESUMABLE
from web_router.exceptions import ProviderError, UnsupportedParameterError, WebRouterError
from web_router.providers import base
from web_router.providers._deadline import job_budget
from web_router.providers.base import Job, JobProvider
from web_router.types import JobState, NormalizedRequest

CALL_TIMEOUT = 60.0

STATES: dict[str, JobState] = {
    "WAITING": "queued",
    "BUSY": "running",
    "DONE": "completed",
    "EMPTY_HANDED": "completed",
    "BROKE": "failed",
    "GAVE_UP": "cancelled",
}


class ChoreRequest(NormalizedRequest):
    always_sent: ClassVar[frozenset[str]] = frozenset({"task", "provider_options"})

    task: str
    urgency: str | None = None


class ChoreResponse(BaseModel):
    provider: str
    task: str
    outcome: str


class ChoreProgress(BaseModel):
    job_id: str
    state: JobState
    response: ChoreResponse | None = None
    detail: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.state not in ("queued", "running")


class ChoreTimeoutError(WebRouterError):
    def __init__(self, provider: str, job_id: str, timeout: float) -> None:
        self.job_id = job_id
        super().__init__(f"chore {job_id} on {provider} outlived {timeout:g}s")


class ChoreJob(Job[ChoreRequest, ChoreProgress, ChoreResponse]):
    """Its own wait and cadence as constants, which is all a method should need to set."""

    default_timeout = 30.0
    poll_interval = 0.01
    poll_backoff = 2.0
    poll_max_interval = 0.04


class ChoreProvider(JobProvider[ChoreRequest, ChoreProgress, ChoreResponse]):
    """The method: what research is to `JobProvider`, in a dozen lines."""

    _failed_message = "chore failed: {error}"
    _not_resumable_message = "{provider} does chores in one go"

    def submit(self, request: ChoreRequest) -> ChoreJob:
        return ChoreJob(self, request, self._begin(request))

    async def asubmit(self, request: ChoreRequest) -> ChoreJob:
        return ChoreJob(self, request, await self._abegin(request))

    def do(self, request: ChoreRequest, *, timeout: float | None = 30.0) -> ChoreResponse:
        return self._run(request, timeout)

    async def ado(self, request: ChoreRequest, *, timeout: float | None = 30.0) -> ChoreResponse:
        return await self._arun(request, timeout)

    def _timed_out(self, job_id: str, timeout: float) -> WebRouterError:
        return ChoreTimeoutError(self.name, job_id, timeout)


class FakeChores(ChoreProvider):
    """A backend reporting in its own vocabulary, translated through `STATES`."""

    name = "chores"

    def __init__(self, *, reports: list[str], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.reports = list(reports)
        self.polls = 0
        self.cancelled: list[str] = []
        self.budgets: list[float] = []

    def _report(self, request: ChoreRequest) -> ChoreProgress:
        self.budgets.append(job_budget(CALL_TIMEOUT))
        reported = self.reports.pop(0) if len(self.reports) > 1 else self.reports[0]
        state = self.state_of(reported, STATES)
        response = ChoreResponse(provider=self.name, task=request.task, outcome="swept") if reported == "DONE" else None
        return ChoreProgress(job_id="chore-1", state=state, response=response, detail=f"backend said {reported}")

    def _start(self, request: ChoreRequest) -> ChoreProgress:
        return self._report(request)

    async def _astart(self, request: ChoreRequest) -> ChoreProgress:
        return self._report(request)

    def _fetch(self, request: ChoreRequest, job_id: str) -> ChoreProgress:
        self.polls += 1
        return self._report(request)

    def _cancel(self, job_id: str) -> bool:
        self.cancelled.append(job_id)
        return True


class _Clock:
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
    monkeypatch.setattr(base, "asyncio", SimpleNamespace(sleep=fake.asleep, gather=asyncio.gather))
    return fake


def _request(**options: Any) -> ChoreRequest:
    return ChoreRequest(task="sweep the porch", **options)


def test_a_job_is_submitted_polled_and_answered(clock: _Clock) -> None:
    provider = FakeChores(reports=["WAITING", "BUSY", "BUSY", "BUSY", "BUSY", "DONE"])

    job = provider.submit(_request())
    assert (job.state, job.job_id, job.provider) == ("queued", "chore-1", "chores")
    assert provider.polls == 0, "submitting must not wait"

    response = job.result()

    assert response == ChoreResponse(provider="chores", task="sweep the porch", outcome="swept")
    assert provider.polls == 5
    assert clock.slept == [0.01, 0.02, 0.04, 0.04], "the method's own cadence, not the default one"
    assert job.result() is response, "a finished job is not asked about again"
    assert provider.polls == 5
    assert repr(job) == "<ChoreJob provider='chores' job_id='chore-1' state='completed'>"


async def test_the_async_cycle_is_the_same(clock: _Clock) -> None:
    provider = FakeChores(reports=["BUSY"] * 8 + ["DONE"])

    response = await provider.ado(_request(), timeout=None)

    assert response.outcome == "swept"
    assert provider.polls == 8
    assert clock.slept[-1] == ChoreJob.poll_max_interval


@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
async def test_a_timeout_raises_the_methods_own_error_and_leaves_the_run(clock: _Clock, asynchronous: bool) -> None:
    provider = FakeChores(reports=["BUSY"])

    with pytest.raises(ChoreTimeoutError, match="outlived 10s") as caught:
        if asynchronous:
            await provider.ado(_request(), timeout=10.0)
        else:
            provider.do(_request(), timeout=10.0)

    assert caught.value.job_id == "chore-1"
    assert provider.cancelled == []
    assert sum(clock.slept) == pytest.approx(10.0), "waiting must stop at the timeout, not overshoot it"


async def test_cancelling_reaches_the_backend_only_while_the_run_is_live() -> None:
    running = FakeChores(reports=["BUSY"])
    finished = FakeChores(reports=["DONE"])

    assert running.submit(_request()).cancel() is True
    assert await (await running.asubmit(_request())).acancel() is True
    assert finished.submit(_request()).cancel() is False

    assert running.cancelled == ["chore-1", "chore-1"]
    assert finished.cancelled == []


@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
async def test_an_unrecognized_status_raises_rather_than_polling_forever(clock: _Clock, asynchronous: bool) -> None:
    provider = FakeChores(reports=["BUSY", "ON_STRIKE"])

    with pytest.raises(ProviderError, match="unrecognized run state 'ON_STRIKE'"):
        if asynchronous:
            await provider.ado(_request())
        else:
            provider.do(_request())


def test_the_methods_default_timeout_applies_when_the_caller_names_none(clock: _Clock) -> None:
    job = FakeChores(reports=["BUSY"]).submit(_request())

    with pytest.raises(ChoreTimeoutError, match="outlived 30s"):
        job.result()


def test_a_cancelled_run_says_so(clock: _Clock) -> None:
    with pytest.raises(ProviderError, match="cancelled before it answered"):
        FakeChores(reports=["BUSY", "GAVE_UP"]).do(_request())


async def test_a_completed_run_with_no_response_is_a_failure_not_an_empty_answer(clock: _Clock) -> None:
    with pytest.raises(ProviderError, match="the run failed: backend said EMPTY_HANDED"):
        await FakeChores(reports=["BUSY", "EMPTY_HANDED"]).ado(_request())


def test_a_failed_run_carries_the_backends_reason_and_failures_use_the_methods_message(clock: _Clock) -> None:
    with pytest.raises(ProviderError, match="backend said BROKE"):
        FakeChores(reports=["BROKE"]).do(_request())

    def _start(self: Any, request: ChoreRequest) -> ChoreProgress:
        raise RuntimeError("the broom snapped")

    exploding = type("Exploding", (FakeChores,), {"_start": _start})(reports=["DONE"])
    with pytest.raises(ProviderError, match="chore failed: the broom snapped") as caught:
        exploding.do(_request())
    assert isinstance(caught.value.__cause__, RuntimeError)


def test_an_unmapped_option_is_rejected_before_the_run_starts() -> None:
    provider = FakeChores(reports=["DONE"])

    with pytest.raises(UnsupportedParameterError, match="urgency"):
        provider.submit(_request(urgency="now"))
    assert provider.budgets == [], "nothing may be started for a request that was going to be refused"


def test_a_backend_that_cannot_resume_refuses_to_be_polled_in_the_methods_words() -> None:
    one_shot = type("OneShot", (FakeChores,), {"resumable": False, "_fetch": JobProvider._fetch})(reports=["DONE"])

    assert one_shot.do(_request()).outcome == "swept"
    with pytest.raises(ProviderError, match="chores does chores in one go"):
        one_shot.poll(_request(), "chore-1")


def test_the_budget_clamps_every_call_inside_the_run_down_never_up() -> None:
    short = FakeChores(reports=["DONE"])
    short.do(_request(), timeout=5)

    long = FakeChores(reports=["DONE"])
    long.do(_request(), timeout=600)

    unbounded = FakeChores(reports=["DONE"])
    unbounded.do(_request(), timeout=None)

    assert short.budgets == [pytest.approx(5, abs=0.5)]
    assert long.budgets == [pytest.approx(CALL_TIMEOUT, abs=0.5)]
    assert unbounded.budgets == [CALL_TIMEOUT]


def test_the_budget_reaches_every_poll_while_a_submitted_job_is_waited_on() -> None:
    """The budget set by result() -- not by submit() -- is what each poll is clamped to."""
    provider = FakeChores(reports=["WAITING", "BUSY", "DONE"])
    job = provider.submit(_request())

    job.result(timeout=5)

    assert provider.polls == 2
    assert provider.budgets[0] == CALL_TIMEOUT, "nobody was waiting when the run was submitted"
    assert provider.budgets[1:] == [pytest.approx(5, abs=0.5)] * 2


class _Forgetful(JobProvider[ChoreRequest, ChoreProgress, ChoreResponse]):
    """A method that set none of the messages -- the mistake the defaults exist for."""

    name = "forgetful"
    resumable = False

    def submit(self, request: ChoreRequest) -> Job[ChoreRequest, ChoreProgress, ChoreResponse]:
        return Job(self, request, self._begin(request))

    async def asubmit(self, request: ChoreRequest) -> Job[ChoreRequest, ChoreProgress, ChoreResponse]:
        return Job(self, request, await self._abegin(request))

    def _timed_out(self, job_id: str, timeout: float) -> WebRouterError:
        return ChoreTimeoutError(self.name, job_id, timeout)

    def _start(self, request: ChoreRequest) -> ChoreProgress:
        raise RuntimeError("the broom snapped")

    async def _astart(self, request: ChoreRequest) -> ChoreProgress:
        raise RuntimeError("the broom snapped")


async def test_a_method_that_names_no_messages_still_reports_the_failure() -> None:
    """Not an AttributeError from inside the error path, which would bury the backend's own."""
    provider = _Forgetful()

    with pytest.raises(ProviderError, match=MSG_JOB_FAILED.format(error="the broom snapped")) as caught:
        provider.submit(_request())
    assert isinstance(caught.value.__cause__, RuntimeError)

    with pytest.raises(ProviderError, match="job failed"):
        await provider.asubmit(_request())

    with pytest.raises(ProviderError) as refused:
        provider.poll(_request(), "chore-1")
    assert str(refused.value) == f"[forgetful] {MSG_JOB_NOT_RESUMABLE.format(provider='forgetful')}"


async def test_the_async_budget_reaches_the_call_that_starts_the_run() -> None:
    provider = FakeChores(reports=["DONE"])

    await provider.ado(_request(), timeout=5)

    assert provider.budgets == [pytest.approx(5, abs=0.5)]
    assert job_budget(CALL_TIMEOUT) == CALL_TIMEOUT, "the budget must not outlive the call that set it"


class StallingChores(FakeChores):
    """A backend whose status call stalls until the caller's budget runs out, as a clamped read timeout does."""

    def __init__(self, clock: _Clock, *, stall_from_poll: int, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.clock = clock
        self.stall_from_poll = stall_from_poll

    def _fetch(self, request: ChoreRequest, job_id: str) -> ChoreProgress:
        self.polls += 1
        if self.polls >= self.stall_from_poll:
            # The request was clamped to what the caller had left, so it fails as that runs out.
            self.clock.now += CALL_TIMEOUT
            raise TimeoutError("read timed out")
        return self._report(request)


@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
async def test_a_poll_that_stalls_past_the_deadline_is_a_timeout_not_a_failure(
    clock: _Clock, asynchronous: bool
) -> None:
    """The timeout carries the job id, so a run still going -- and billing -- can be come back to."""
    provider = StallingChores(clock, stall_from_poll=2, reports=["BUSY"])

    with pytest.raises(ChoreTimeoutError, match="outlived 10s") as caught:
        if asynchronous:
            await provider.ado(_request(), timeout=10.0)
        else:
            provider.do(_request(), timeout=10.0)

    assert caught.value.job_id == "chore-1"
    assert isinstance(caught.value.__cause__, ProviderError)
    assert "read timed out" in str(caught.value.__cause__)


def test_a_poll_that_fails_with_time_left_is_still_the_backends_failure(clock: _Clock) -> None:
    class Failing(FakeChores):
        def _fetch(self, request: ChoreRequest, job_id: str) -> ChoreProgress:
            raise ConnectionError("connection reset")

    with pytest.raises(ProviderError, match="connection reset"):
        Failing(reports=["BUSY"]).do(_request(), timeout=10.0)


@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
async def test_a_poll_that_fails_for_another_reason_after_the_deadline_is_still_that_failure(
    clock: _Clock, asynchronous: bool
) -> None:
    """Only a request that timed out becomes the method's timeout; a refused key late in the wait stays a failure."""

    class Refusing(StallingChores):
        def _fetch(self, request: ChoreRequest, job_id: str) -> ChoreProgress:
            self.polls += 1
            if self.polls >= self.stall_from_poll:
                self.clock.now += CALL_TIMEOUT
                raise PermissionError("401 invalid api key")
            return self._report(request)

    provider = Refusing(clock, stall_from_poll=2, reports=["BUSY"])

    with pytest.raises(ProviderError, match="invalid api key"):
        if asynchronous:
            await provider.ado(_request(), timeout=10.0)
        else:
            provider.do(_request(), timeout=10.0)


@pytest.mark.parametrize(
    "error",
    [
        type("ReadTimeout", (type("TimeoutException", (Exception,), {}),), {})("httpx read timeout"),
        type("APITimeoutError", (Exception,), {})("Request timed out."),
    ],
    ids=["httpx-shaped", "sdk-shaped"],
)
def test_a_timeout_is_recognized_whichever_library_raised_it(clock: _Clock, error: Exception) -> None:
    class Stalling(StallingChores):
        def _fetch(self, request: ChoreRequest, job_id: str) -> ChoreProgress:
            self.polls += 1
            if self.polls >= self.stall_from_poll:
                self.clock.now += CALL_TIMEOUT
                raise error
            return self._report(request)

    with pytest.raises(ChoreTimeoutError):
        Stalling(clock, stall_from_poll=2, reports=["BUSY"]).do(_request(), timeout=10.0)
