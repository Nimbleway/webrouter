"""Guards on the research contract itself.

The third counterpart of test_contract.py. Research is the method that is a
*job*: the backend accepts a question, works for minutes, and answers when it
is done. So beside the usual drift guards -- a provider that skipped
validation, a table the registry has outgrown -- these pin the waiting itself:
what gets polled and how often, what a timeout does to a run that is still
going, and what a finished run that carries no answer must not be read as.

Nothing here sleeps: the clock inside `providers.base` is swapped for a fake
one, so a backoff schedule that would take half a minute costs nothing and can
be asserted on directly.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from types import SimpleNamespace
from typing import Any

import pytest

from web_router import AsyncWebRouter, WebRouter
from web_router.constants import (
    RESEARCH_ENV_BY_PROVIDER,
    RESEARCH_HTTP_TIMEOUT,
    RESEARCH_POLL_BACKOFF,
    RESEARCH_POLL_INTERVAL,
    RESEARCH_POLL_MAX_INTERVAL,
)
from web_router.exceptions import ProviderError, ResearchTimeoutError, UnsupportedParameterError
from web_router.providers import base
from web_router.providers._deadline import research_budget
from web_router.providers.base import ResearchProvider
from web_router.registry import (
    available_research_providers,
    configured_research_providers,
    get_research_provider_class,
)
from web_router.types import (
    RESEARCH_ALWAYS_SENT,
    RESEARCH_PARAMETERS,
    ResearchProgress,
    ResearchRequest,
    ResearchResponse,
)

ROUTER_ONLY_ARGUMENTS = {"self", "query", "provider", "budget"}

ENV_VARS = sorted({env_var for env_vars in RESEARCH_ENV_BY_PROVIDER.values() for env_var in env_vars})

PUBLIC_METHODS = (
    "submit",
    "asubmit",
    "research",
    "aresearch",
    "poll",
    "apoll",
    "cancel",
    "acancel",
    "validate",
)
"""Everything a caller reaches, all of which enforce something on the backend's behalf."""

QUERY = "how did the 2022 world cup final end"


def _request(**options: Any) -> ResearchRequest:
    return ResearchRequest(query=QUERY, **options)


def _fake(**overrides: Any) -> Any:
    """A fake research backend with one behaviour swapped out."""
    from tests.conftest import FakeResearchProvider

    return type("Swapped", (FakeResearchProvider,), overrides)()


def _one_shot(**overrides: Any) -> Any:
    """A backend that answers inside the request that started it, as Perplexity's does."""
    return _fake(
        resumable=False,
        _fetch=ResearchProvider._fetch,
        _afetch=ResearchProvider._afetch,
        **overrides,
    )


class _Clock:
    """Stands in for `time` inside providers.base, so waiting costs no wall time.

    Sleeping moves the clock rather than the test, which is what makes the poll
    schedule and the timeout assertable instead of merely slow.
    """

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


@pytest.mark.parametrize("method", [WebRouter.research, AsyncWebRouter.research], ids=["sync", "async"])
def test_router_research_exposes_every_shared_option(method: object) -> None:
    """The two research signatures restate ResearchRequest's fields, so guard the copies."""
    exposed = set(inspect.signature(method).parameters) - ROUTER_ONLY_ARGUMENTS - {"timeout"}
    assert exposed == RESEARCH_PARAMETERS | RESEARCH_ALWAYS_SENT - {"query"}


@pytest.mark.parametrize("method", [WebRouter.submit_research, AsyncWebRouter.submit_research], ids=["sync", "async"])
def test_submitting_takes_the_same_request_minus_the_wait(method: object) -> None:
    """submit_research() differs from research() in one thing only: it does not wait."""
    exposed = set(inspect.signature(method).parameters) - ROUTER_ONLY_ARGUMENTS
    assert exposed == RESEARCH_PARAMETERS | RESEARCH_ALWAYS_SENT - {"query"}


def test_research_parameters_tracks_the_model() -> None:
    assert set(ResearchRequest.model_fields) - RESEARCH_ALWAYS_SENT == RESEARCH_PARAMETERS


