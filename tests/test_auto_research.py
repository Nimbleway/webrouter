"""`auto` for research: a chain of billed backends, and one run that stays with the one that took it."""

from __future__ import annotations

import os
from typing import Any

import httpx
import pytest

from web_router import WebRouter
from web_router.constants import (
    AUTO_RESEARCH_PREFERRED,
    AUTO_RESEARCH_SUPPORTED_PARAMETERS,
    PROVIDER_EXA,
    PROVIDER_LINKUP,
    PROVIDER_NIMBLE,
    PROVIDER_PARALLEL,
    PROVIDER_PERPLEXITY,
    PROVIDER_YOU,
    RESEARCH_ENV_BY_PROVIDER,
)
from web_router.exceptions import (
    ConfigurationError,
    ProviderError,
    ProviderNotInstalledError,
    UnsupportedParameterError,
)
from web_router.providers.auto import AutoResearchProvider
from web_router.registry import register_research_provider, unregister_research_provider
from web_router.types import RESEARCH_PARAMETERS, ResearchProgress, ResearchRequest

QUERY = ResearchRequest(query="who founded it")


def _http_failure(member: str, status: int, reason: str) -> ProviderError:
    """A member's failure as the base class wraps an HTTP status: a 4xx refused before anything
    was started or billed, while a 5xx may have come from a gateway after the run was accepted."""
    request = httpx.Request("POST", "https://research.example.test/runs")
    response = httpx.Response(status, request=request)
    error = httpx.HTTPStatusError(reason, request=request, response=response)
    return ProviderError(member, reason, original=error)


class _Recorder:
    """Tracks which members a chain reached, and which of them were asked again."""

    def __init__(self) -> None:
        self.called: list[str] = []
        self.polled: list[str] = []
        # Tracked rather than listed in the fixture: a hardcoded cleanup list
        # silently leaks any member a later test invents, and a leaked provider
        # breaks the contract tests that compare the registry against
        # RESEARCH_ENV_BY_PROVIDER -- from another file, with no clue why.
        self.registered: list[str] = []


def _member(
    recorder: _Recorder,
    member_name: str,
    *,
    raises: Exception | None = None,
    poll_raises: Exception | None = None,
    polls: int = 0,
    job_id: str | None = None,
    supported: frozenset[str] = RESEARCH_PARAMETERS,
) -> type:
    """Register a chain member that answers after ``polls`` polls, or fails in a given way."""
    from tests.conftest import FakeResearchProvider

    label = job_id or f"{member_name}-job"

    class Member(FakeResearchProvider):  # type: ignore[misc, valid-type]
        name = member_name
        polls_before_done = polls
        supported_parameters = supported

        def _start(self, request: ResearchRequest) -> ResearchProgress:
            recorder.called.append(member_name)
            if raises is not None:
                raise raises
            return self._labelled(super()._start(request))

        def _fetch(self, request: ResearchRequest, job_id: str) -> ResearchProgress:
            recorder.polled.append(member_name)
            if poll_raises is not None:
                raise poll_raises
            return self._labelled(super()._fetch(request, job_id))

        def _labelled(self, progress: ResearchProgress) -> ResearchProgress:
            """Give each member its own job ids, as real backends have."""
            return progress.model_copy(update={"job_id": label})

    register_research_provider(member_name, Member)
    recorder.registered.append(member_name)
    return Member


@pytest.fixture
def chain() -> Any:
    recorder = _Recorder()
    yield recorder
    for name in recorder.registered:
        unregister_research_provider(name)


def test_the_first_member_that_answers_wins(chain: _Recorder) -> None:
    _member(chain, "first")
    _member(chain, "second")

    provider = AutoResearchProvider(chain=(("first", {}), ("second", {})))
    try:
        response = provider.research(QUERY)
    finally:
        provider.close()

    assert chain.called == ["first"], "the second member should never have been reached"
    assert response.answer


def test_the_answer_names_the_backend_that_produced_it(chain: _Recorder) -> None:
    """A caller must be able to tell who did the research -- never just "auto"."""
    _member(chain, "first", raises=_http_failure("first", 429, "rate limited"))
    _member(chain, "second")

    provider = AutoResearchProvider(chain=(("first", {}), ("second", {})))
    try:
        response = provider.research(QUERY)
    finally:
        provider.close()

    assert chain.called == ["first", "second"]
    assert response.provider == "second"


