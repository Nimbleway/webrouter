"""Per-call deadlines under ``auto``: it tries a next member only while time is left, and spends what is left."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from tests.conftest import FakeProvider, FakeResearchProvider
from tests.deadline_helpers import DEADLINE, TINY, _Clock
from web_router.exceptions import DeadlineExceededError, ProviderError, ResearchTimeoutError
from web_router.providers._deadline import call_deadline, job_budget
from web_router.providers.auto import AutoProvider
from web_router.registry import register_provider, unregister_provider
from web_router.types import ResearchProgress, ResearchRequest, SearchRequest, SearchResponse

# -- auto spends what is left ------------------------------------------------------


class _Calls:
    def __init__(self) -> None:
        self.started: list[tuple[str, float, float]] = []
        """(member, when it started, the timeout its request was given)."""
        self.names: list[str] = []


@pytest.fixture
def calls() -> Any:
    record = _Calls()
    yield record
    for name in record.names:
        unregister_provider(name)


def _hanging_member(calls: _Calls, clock: _Clock, name: str, *, own_timeout: float) -> None:
    """A search member that stalls until its request's timeout -- its own, clamped to the time left -- runs out."""

    class Member(FakeProvider):
        def _search(self, request: SearchRequest) -> SearchResponse:
            budget = job_budget(own_timeout)
            calls.started.append((name, clock.now, budget))
            clock.now += budget
            raise TimeoutError("read timed out")

    Member.name = name
    register_provider(name, Member)
    calls.names.append(name)


def _empty_member(calls: _Calls, name: str) -> None:
    class Member(FakeProvider):
        def _search(self, request: SearchRequest) -> SearchResponse:
            calls.started.append((name, 0.0, 0.0))
            return SearchResponse(provider=name, query=request.query, results=[])

    Member.name = name
    register_provider(name, Member)
    calls.names.append(name)


def test_auto_tries_the_next_member_within_the_deadline_and_names_it_at_the_end(clock: _Clock, calls: _Calls) -> None:
    """A chain of three and a 3s deadline: the first hangs, the second starts before the deadline.

    No time is held back for later members: the first runs to its own 2s
    timeout, the second -- not the last, yet -- gets min(its timeout, what is
    left), and the third never starts, because nothing is left.
    """
    for name in ("first", "second", "third"):
        _hanging_member(calls, clock, name, own_timeout=2.0)
    provider = AutoProvider(chain=(("first", {}), ("second", {}), ("third", {})))

    with call_deadline(DEADLINE), pytest.raises(DeadlineExceededError) as caught:
        provider.search(SearchRequest(query="q"))

    assert calls.started == [("first", 0.0, 2.0), ("second", 2.0, 1.0)]
    assert caught.value.provider == "auto"
    message = str(caught.value)
    assert "3s deadline" in message
    assert "first:" in message and "second:" in message, "each member's reason is named"


def test_a_member_the_deadline_left_no_time_for_is_never_built_nor_named(clock: _Clock, calls: _Calls) -> None:
    """The first member fails on its own as the deadline arrives: the chain stops there, before building the next."""
    built: list[str] = []

    class Spends(FakeProvider):
        def _search(self, request: SearchRequest) -> SearchResponse:
            clock.now = DEADLINE
            raise ProviderError("spends", "upstream 503")

    class Next(FakeProvider):
        def __init__(self, **kwargs: Any) -> None:
            built.append("next")
            super().__init__(**kwargs)

    for name, cls in (("spends", Spends), ("next", Next)):
        cls.name = name
        register_provider(name, cls)
        calls.names.append(name)
    provider = AutoProvider(chain=(("spends", {}), ("next", {})))

    with call_deadline(DEADLINE), pytest.raises(DeadlineExceededError) as caught:
        provider.search(SearchRequest(query="q"))

    assert built == [], "no member is built once no time is left"
    message = str(caught.value)
    assert "spends: " in message and "upstream 503" in message
    assert "next" not in message, "a member that never started is not named as tried"