def test_every_provider_declares_where_its_credentials_come_from() -> None:
    """configured_research_providers() reads this table, so a gap there hides a provider."""
    assert set(RESEARCH_ENV_BY_PROVIDER) == set(available_research_providers())


def test_no_research_backend_is_usable_without_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """The one method with no keyless tier: every backend bills for the run.

    `auto` is the exception only in that it is the chooser, and with nothing to
    choose from it is the one that says which variables would fix that.
    """
    for env_var in ENV_VARS:
        monkeypatch.delenv(env_var, raising=False)

    assert configured_research_providers() == ["auto"]

    monkeypatch.setenv("LINKUP_API_KEY", "test-key")

    assert "linkup" in configured_research_providers()
    assert set(configured_research_providers()) <= set(available_research_providers())


def test_configured_reads_the_environment_and_nothing_else(monkeypatch: pytest.MonkeyPatch) -> None:
    """A key passed as provider_config is deliberately invisible here."""
    for env_var in ENV_VARS:
        monkeypatch.delenv(env_var, raising=False)
    WebRouter("nimble", provider_config={"nimble": {"api_key": "secret"}})

    assert "nimble" not in configured_research_providers()


@pytest.mark.parametrize("name", available_research_providers())
def test_registered_providers_are_research_providers(name: str) -> None:
    provider_cls = get_research_provider_class(name)

    assert issubclass(provider_cls, ResearchProvider)
    assert provider_cls.name == name, f"{name} answers to a name it does not declare"


@pytest.mark.parametrize("name", available_research_providers())
def test_providers_declare_only_real_options(name: str) -> None:
    """A typo in supported_parameters would silently reject a valid option forever."""
    assert get_research_provider_class(name).supported_parameters <= RESEARCH_PARAMETERS


@pytest.mark.parametrize("name", available_research_providers())
def test_public_research_methods_are_always_the_base_implementation(name: str) -> None:
    """Validation, error wrapping and the whole wait-and-poll cycle live on the base class.

    A provider that overrode any of these -- directly or through an
    intermediate base like `HttpResearchProvider` -- would bypass them.
    Comparing the bound function catches that however deep the hierarchy gets.
    """
    provider_cls = get_research_provider_class(name)
    for method in PUBLIC_METHODS:
        assert getattr(provider_cls, method) is getattr(ResearchProvider, method), (
            f"{name} overrides {method}() and would skip what the base class enforces"
        )


@pytest.mark.parametrize("name", available_research_providers())
def test_providers_are_concrete(name: str) -> None:
    assert not inspect.isabstract(get_research_provider_class(name))