def test_a_poll_goes_back_to_the_member_that_accepted_the_run(chain: _Recorder) -> None:
    """A run id means nothing to a backend that did not issue it, so the choice has to stick."""
    _member(chain, "first", polls=2)
    _member(chain, "second")

    provider = AutoResearchProvider(chain=(("first", {}), ("second", {})))
    try:
        job = provider.submit(QUERY)
        progress = provider.poll(QUERY, job.job_id)
        progress = provider.poll(QUERY, job.job_id)
    finally:
        provider.close()

    assert job.job_id == "first:first-job", "the handle names the member, so a poll can find it again"
    assert set(chain.polled) == {"first"}, "the second member was asked about a run it never started"
    assert progress.state == "completed"
    assert progress.response is not None and progress.response.provider == "first"


def test_a_run_started_somewhere_else_cannot_be_followed_here(chain: _Recorder) -> None:
    _member(chain, "first")

    provider = AutoResearchProvider(chain=(("first", {}),))
    try:
        with pytest.raises(ProviderError, match="was not started here"):
            provider.poll(QUERY, "some-other-job")
    finally:
        provider.close()


def test_a_running_job_is_cancelled_through_the_member_holding_it(chain: _Recorder) -> None:
    member_cls = _member(chain, "first", polls=5)

    provider = AutoResearchProvider(chain=(("first", {}),))
    try:
        job = provider.submit(QUERY)
        cancelled = provider.cancel(job.job_id)
    finally:
        provider.close()

    assert cancelled
    assert isinstance(provider._members[0], member_cls)
    assert provider._members[0].cancelled == ["first-job"]


def test_a_missing_credential_falls_through_to_the_next_member(chain: _Recorder) -> None:
    _member(chain, "first", raises=ConfigurationError.missing_api_key("first", "FIRST_KEY"))
    _member(chain, "second")

    provider = AutoResearchProvider(chain=(("first", {}), ("second", {})))
    try:
        assert provider.research(QUERY).provider == "second"
    finally:
        provider.close()


def test_a_failure_that_may_have_started_the_run_stops_the_chain(chain: _Recorder) -> None:
    """A 503 can come from a gateway after the backend accepted the run, so trying another would bill twice."""
    _member(chain, "first", raises=_http_failure("first", 503, "bad gateway"))
    _member(chain, "second")

    provider = AutoResearchProvider(chain=(("first", {}), ("second", {})))
    try:
        with pytest.raises(ProviderError, match="bad gateway"):
            provider.research(QUERY)
    finally:
        provider.close()

    assert chain.called == ["first"], "the second member was started after an ambiguous failure"


def test_a_failed_first_poll_keeps_the_handle_and_never_starts_a_second_run(chain: _Recorder) -> None:
    """The accepted run is being paid for: it must stay reachable, and its failure is its own."""
    _member(chain, "first", polls=1, poll_raises=ProviderError("first", "lost the run"))
    _member(chain, "second")

    provider = AutoResearchProvider(chain=(("first", {}), ("second", {})))
    try:
        job = provider.submit(QUERY)
        assert chain.polled == [], "the handle is registered from the start progress, before any poll"
        with pytest.raises(ProviderError, match="lost the run"):
            provider.poll(QUERY, job.job_id)
    finally:
        provider.close()

    assert job.job_id == "first:first-job"
    assert job.job_id in provider._jobs, "the billed run can still be polled or cancelled"
    assert chain.called == ["first"], "no second run was started over a poll that failed"


def test_exhausting_the_chain_reports_every_backends_own_reason(chain: _Recorder) -> None:
    _member(chain, "first", raises=_http_failure("first", 429, "rate limited"))
    _member(chain, "second", raises=_http_failure("second", 403, "plan does not include research"))

    provider = AutoResearchProvider(chain=(("first", {}), ("second", {})))
    try:
        with pytest.raises(ProviderError) as caught:
            provider.research(QUERY)
    finally:
        provider.close()

    message = str(caught.value)
    assert "first" in message and "rate limited" in message
    assert "second" in message and "plan does not include research" in message
    assert "keyless" not in message, "the members are keyed and research has no keyless tier"


def test_nothing_installed_reports_one_fix_rather_than_a_row_of_failures(chain: _Recorder) -> None:
    _member(chain, "first", raises=ProviderNotInstalledError("first", "first"))

    provider = AutoResearchProvider(chain=(("first", {}),))
    try:
        with pytest.raises(ProviderNotInstalledError):
            provider.research(QUERY)
    finally:
        provider.close()


