"""Per-call deadlines on research and browse, which wait no longer, and on ``max``, which carries it to its threads."""

from __future__ import annotations

import threading
from typing import Any

import pytest

from tests.conftest import FakeBrowseProvider, FakeExtractProvider, FakeProvider, FakeResearchProvider
from tests.deadline_helpers import DEADLINE, TINY, URLS, _Clock
from tests.deadline_helpers import clock as clock  # a fixture: imported by name so pytest finds it here
from web_router import WebRouter
from web_router.exceptions import (
    BrowseTimeoutError,
    ConfigurationError,
    DeadlineExceededError,
    ProviderError,
    ResearchTimeoutError,
)
from web_router.providers._deadline import call_deadline, current_deadline, job_budget, job_deadline
from web_router.providers.auto import AutoResearchProvider
from web_router.providers.max import MaxExtractProvider
from web_router.registry import (
    register_extract_provider,
    register_provider,
    register_research_provider,
    unregister_extract_provider,
    unregister_provider,
    unregister_research_provider,
)
from web_router.types import (
    BrowseRequest,
    ExtractRequest,
    ExtractResponse,
    ResearchProgress,
    ResearchRequest,
    SearchRequest,
    SearchResponse,
)

# -- Research and browse wait no longer than the deadline -----------------------


class _NeverDone(FakeResearchProvider):
    polls_before_done = 10**9


class _NeverDoneBrowse(FakeBrowseProvider):
    polls_before_done = 10**9


def test_research_waits_no_longer_than_an_enclosing_deadline(clock: _Clock) -> None:
    with call_deadline(DEADLINE), pytest.raises(ResearchTimeoutError, match="after 3s") as caught:
        _NeverDone().research(ResearchRequest(query="q"), timeout=600)

    assert caught.value.job_id == "job-1", "the run is still going, and the error says how to come back to it"
    assert clock.now == DEADLINE


def test_browse_waits_no_longer_than_an_enclosing_deadline(clock: _Clock) -> None:
    with call_deadline(DEADLINE), pytest.raises(BrowseTimeoutError, match="after 3s") as caught:
        _NeverDoneBrowse().browse(BrowseRequest(task="t"), timeout=900)

    assert caught.value.job_id == "task-1"


async def test_async_research_waits_no_longer_than_an_enclosing_deadline() -> None:
    with call_deadline(TINY), pytest.raises(ResearchTimeoutError, match=r"after 0\.05s"):
        await _NeverDone().aresearch(ResearchRequest(query="q"), timeout=600)


def test_a_shorter_research_timeout_still_wins_over_a_longer_deadline(clock: _Clock) -> None:
    with call_deadline(600), pytest.raises(ResearchTimeoutError, match="after 3s"):
        _NeverDone().research(ResearchRequest(query="q"), timeout=DEADLINE)


# -- The deadline itself ----------------------------------------------------------


def test_an_inner_deadline_can_tighten_but_never_extend(clock: _Clock) -> None:
    with call_deadline(DEADLINE):
        with call_deadline(600):
            assert job_budget(60) == DEADLINE
            assert current_deadline().seconds == DEADLINE  # type: ignore[union-attr]
        with job_deadline(1.0):
            assert job_budget(60) == 1.0
        with call_deadline(None):
            assert job_budget(60) == DEADLINE
    assert current_deadline() is None
    assert job_budget(60) == 60


# -- Billed submits are as they were -------------------------------------------------


@pytest.fixture
def research_members() -> Any:
    names: list[str] = []
    yield names
    for name in names:
        unregister_research_provider(name)


def test_auto_research_never_cuts_a_submit_short_for_a_later_member(clock: _Clock, research_members: list[str]) -> None:
    """A member that started nothing passes on the whole of what is left; the submit is clamped as on its own."""
    budgets: list[float] = []

    class NoKey(FakeResearchProvider):
        def _start(self, request: ResearchRequest) -> ResearchProgress:
            clock.now += 0.5
            raise ConfigurationError("no key")

    class Takes(FakeResearchProvider):
        def _start(self, request: ResearchRequest) -> ResearchProgress:
            budgets.append(job_budget(120.0))
            return super()._start(request)

    for name, cls in (("nokey", NoKey), ("takes", Takes)):
        cls.name = name
        register_research_provider(name, cls)
        research_members.append(name)
    provider = AutoResearchProvider(chain=(("nokey", {}), ("takes", {})))

    with call_deadline(DEADLINE):
        response = provider.research(ResearchRequest(query="q"), timeout=600)

    assert response.answer == "the answer"
    assert budgets == [DEADLINE - 0.5], "everything left, not a share of it"


