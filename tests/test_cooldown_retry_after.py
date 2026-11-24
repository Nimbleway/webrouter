"""How long a throttle's ``Retry-After`` cools a backend: the value, the status's default, the cap, and a nan."""

from __future__ import annotations

import pytest

from tests.test_cooldown import _throttle
from web_router.providers import _cooldown
from web_router.providers._cooldown import Cooldowns


def test_a_429_with_retry_after_cools_the_backend_for_that_long(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [100.0]
    monkeypatch.setattr(_cooldown.time, "monotonic", lambda: now[0])
    cooldowns = Cooldowns()
    cooldowns.note("tavily", _throttle(429, "12"))
    assert cooldowns.remaining("tavily") == (12.0, 429)
    now[0] += 12.5
    assert cooldowns.remaining("tavily") is None


@pytest.mark.parametrize(("status", "seconds"), [(429, 10.0), (402, 60.0)])
def test_a_throttle_without_retry_after_cools_for_the_statuss_default(status: int, seconds: float) -> None:
    cooldowns = Cooldowns()
    cooldowns.note("you-mcp", _throttle(status))
    left = cooldowns.remaining("you-mcp")
    assert left is not None and left[1] == status and seconds - 1 < left[0] <= seconds


def test_a_retry_after_past_the_cap_is_held_to_it_and_other_failures_set_none() -> None:
    cooldowns = Cooldowns()
    cooldowns.note("a", _throttle(429, "86400"))
    cooldowns.note("b", _throttle(503))
    cooldowns.note("c", ValueError("not a provider error"))
    left = cooldowns.remaining("a")
    assert left is not None and left[0] <= 600.0
    assert cooldowns.remaining("b") is None and cooldowns.remaining("c") is None


@pytest.mark.parametrize(("wait", "cap"), [(float("nan"), 10.0), (float("inf"), 600.0)])
def test_a_nan_retry_after_falls_back_but_infinity_stays_capped(wait: float, cap: float) -> None:
    """A nan would pin the backend forever; infinity still means the cap, as it did before."""
    cooldowns = Cooldowns()
    error = _throttle(429)
    error.retry_after = wait
    cooldowns.note("tavily", error)
    left = cooldowns.remaining("tavily")
    assert left is not None and left[1] == 429 and cap - 1 < left[0] <= cap
