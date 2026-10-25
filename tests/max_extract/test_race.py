"""The race on one URL: the first document back wins, and only a document wins."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest

from tests.max_extract.conftest import URL, _member, _race, _Recorder, _timed
from web_router.exceptions import ProviderError, UnsupportedFormatError, UnsupportedParameterError
from web_router.providers.max import MaxExtractProvider
from web_router.types import ExtractRequest


def test_the_first_document_back_wins_whatever_the_chain_order(members: _Recorder) -> None:
    _member(members, "slow", delay=0.3)
    _member(members, "fast", delay=0.0)

    response, elapsed = _timed(_race("slow", "fast"))

    assert response.provider == "fast", "the answer names who served it, never max"
    assert response.raw == [{"from": "fast"}]
    assert elapsed < 0.25, "the winner is returned without waiting for the loser"
    assert set(members.started) == {"slow", "fast"}, "without a hedge every member is started at once"


def test_a_failure_does_not_win_however_fast(members: _Recorder) -> None:
    _member(members, "fast-but-broken", raises=ProviderError("fast-but-broken", "HTTP 403"))
    _member(members, "slow", delay=0.1)

    response, _ = _timed(_race("fast-but-broken", "slow"))

    assert response.provider == "slow"


def test_every_member_failing_names_each_ones_reason(members: _Recorder) -> None:
    _member(members, "first", raises=ProviderError("first", "HTTP 403"))
    _member(members, "second", raises=ProviderError("second", "timed out"))

    with pytest.raises(ProviderError, match="no backend answered") as caught:
        _timed(_race("first", "second"))

    assert "[first] HTTP 403" in str(caught.value) and "[second] timed out" in str(caught.value)


def test_a_member_that_cannot_be_built_is_a_failure_not_a_crash(members: _Recorder, monkeypatch: Any) -> None:
    """A real backend with no key raises ConfigurationError when built, which is that member's own loss."""
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    _member(members, "fine", delay=0.05)

    response, _ = _timed(MaxExtractProvider(chain=[("fine", {}), ("exa", {})]))

    assert response.provider == "fine"


def test_a_caller_bug_in_a_member_propagates_rather_than_being_raced_around(members: _Recorder) -> None:
    _member(members, "strict", raises=UnsupportedParameterError("strict", ["format"]))
    _member(members, "slow", delay=0.3)

    with pytest.raises(UnsupportedParameterError):
        _timed(_race("strict", "slow"))


def test_every_member_validates_the_request_before_anything_is_fetched(members: _Recorder) -> None:
    """A member that refuses an option says so first, so a faster member's document cannot hide it."""
    _member(members, "fast")
    Strict = _member(members, "strict", delay=0.5)
    Strict.supported_parameters = frozenset()

    with pytest.raises(UnsupportedParameterError, match="format"):
        _timed(_race("fast", "strict"), ExtractRequest(urls=[URL], format="markdown"))

    assert members.started == [], "nothing was fetched, so nothing was paid for"


def test_calls_are_handed_back_in_the_order_they_finished_not_the_order_they_were_waited_on() -> None:
    """``wait()`` returns a set; the race records completion order itself, so the first document really is first."""
    from web_router.providers.max import _Race

    race = _Race()
    with ThreadPoolExecutor(max_workers=2) as pool:
        race.add("late", pool.submit(lambda: time.sleep(0.05) or "late"))
        race.add("early", pool.submit(lambda: "early"))
        time.sleep(0.2)  # both finished before anyone asked
        first = race.next()
        second = race.next()

    assert first is not None and first[0] == "early"
    assert second is not None and second[0] == "late"
    assert race.next() is None, "nothing left running"


def test_the_format_is_pinned_and_a_member_that_cannot_return_it_is_left_out(members: _Recorder) -> None:
    _member(members, "text-only", formats=frozenset({"text"}))
    _member(members, "markdown", formats=frozenset({"markdown"}))

    response, _ = _timed(_race("text-only", "markdown"))

    assert response.provider == "markdown"
    assert response.documents[0].format == "markdown"
    assert members.started == ["markdown"], "a member passed over for the format is never called"


def test_a_format_no_member_returns_raises_before_anything_is_fetched(members: _Recorder) -> None:
    _member(members, "text-only", formats=frozenset({"text"}))

    with pytest.raises(UnsupportedFormatError, match="html"):
        _timed(_race("text-only"), ExtractRequest(urls=[URL], format="html"))

    assert members.started == []