def test_the_last_member_gets_everything_that_remains(clock: _Clock, calls: _Calls) -> None:
    _hanging_member(calls, clock, "first", own_timeout=0.5)
    _hanging_member(calls, clock, "last", own_timeout=60.0)
    provider = AutoProvider(chain=(("first", {}), ("last", {})))

    with call_deadline(DEADLINE), pytest.raises(DeadlineExceededError):
        provider.search(SearchRequest(query="q"))

    assert calls.started == [("first", 0.0, 0.5), ("last", 0.5, DEADLINE - 0.5)]


def test_an_empty_answer_that_came_back_in_time_outranks_the_deadline(clock: _Clock, calls: _Calls) -> None:
    _empty_member(calls, "empty")
    _hanging_member(calls, clock, "slow", own_timeout=60.0)
    provider = AutoProvider(chain=(("empty", {}), ("slow", {})))

    with call_deadline(DEADLINE):
        response = provider.search(SearchRequest(query="q"))

    assert response.provider == "empty"


async def test_async_auto_moves_on_and_stops_at_the_deadline(calls: _Calls) -> None:
    class Refuses(FakeProvider):
        async def _asearch(self, request: SearchRequest) -> SearchResponse:
            calls.started.append(("refuses", 0.0, 0.0))
            raise ProviderError("refuses", "rate limited")

    class Never(FakeProvider):
        async def _asearch(self, request: SearchRequest) -> SearchResponse:
            calls.started.append(("never", 0.0, 0.0))
            await asyncio.sleep(30)
            raise AssertionError  # pragma: no cover

    for name, cls in (("refuses", Refuses), ("never", Never)):
        cls.name = name
        register_provider(name, cls)
        calls.names.append(name)
    provider = AutoProvider(chain=(("refuses", {}), ("never", {})))

    with call_deadline(TINY), pytest.raises(DeadlineExceededError, match=r"0\.05s deadline"):
        await provider.asearch(SearchRequest(query="q"))

    assert [name for name, _, _ in calls.started] == ["refuses", "never"]


def test_a_members_own_shorter_timeout_is_its_failure_and_auto_moves_on(clock: _Clock, calls: _Calls) -> None:
    """0.4s left and a member whose own timeout is 0.1s: it timed out on its own, so the next member is tried."""
    from web_router.providers._deadline import call_budget

    class Quick(FakeProvider):
        def _search(self, request: SearchRequest) -> SearchResponse:
            budget = call_budget(0.1)
            calls.started.append(("quick", clock.now, budget))
            clock.now += budget
            raise TimeoutError("read timed out")

    class Answers(FakeProvider):
        def _search(self, request: SearchRequest) -> SearchResponse:
            calls.started.append(("answers", clock.now, 0.0))
            return super()._search(request)

    for name, cls in (("quick", Quick), ("answers", Answers)):
        cls.name = name
        register_provider(name, cls)
        calls.names.append(name)
    provider = AutoProvider(chain=(("quick", {}), ("answers", {})))

    with call_deadline(DEADLINE):
        clock.now = DEADLINE - 0.4
        response = provider.search(SearchRequest(query="q"))

    assert response.provider == "answers"
    assert [name for name, _, _ in calls.started] == ["quick", "answers"]
    assert calls.started[0][2] == pytest.approx(0.1), "the member's own timeout, not the time left, set the limit"


class _CutOffInPoll(FakeResearchProvider):
    polls_before_done = 10**6

    def _fetch(self, request: ResearchRequest, job_id: str) -> ResearchProgress:
        raise DeadlineExceededError(self.name, DEADLINE)


def test_a_poll_the_deadline_cuts_off_raises_researchs_timeout_with_the_runs_id() -> None:
    with call_deadline(600), pytest.raises(ResearchTimeoutError) as caught:
        _CutOffInPoll().research(ResearchRequest(query="q"), timeout=600)
    assert caught.value.job_id == "job-1", "the run started and bills: its id picks it back up"


def test_a_failure_ending_in_a_period_is_not_given_a_second_one() -> None:
    from web_router.exceptions import DeadlineExceededError

    message = str(DeadlineExceededError("auto", 5, tried="tavily: timed out."))
    assert message.endswith("Before it ran out: tavily: timed out.")
