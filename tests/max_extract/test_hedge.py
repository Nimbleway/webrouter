"""The hedge: the first member runs alone for that long, and the rest start only if it has not answered."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest

from tests.max_extract.conftest import REQUEST, _member, _race, _Recorder, _timed
from web_router.exceptions import ConfigurationError, ProviderError
from web_router.providers.max import MaxExtractProvider
from web_router.registry import register_extract_provider


def test_with_a_hedge_a_first_member_that_answers_in_time_is_the_only_fetch(members: _Recorder) -> None:
    _member(members, "first", delay=0.0)
    _member(members, "second")

    response, _ = _timed(_race("first", "second", hedge=0.5))

    assert response.provider == "first"
    assert members.started == ["first"], "the rest were never started, so nothing else was paid for"


def test_with_a_hedge_a_slow_first_member_is_joined_by_the_rest_once_it_runs_out(members: _Recorder) -> None:
    _member(members, "first", delay=0.6)
    _member(members, "second", delay=0.0)

    response, elapsed = _timed(_race("first", "second", hedge=0.1))

    assert response.provider == "second"
    assert 0.1 <= elapsed < 0.5, "the rest start when the hedge runs out, and the winner is not held for the first"
    assert members.started == ["first", "second"]


def test_with_a_hedge_a_first_member_that_fails_early_does_not_hold_the_rest(members: _Recorder) -> None:
    _member(members, "first", raises=ProviderError("first", "HTTP 403"))
    _member(members, "second")

    response, elapsed = _timed(_race("first", "second", hedge=2.0))

    assert response.provider == "second"
    assert elapsed < 1.0, "a failure before the hedge runs out starts the rest at once"


def test_with_a_hedge_a_first_member_that_cannot_be_built_does_not_hold_the_rest(
    members: _Recorder, monkeypatch: Any
) -> None:
    """A member with no key fails at construction, before the hedge even starts; the rest must still run."""
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    _member(members, "second")

    response, elapsed = _timed(MaxExtractProvider(chain=[("exa", {}), ("second", {})], hedge=5.0))

    assert response.provider == "second"
    assert elapsed < 1.0


def test_two_races_at_once_share_one_instance_of_each_member(members: _Recorder) -> None:
    """Callers on their own threads build a member once: a slow constructor widens the window the lock closes."""
    built: list[str] = []

    class Counted(_member(members, "counted", delay=0.05)):  # type: ignore[misc, valid-type]
        def __init__(self, **kwargs: Any) -> None:
            time.sleep(0.1)  # long enough for every caller to be inside the cache miss without a lock
            built.append("counted")
            super().__init__(**kwargs)

    register_extract_provider("counted", Counted)
    provider = _race("counted")
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            responses = list(pool.map(lambda _: provider.extract(REQUEST), range(4)))
    finally:
        provider.close()

    assert all(response.provider == "counted" for response in responses)
    assert built == ["counted"]


@pytest.mark.parametrize("hedge", [-1, "soon", True, float("inf"), float("nan")])
def test_a_hedge_that_is_not_a_non_negative_number_is_refused(hedge: Any) -> None:
    with pytest.raises(ConfigurationError, match="hedge"):
        MaxExtractProvider(chain=[("jina", {})], hedge=hedge)


def test_a_zero_hedge_means_no_hedge() -> None:
    assert MaxExtractProvider(chain=[("jina", {})], hedge=0).hedge is None