def test_a_submit_that_times_out_at_the_deadline_raises_as_it_always_has(
    clock: _Clock, research_members: list[str]
) -> None:
    """A read timeout may come after the backend created the run, so auto starts no second one."""

    class Stalls(FakeResearchProvider):
        def _start(self, request: ResearchRequest) -> ResearchProgress:
            clock.now += job_budget(120.0)
            raise TimeoutError("read timed out")

    class Never(FakeResearchProvider):
        def _start(self, request: ResearchRequest) -> ResearchProgress:
            raise AssertionError("a second run must not start")  # pragma: no cover

    for name, cls in (("stalls", Stalls), ("never", Never)):
        cls.name = name
        register_research_provider(name, cls)
        research_members.append(name)
    provider = AutoResearchProvider(chain=(("stalls", {}), ("never", {})))

    with call_deadline(DEADLINE), pytest.raises(ProviderError, match="read timed out"):
        provider.research(ResearchRequest(query="q"), timeout=600)


# -- max hands the deadline to its worker threads -------------------------------------


def test_max_carries_the_deadline_onto_its_worker_threads() -> None:
    seen: list[tuple[bool, bool]] = []

    class Member(FakeExtractProvider):
        name = "member"

        def _extract(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
            seen.append((threading.current_thread() is threading.main_thread(), current_deadline() is not None))
            return super()._extract(request, urls)

    register_extract_provider("member", Member)
    try:
        provider = MaxExtractProvider(chain=(("member", {}),))
        with call_deadline(60):
            provider.extract(ExtractRequest(urls=URLS[:1]))
        provider.close()
    finally:
        unregister_extract_provider("member")

    assert seen == [(False, True)]


# -- A billed run is not started once the deadline has passed ------------------------


class _NeverStarts(FakeResearchProvider):
    def _start(self, request: ResearchRequest) -> ResearchProgress:
        raise AssertionError("a billed run must not start past the deadline")  # pragma: no cover


def test_research_is_not_submitted_once_an_enclosing_deadline_has_passed(clock: _Clock) -> None:
    with call_deadline(DEADLINE):
        clock.now = DEADLINE
        with pytest.raises(DeadlineExceededError, match="3s deadline"):
            _NeverStarts().research(ResearchRequest(query="q"), timeout=600)


async def test_async_research_is_not_submitted_once_an_enclosing_deadline_has_passed(clock: _Clock) -> None:
    with call_deadline(DEADLINE):
        clock.now = DEADLINE
        with pytest.raises(DeadlineExceededError, match="3s deadline"):
            await _NeverStarts().aresearch(ResearchRequest(query="q"), timeout=600)


def test_a_runs_own_short_timeout_still_submits_it(clock: _Clock) -> None:
    """Only the caller's deadline refuses a run: its own timeout of zero still sends it, then times out."""
    provider = _NeverDone()

    with pytest.raises(ResearchTimeoutError):
        provider.research(ResearchRequest(query="q"), timeout=0)

    assert provider.requests, "the run was submitted"


# -- A benchmark hands the deadline to its worker threads --------------------------


def test_a_benchmark_carries_the_deadline_onto_each_members_thread() -> None:
    seen: dict[str, tuple[bool, bool]] = {}

    class Member(FakeProvider):
        def _search(self, request: SearchRequest) -> SearchResponse:
            seen[self.name] = (threading.current_thread() is threading.main_thread(), current_deadline() is not None)
            return super()._search(request)

    names = ("bench_first", "bench_second")
    for name in names:
        register_provider(name, type(name, (Member,), {"name": name}))
    try:
        with WebRouter(names[0]) as router, call_deadline(60):
            router.benchmark("q", providers=list(names))
    finally:
        for name in names:
            unregister_provider(name)

    assert seen == dict.fromkeys(names, (False, True))