@pytest.mark.parametrize("name", available_research_providers())
def test_provider_repr_never_leaks_a_credential(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "SEKRIT-0123456789abcdef"
    for env_var in ENV_VARS:
        monkeypatch.setenv(env_var, secret)

    provider = get_research_provider_class(name)(api_key=secret)
    try:
        assert secret not in repr(provider)
        assert secret not in repr(provider.options)
    finally:
        provider.close()


# -- What ResearchProvider guarantees on every backend's behalf --------------


def test_an_option_the_backend_does_not_map_is_rejected_not_dropped() -> None:
    provider = _fake(supported_parameters=frozenset({"effort"}))

    provider.research(_request(effort="low"))  # mapped, so this is fine

    with pytest.raises(UnsupportedParameterError, match="include_domains"):
        provider.research(_request(include_domains=["example.com"]))


def test_output_schema_is_rejected_like_any_other_unmapped_option() -> None:
    """A backend with no structured output says so rather than answering in prose.

    Deliberately not a special case: asking for a schema from a backend that
    does not declare one fails in the same sentence as asking a search backend
    to geo-target.
    """
    provider = _fake(supported_parameters=frozenset({"effort"}))

    with pytest.raises(UnsupportedParameterError, match="output_schema"):
        provider.research(_request(output_schema={"type": "object"}))

    assert provider.requests == [], "the run must be rejected before it is started, not after"


def test_whatever_a_backend_raises_is_wrapped() -> None:
    def _start(self: Any, request: ResearchRequest) -> ResearchProgress:
        raise RuntimeError("upstream exploded")

    provider = _fake(_start=_start)
    with pytest.raises(ProviderError, match="upstream exploded") as caught:
        provider.research(_request())

    assert isinstance(caught.value.original, RuntimeError)
    assert isinstance(caught.value.__cause__, RuntimeError)


async def test_whatever_a_backend_raises_asynchronously_is_wrapped_too() -> None:
    async def _astart(self: Any, request: ResearchRequest) -> ResearchProgress:
        raise RuntimeError("upstream exploded")

    provider = _fake(_astart=_astart)
    with pytest.raises(ProviderError, match="upstream exploded") as caught:
        await provider.aresearch(_request())

    assert isinstance(caught.value.__cause__, RuntimeError)


def test_a_failure_while_polling_is_wrapped_as_well(clock: _Clock) -> None:
    """The waiting is the base class's, so a backend that breaks mid-run fails alike."""

    def _fetch(self: Any, request: ResearchRequest, job_id: str) -> ResearchProgress:
        raise RuntimeError("the status endpoint 500ed")

    provider = _fake(polls_before_done=3, _fetch=_fetch)
    with pytest.raises(ProviderError, match="the status endpoint 500ed"):
        provider.research(_request())


def test_a_router_error_passes_through_unwrapped() -> None:
    """A web-router error is the answer already; rewrapping it would bury it."""

    def _start(self: Any, request: ResearchRequest) -> ResearchProgress:
        raise UnsupportedParameterError("fake", ["effort"])

    provider = _fake(_start=_start)
    with pytest.raises(UnsupportedParameterError):
        provider.research(_request())


def test_state_of_translates_a_status_through_the_backends_own_table() -> None:
    assert _fake().state_of("FINISHED", {"FINISHED": "completed"}) == "completed"


def test_state_of_raises_on_a_status_it_does_not_know() -> None:
    """A backend that changed under us must not be read as "still running" forever."""
    with pytest.raises(ProviderError, match="unrecognized run state"):
        _fake().state_of("astonished", {"FINISHED": "completed"})


# -- The job: starting, waiting, finishing -----------------------------------


def test_a_run_that_finishes_inside_start_is_never_polled(clock: _Clock) -> None:
    provider = _fake()

    response = provider.research(_request())

    assert response.answer == "the answer"
    assert provider.polls == 0, "the answer was already in hand"
    assert clock.slept == []


async def test_a_run_that_finishes_inside_start_is_never_polled_asynchronously(clock: _Clock) -> None:
    provider = _fake()

    response = await provider.aresearch(_request())

    assert response.answer == "the answer"
    assert provider.polls == 0
    assert clock.slept == []


def test_submitting_a_run_does_not_wait_for_it(clock: _Clock) -> None:
    provider = _fake(polls_before_done=3)

    job = provider.submit(_request())

    assert job.state == "running"
    assert job.job_id == "job-1"
    assert job.provider == "fake"
    assert provider.polls == 0 and clock.slept == []


def test_a_run_that_takes_several_polls_answers_and_backs_off(clock: _Clock) -> None:
    """The gap between polls grows and then stops growing.

    An hour-long run polled every two seconds is two thousand requests for one
    answer; a ceiling keeps a run that finishes late from being noticed late.
    """
    provider = _fake(polls_before_done=8)

    response = provider.research(_request())

    assert response.answer == "the answer"
    assert provider.polls == 8
    assert clock.slept[0] == RESEARCH_POLL_INTERVAL
    assert clock.slept[1] == RESEARCH_POLL_INTERVAL * RESEARCH_POLL_BACKOFF
    assert clock.slept == sorted(clock.slept), f"the gap between polls shrank: {clock.slept}"
    assert clock.slept[-1] == clock.slept[-2] == RESEARCH_POLL_MAX_INTERVAL


async def test_the_async_path_waits_on_the_same_schedule(clock: _Clock) -> None:
    provider = _fake(polls_before_done=8)

    response = await provider.aresearch(_request())

    assert response.answer == "the answer"
    assert clock.slept[0] == RESEARCH_POLL_INTERVAL
    assert clock.slept == sorted(clock.slept)
    assert clock.slept[-1] == RESEARCH_POLL_MAX_INTERVAL


def test_the_answer_is_kept_rather_than_asked_for_again(clock: _Clock) -> None:
    provider = _fake(polls_before_done=2)
    job = provider.submit(_request())

    first = job.result()
    polled = provider.polls

    assert job.result() is first
    assert job.status().state == "completed"
    assert provider.polls == polled, "a finished run must not be polled again"


async def test_the_async_answer_is_kept_too(clock: _Clock) -> None:
    provider = _fake(polls_before_done=2)
    job = await provider.asubmit(_request())

    first = await job.aresult()
    polled = provider.polls

    assert await job.aresult() is first
    assert (await job.astatus()).state == "completed"
    assert provider.polls == polled


def test_a_timeout_raises_and_leaves_the_run_going(clock: _Clock) -> None:
    """Giving up on the wait is not giving up on the run, which is still being paid for."""
    provider = _fake(polls_before_done=1000)

    with pytest.raises(ResearchTimeoutError) as caught:
        provider.research(_request(), timeout=10.0)

    assert caught.value.provider == "fake"
    assert caught.value.job_id == "job-1"
    assert "poll the job yourself" in str(caught.value)
    assert provider.cancelled == [], "a timeout must not cancel the run"
    assert sum(clock.slept) <= 10.0, "waiting must not overshoot the timeout"


async def test_the_async_timeout_behaves_identically(clock: _Clock) -> None:
    provider = _fake(polls_before_done=1000)

    with pytest.raises(ResearchTimeoutError) as caught:
        await provider.aresearch(_request(), timeout=10.0)

    assert caught.value.job_id == "job-1"
    assert provider.cancelled == []
    assert sum(clock.slept) <= 10.0


def test_a_failed_run_carries_the_backends_own_reason(clock: _Clock) -> None:
    provider = _fake(fails_with="the account ran out of credit")

    with pytest.raises(ProviderError, match="the account ran out of credit"):
        provider.research(_request())


def test_a_cancelled_run_says_it_was_cancelled(clock: _Clock) -> None:
    """Distinct from a failure: nothing went wrong, somebody stopped it."""

    def _start(self: Any, request: ResearchRequest) -> ResearchProgress:
        return ResearchProgress(job_id="job-1", state="cancelled")

    provider = _fake(_start=_start)
    with pytest.raises(ProviderError, match="cancelled"):
        provider.research(_request())


def test_a_completed_run_with_no_answer_attached_raises(clock: _Clock) -> None:
    """A provider bug, and the one shape a caller could not tell from a thin answer."""

    def _start(self: Any, request: ResearchRequest) -> ResearchProgress:
        return ResearchProgress(job_id="job-1", state="completed")

    provider = _fake(_start=_start)
    with pytest.raises(ProviderError, match="the run failed"):
        provider.research(_request())


async def test_a_completed_run_with_no_answer_raises_on_the_async_path_too(clock: _Clock) -> None:
    async def _astart(self: Any, request: ResearchRequest) -> ResearchProgress:
        return ResearchProgress(job_id="job-1", state="completed")

    provider = _fake(_astart=_astart)
    with pytest.raises(ProviderError, match="the run failed"):
        await provider.aresearch(_request())


# -- Backends that answer in one request -------------------------------------


def test_a_backend_that_answers_in_one_request_still_researches(clock: _Clock) -> None:
    provider = _one_shot()

    response = provider.research(_request())

    assert response.answer == "the answer"
    assert clock.slept == [], "there was never anything to wait for"


def test_a_run_that_cannot_be_resumed_refuses_to_be_polled(clock: _Clock) -> None:
    """Refusing names the provider, because the fix is to pick another one."""
    provider = _one_shot()
    job = provider.submit(_request())

    assert job.resumable is False
    with pytest.raises(ProviderError, match="answers inside the request") as caught:
        provider.poll(_request(), job.job_id)

    assert "fake" in str(caught.value)


async def test_a_run_that_cannot_be_resumed_refuses_the_async_poll_as_well(clock: _Clock) -> None:
    provider = _one_shot()
    job = await provider.asubmit(_request())

    with pytest.raises(ProviderError, match="answers inside the request"):
        await provider.apoll(_request(), job.job_id)


# -- Cancelling --------------------------------------------------------------


def test_cancelling_a_finished_run_never_reaches_the_backend() -> None:
    provider = _fake()
    job = provider.submit(_request())

    assert job.cancel() is False
    assert provider.cancelled == [], "there is nothing to stop, so nothing to pay for asking"


def test_cancelling_a_running_run_reaches_the_backend() -> None:
    provider = _fake(polls_before_done=3)
    job = provider.submit(_request())

    assert job.cancel() is True
    assert provider.cancelled == ["job-1"]


async def test_cancelling_behaves_the_same_on_the_async_path() -> None:
    finished = _fake()
    running = _fake(polls_before_done=3)

    assert await (await finished.asubmit(_request())).acancel() is False
    assert await (await running.asubmit(_request())).acancel() is True

    assert finished.cancelled == []
    assert running.cancelled == ["job-1"]


def test_a_backend_that_cannot_cancel_says_so_rather_than_pretending() -> None:
    provider = _fake(polls_before_done=3, _cancel=ResearchProvider._cancel)
    job = provider.submit(_request())

    assert job.cancel() is False


def test_the_answer_is_the_normalized_response_whichever_backend_served_it(clock: _Clock) -> None:
    """The point of the package, at the one method where the wait could obscure it."""
    provider = _fake(polls_before_done=2)

    response = provider.research(_request(output_schema={"type": "object"}))

    assert isinstance(response, ResearchResponse)
    assert response.provider == "fake"
    assert response.query == QUERY
    assert response.structured == {"ok": True}
    assert response.urls == ["https://example.com"]
    assert response.outcome == "complete"


class _SlowStart(ResearchProvider):
    """A backend that does the whole run inside the call that starts it, slowly.

    Perplexity's shape. It is the case where a timeout is easiest to get wrong:
    there is no polling to interrupt, so if the budget only starts once a job
    exists, the caller's limit never applies to the part that actually takes
    the time.
    """

    name = "slow-start"
    supported_parameters = RESEARCH_PARAMETERS
    resumable = False

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.budgets: list[float] = []

    def _start(self, request: ResearchRequest) -> ResearchProgress:
        self.budgets.append(research_budget(RESEARCH_HTTP_TIMEOUT))
        return ResearchProgress(
            job_id="slow-1",
            state="completed",
            response=ResearchResponse(provider=self.name, query=request.query, answer="done"),
        )

    async def _astart(self, request: ResearchRequest) -> ResearchProgress:
        return self._start(request)


def test_the_budget_covers_the_call_that_starts_the_run() -> None:
    """A synchronous backend must see the caller's timeout, not its own HTTP one.

    Without this the timeout is decorative for Perplexity: the entire run
    happens inside `_start`, under a 120-second client timeout, so a caller
    asking for 5 seconds waits for as long as the backend feels like taking.
    """
    provider = _SlowStart()
    provider.research(ResearchRequest(query="q"), timeout=5)

    assert provider.budgets == [pytest.approx(5, abs=0.5)], "the start call was not clamped to the caller's budget"


def test_a_generous_budget_never_stretches_a_single_call() -> None:
    """The clamp only narrows. A 10-minute research budget is not a 10-minute request."""
    provider = _SlowStart()
    provider.research(ResearchRequest(query="q"), timeout=600)

    assert provider.budgets == [pytest.approx(RESEARCH_HTTP_TIMEOUT, abs=0.5)]


def test_no_timeout_leaves_each_call_to_its_own_default() -> None:
    provider = _SlowStart()
    provider.research(ResearchRequest(query="q"), timeout=None)

    assert provider.budgets == [RESEARCH_HTTP_TIMEOUT]


def test_an_answer_that_arrived_late_is_returned_rather_than_thrown_away() -> None:
    """A run that finished while the budget ran out has still been paid for.

    Reporting a timeout here would be the one outcome nobody wants: the work is
    done, the money is spent, and the answer is in hand.
    """

    class _Late(_SlowStart):
        name = "late"

        def _start(self, request: ResearchRequest) -> ResearchProgress:
            progress = super()._start(request)
            time.sleep(0.05)  # the budget expires during the call
            return progress

    provider = _Late()
    response = provider.research(ResearchRequest(query="q"), timeout=0.01)

    assert response.answer == "done"
