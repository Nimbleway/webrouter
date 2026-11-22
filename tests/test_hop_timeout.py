"""A router's ``hop_timeout``: one ``auto`` member that hangs fails as ``timeout`` and the next one answers."""

from __future__ import annotations

import time
from typing import Any

import pytest

from tests.hop_helpers import hop_members, hop_router
from web_router import (
    AllProvidersFailedError,
    AsyncWebRouter,
    DeadlineExceededError,
    InvalidParameterError,
    WebRouter,
)
from web_router.constants_production import TIMEOUT_COOLDOWN_AFTER
from web_router.types_errors import ERROR_KIND_TIMEOUT

pytestmark = pytest.mark.usefixtures(hop_members.__name__)


def test_a_hung_first_member_gives_way_to_the_next_within_the_call_deadline() -> None:
    started = time.monotonic()
    response = hop_router(hop_timeout=0.2).search("q", timeout=5)
    assert response.provider == "healthy"
    assert time.monotonic() - started < 2.0
    assert response.decision is not None
    assert [hop.id for hop in response.decision.hops] == ["search:hangs"]


def test_the_hop_timeout_applies_with_no_call_deadline() -> None:
    assert hop_router(hop_timeout=0.2).search("q").provider == "healthy"


async def test_the_async_router_cuts_the_hop_off_and_moves_on() -> None:
    started = time.monotonic()
    response = await hop_router(AsyncWebRouter, hop_timeout=0.2).search("q", timeout=5)
    assert response.provider == "healthy"
    assert time.monotonic() - started < 2.0


def test_by_default_a_hop_gets_half_the_time_left() -> None:
    started = time.monotonic()
    assert hop_router().search("q", timeout=1).provider == "healthy"
    assert 0.4 < time.monotonic() - started < 0.9


def test_the_last_hop_left_gets_all_the_time_left() -> None:
    """``slow`` answers in 0.35 s: past a quarter of the call, so only an uncapped last hop answers."""
    assert hop_router(chain=("hangs", "slow")).search("q", timeout=1).provider == "slow"


def test_with_no_call_deadline_the_default_caps_nothing() -> None:
    assert hop_router(chain=("slow", "healthy")).search("q").provider == "slow"


def test_hop_timeout_false_lets_the_hung_member_take_the_whole_deadline() -> None:
    with pytest.raises(DeadlineExceededError):
        hop_router(hop_timeout=False).search("q", timeout=0.3)


def test_the_call_deadline_still_wins_when_it_comes_first() -> None:
    with pytest.raises(DeadlineExceededError) as caught:
        hop_router(hop_timeout=5).search("q", timeout=0.3)
    assert "hop timeout" not in str(caught.value), "the call's deadline, not a hop cut off with time to move on"


def test_a_call_deadline_beating_a_fixed_hop_timeout_never_counts_towards_a_cooldown() -> None:
    router = hop_router(hop_timeout=5)
    for _ in range(TIMEOUT_COOLDOWN_AFTER):
        with pytest.raises(DeadlineExceededError):
            router.search("q", timeout=0.2)
    assert router._runtime.cooldowns.remaining("hangs") is None


def test_a_hop_cut_off_is_a_timeout_failure_naming_the_cap() -> None:
    with pytest.raises(AllProvidersFailedError) as caught:
        hop_router(chain=("hangs", "fails"), hop_timeout=0.2).search("q", timeout=5)
    failure = caught.value.failures["hangs"]
    assert failure.kind == ERROR_KIND_TIMEOUT
    assert "hop timeout of 0.2s" in str(failure)


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan"), True, "3"])
def test_hop_timeout_refuses_what_is_not_seconds_above_zero(value: Any) -> None:
    with pytest.raises(InvalidParameterError):
        WebRouter(hop_timeout=value)