def test_with_no_backend_configured_the_error_names_the_variables_that_would_fix_it() -> None:
    """There is no keyless research tier, so an empty chain is a setup problem, not a backend failure."""
    provider = AutoResearchProvider(chain=())

    with pytest.raises(ConfigurationError) as caught:
        provider.research(QUERY)

    message = str(caught.value)
    assert "NIMBLE_API_KEY" in message
    assert "PERPLEXITY_API_KEY" in message


def test_only_the_backends_you_hold_a_key_for_are_tried(monkeypatch: pytest.MonkeyPatch) -> None:
    """Search and extract end in an anonymous tier; research bills for every run and has none."""
    for env_vars in RESEARCH_ENV_BY_PROVIDER.values():
        for env_var in env_vars:
            monkeypatch.delenv(env_var, raising=False)
    monkeypatch.setenv("NIMBLE_API_KEY", "test-key")

    assert AutoResearchProvider.default_chain() == (("nimble", {}),)


def test_later_backends_join_the_chain_after_the_original_members(monkeypatch: pytest.MonkeyPatch) -> None:
    """Linkup, then You.com, appended, so an environment that reached an earlier member still reaches it first."""
    for env_vars in RESEARCH_ENV_BY_PROVIDER.values():
        for env_var in env_vars:
            monkeypatch.setenv(env_var, "test-key")

    chain = [name for name, _ in AutoResearchProvider.default_chain()]

    assert chain == [PROVIDER_NIMBLE, PROVIDER_PARALLEL, PROVIDER_PERPLEXITY, PROVIDER_LINKUP, PROVIDER_YOU]
    assert list(AUTO_RESEARCH_PREFERRED) == chain


def test_a_research_chain_in_provider_config_is_honoured(chain: _Recorder) -> None:
    _member(chain, "first")
    _member(chain, "second")

    with WebRouter(provider_config={"auto": {"chain": {"research": ["second", "first"]}}}) as router:
        response = router.research("who founded it", timeout=5)

    assert response.provider == "second"
    assert chain.called == ["second"]


def test_with_no_credentials_at_all_there_is_no_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    for env_vars in RESEARCH_ENV_BY_PROVIDER.values():
        for env_var in env_vars:
            monkeypatch.delenv(env_var, raising=False)

    assert AutoResearchProvider.default_chain() == ()


def test_an_option_no_member_is_guaranteed_to_map_is_rejected_before_any_run_starts(chain: _Recorder) -> None:
    """Falling through on a caller error would pay for the same run on every backend in turn."""
    _member(chain, "first")

    provider = AutoResearchProvider(chain=(("first", {}),))
    try:
        with pytest.raises(UnsupportedParameterError):
            provider.research(ResearchRequest(query="q", records=[{"name": "Nimbleway"}]))
    finally:
        provider.close()

    assert chain.called == []
    assert "records" not in AUTO_RESEARCH_SUPPORTED_PARAMETERS


def test_members_are_built_once_and_closed(chain: _Recorder) -> None:
    _member(chain, "first")

    provider = AutoResearchProvider(chain=(("first", {}),))
    provider.research(QUERY)
    provider.research(QUERY)
    built = list(provider._members.values())
    provider.close()

    assert len(built) == 1, "the member should be cached, not rebuilt per run"
    assert all(member.closed for member in built)


async def test_the_chain_works_the_same_way_async(chain: _Recorder) -> None:
    _member(chain, "first", raises=_http_failure("first", 429, "rate limited"))
    _member(chain, "second", polls=1)

    provider = AutoResearchProvider(chain=(("first", {}), ("second", {})))
    try:
        job = await provider.asubmit(QUERY)
        progress = await provider.apoll(QUERY, job.job_id)
    finally:
        await provider.aclose()

    assert chain.called == ["first", "second"]
    assert job.job_id == "second:second-job"
    assert progress.response is not None and progress.response.provider == "second"


