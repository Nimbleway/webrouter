"""The race on a list of URLs: one race for the whole list, and the first *complete* answer wins."""

from __future__ import annotations

from tests.max_extract.conftest import URL, URLS, _member, _race, _Recorder, _timed
from web_router.exceptions import ProviderError
from web_router.types import ExtractRequest


def test_several_urls_are_one_race_and_the_winner_serves_them_all(members: _Recorder) -> None:
    _member(members, "slow", delay=0.3)
    _member(members, "fast")

    response, _ = _timed(_race("slow", "fast"), ExtractRequest(urls=[URL, "https://example.org"]))

    assert response.provider == "fast", "one race for the whole list: every document comes from the winner"
    assert response.urls == [URL, "https://example.org"]


def test_a_partial_answer_is_held_while_a_slower_member_may_still_answer_in_full(members: _Recorder) -> None:
    """Most backends take one URL per request, so the member that fetched less is usually the faster one."""
    _member(members, "partial", serves=1)
    _member(members, "complete", delay=0.2)

    response, _ = _timed(_race("partial", "complete"), ExtractRequest(urls=URLS))

    assert response.provider == "complete", "a fast partial answer does not beat a complete one"
    assert response.urls == URLS and response.failures == []


def test_a_partial_answer_is_returned_once_no_member_can_do_better(members: _Recorder) -> None:
    _member(members, "partial", serves=1)
    _member(members, "broken", raises=ProviderError("broken", "HTTP 403"))

    response, _ = _timed(_race("partial", "broken"), ExtractRequest(urls=URLS))

    assert response.provider == "partial"
    assert response.urls == URLS[:1] and [failure.url for failure in response.failures] == URLS[1:]


def test_of_two_partial_answers_the_one_with_fewer_failures_is_returned(members: _Recorder) -> None:
    _member(members, "one", serves=1)
    _member(members, "two", serves=2, delay=0.1)

    response, _ = _timed(_race("one", "two"), ExtractRequest(urls=URLS))

    assert response.provider == "two"
    assert response.urls == URLS[:2]


def test_with_a_hedge_a_partial_first_answer_starts_the_rest_at_once(members: _Recorder) -> None:
    _member(members, "first", serves=1)
    _member(members, "second", delay=0.05)

    response, elapsed = _timed(_race("first", "second", hedge=2.0), ExtractRequest(urls=URLS))

    assert response.provider == "second"
    assert elapsed < 1.0, "a partial answer within the hedge starts the rest as a failure does"