def test_two_backends_issuing_the_same_run_id_stay_separate_runs(chain: _Recorder) -> None:
    """Run ids are issued per backend, so two of them can collide without meaning to.

    Keyed on the id alone, the second run would overwrite the first and polling
    the first handle would answer about the second -- a wrong answer rather than
    an error, which is the worst shape a bug like this can take.
    """
    _member(chain, "first", job_id="run-1", polls=2)
    _member(chain, "second", job_id="run-1", polls=2)

    provider = AutoResearchProvider(chain=(("first", {}),))
    other = AutoResearchProvider(chain=(("second", {}),))
    try:
        first = provider.submit(QUERY)
        second = other.submit(QUERY)
        # Same backend-issued id, two different runs.
        assert first.job_id != second.job_id
        assert first.job_id.endswith("run-1") and second.job_id.endswith("run-1")

        # Only what follows is the question -- which backend a poll of the
        # *first* handle reaches.
        chain.polled.clear()
        provider.poll(QUERY, first.job_id)
    finally:
        provider.close()
        other.close()

    assert chain.polled == ["first"], "the poll reached the backend that took this run"


def test_a_member_that_cannot_honour_the_request_is_passed_over(chain: _Recorder) -> None:
    """`auto` offers a wider surface than its weakest member, so it must route around it.

    Perplexity and You.com map no `output_schema`; Nimble and Parallel do. Narrowing the
    shared surface to the weakest member would cost the other two a real
    capability, so the member that cannot serve *this* request is skipped
    instead -- before anything bills for a run that was going to be rejected.
    """
    _member(chain, "narrow", supported=frozenset({"effort"}))
    _member(chain, "wide", supported=AUTO_RESEARCH_SUPPORTED_PARAMETERS)

    provider = AutoResearchProvider(chain=(("narrow", {}), ("wide", {})))
    try:
        response = provider.research(ResearchRequest(query="q", output_schema={"type": "object"}), timeout=5)
    finally:
        provider.close()

    assert chain.called == ["wide"], "the backend that would have rejected it was never started"
    assert response.provider == "wide"


def test_no_member_maps_the_option_and_nothing_is_billed(chain: _Recorder) -> None:
    """Every member would reject it, so say so instead of starting a run that cannot work."""
    _member(chain, "narrow", supported=frozenset({"effort"}))

    provider = AutoResearchProvider(chain=(("narrow", {}),))
    try:
        with pytest.raises(UnsupportedParameterError) as caught:
            provider.submit(ResearchRequest(query="q", output_schema={"type": "object"}))
    finally:
        provider.close()

    message = str(caught.value)
    assert "output_schema" in message
    assert "narrow (maps only effort)" in message, "the error says which backend was passed over, and why"
    assert "provider_options" not in message, "auto has no provider_options to point at"
    assert chain.called == [], "nothing was started, so nothing was billed"


def test_options_mapped_only_by_different_members_are_named_as_unservable(chain: _Recorder) -> None:
    """Each option has a member that maps it, but none maps both -- the error must still name them."""
    _member(chain, "schemas", supported=frozenset({"output_schema"}))
    _member(chain, "efforts", supported=frozenset({"effort"}))

    provider = AutoResearchProvider(chain=(("schemas", {}), ("efforts", {})))
    try:
        with pytest.raises(UnsupportedParameterError) as caught:
            provider.submit(ResearchRequest(query="q", output_schema={"type": "object"}, effort="low"))
    finally:
        provider.close()

    assert caught.value.parameters == ["effort", "output_schema"]
    assert chain.called == []


def test_the_chain_holds_only_backends_that_can_actually_answer() -> None:
    """Every backend `auto` routes research to is one that is registered to run it.

    Exa is the case this pins: it retired its Research API (every path answers
    410), so it is no research backend at all, and an EXA_API_KEY alone
    configures none.
    """
    assert PROVIDER_EXA not in AUTO_RESEARCH_PREFERRED
    assert PROVIDER_EXA not in RESEARCH_ENV_BY_PROVIDER
    assert set(AUTO_RESEARCH_PREFERRED) <= set(RESEARCH_ENV_BY_PROVIDER)


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("NIMBLE_API_KEY"), reason="NIMBLE_API_KEY not set")
def test_live_auto_research_answers_through_a_configured_backend() -> None:
    from tests.conftest import billing_aware
    from web_router import WebRouter

    with WebRouter("auto") as router, billing_aware("auto"):
        response = router.research("When was Nimbleway founded?", effort="low")

    assert response.provider != "auto", "the response must name the backend that answered"
    assert response.provider in RESEARCH_ENV_BY_PROVIDER
    assert response.answer or response.structured
