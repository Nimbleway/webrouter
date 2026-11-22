"""``timeout_cooldown``, on by default: a backend that timed out several calls in a row is passed over for a while."""

from __future__ import annotations

from typing import Any

import pytest

from web_router import ProviderError
from web_router.constants_production import TIMEOUT_COOLDOWN_AFTER, TIMEOUT_COOLDOWN_S
from web_router.exceptions import DeadlineExceededError
from web_router.providers import _cooldown
from web_router.providers._cooldown import Cooldowns
from web_router.providers._cooldown_timeouts import HopTimeoutError
from web_router.types import SearchResponse
from web_router.types_errors import ERROR_KIND_RATE_LIMIT, ERROR_KIND_TIMEOUT, ERROR_KIND_UPSTREAM


def _timeout() -> ProviderError:
    return ProviderError("slow", "read timed out", kind=ERROR_KIND_TIMEOUT)


def _times_out(cooldowns: Cooldowns, provider: str, times: int) -> None:
    for _ in range(times):
        cooldowns.note(provider, _timeout())


def test_the_last_timeout_in_a_row_starts_the_cooldown() -> None:
    cooldowns = Cooldowns(timeouts=True)
    _times_out(cooldowns, "slow", TIMEOUT_COOLDOWN_AFTER - 1)
    assert cooldowns.remaining("slow") is None
    cooldowns.note("slow", _timeout())
    left = cooldowns.remaining("slow")
    assert left is not None and TIMEOUT_COOLDOWN_S - 1 < left[0] <= TIMEOUT_COOLDOWN_S and left[1] is None
    refusal = cooldowns.refusal("slow")
    assert refusal is not None and f"after {TIMEOUT_COOLDOWN_AFTER} timeouts in a row" in str(refusal)


def _cut(fixed: bool) -> HopTimeoutError:
    return HopTimeoutError("slow", "cut off", original=TimeoutError(), fixed=fixed)


@pytest.mark.parametrize(
    ("timeout", "counts"),
    [
        (lambda: DeadlineExceededError("slow", 2.0), False),
        (lambda: _cut(fixed=False), False),
        (lambda: _cut(fixed=True), True),
        (_timeout, True),
    ],
    ids=["the call's deadline", "a share of the caller's time", "the router's hop_timeout", "the backend's own"],
)
def test_only_the_backends_own_slowness_counts(timeout: Any, counts: bool) -> None:
    cooldowns = Cooldowns(timeouts=True)
    for _ in range(TIMEOUT_COOLDOWN_AFTER):
        cooldowns.note("slow", timeout())
    assert (cooldowns.remaining("slow") is not None) is counts


def test_a_timeout_long_after_the_last_starts_a_new_streak(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [100.0]
    monkeypatch.setattr(_cooldown.time, "monotonic", lambda: now[0])
    cooldowns = Cooldowns(timeouts=True)
    _times_out(cooldowns, "slow", TIMEOUT_COOLDOWN_AFTER - 1)
    now[0] += TIMEOUT_COOLDOWN_S + 1
    cooldowns.note("slow", _timeout())
    assert cooldowns.remaining("slow") is None


def test_a_streak_that_started_a_cooldown_counts_from_zero_after_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """A last backend left is called while it cools down; one more timeout then starts a new count."""
    now = [100.0]
    monkeypatch.setattr(_cooldown.time, "monotonic", lambda: now[0])
    cooldowns = Cooldowns(timeouts=True)
    _times_out(cooldowns, "slow", TIMEOUT_COOLDOWN_AFTER)
    now[0] += 10
    cooldowns.note("slow", _timeout())
    left = cooldowns.remaining("slow")
    assert left is not None and left[0] == pytest.approx(TIMEOUT_COOLDOWN_S - 10), "not restarted"


def test_an_answer_ends_a_timeout_cooldown_but_not_a_throttle() -> None:
    cooldowns = Cooldowns(timeouts=True)
    _times_out(cooldowns, "slow", TIMEOUT_COOLDOWN_AFTER)
    cooldowns.note("throttled", ProviderError("throttled", "HTTP 429", kind=ERROR_KIND_RATE_LIMIT))
    for name in ("slow", "throttled"):
        cooldowns.note_answer(name, SearchResponse(provider=name, query="q", results=[]))
    assert cooldowns.remaining("slow") is None and cooldowns.remaining("throttled") is not None


def test_a_timeout_cooldown_lets_the_last_backend_run_and_says_timeout() -> None:
    cooldowns = Cooldowns(timeouts=True)
    _times_out(cooldowns, "slow", TIMEOUT_COOLDOWN_AFTER)
    refusal = cooldowns.refusal("slow")
    assert refusal is not None and refusal.kind == ERROR_KIND_TIMEOUT
    assert cooldowns.refusal("slow", last=True) is None


def test_a_throttle_still_holds_the_last_backend() -> None:
    cooldowns = Cooldowns()
    cooldowns.note("slow", ProviderError("slow", "HTTP 429", kind=ERROR_KIND_RATE_LIMIT))
    refusal = cooldowns.refusal("slow", last=True)
    assert refusal is not None and refusal.kind == ERROR_KIND_RATE_LIMIT


@pytest.mark.parametrize(
    "between",
    [
        lambda cooldowns: cooldowns.note_answer("slow", SearchResponse(provider="slow", query="q", results=[])),
        lambda cooldowns: cooldowns.note("slow", ProviderError("slow", "HTTP 500", kind=ERROR_KIND_UPSTREAM)),
    ],
    ids=["an answer", "another failure"],
)
def test_anything_but_a_timeout_starts_the_count_again(between: Any) -> None:
    cooldowns = Cooldowns(timeouts=True)
    _times_out(cooldowns, "slow", TIMEOUT_COOLDOWN_AFTER - 1)
    between(cooldowns)
    _times_out(cooldowns, "slow", TIMEOUT_COOLDOWN_AFTER - 1)
    assert cooldowns.remaining("slow") is None


def test_timeouts_start_no_cooldown_when_switched_off() -> None:
    cooldowns = Cooldowns(timeouts=False)
    _times_out(cooldowns, "slow", TIMEOUT_COOLDOWN_AFTER * 2)
    assert cooldowns.remaining("slow") is None


def test_a_pinned_mode_counts_towards_its_backend() -> None:
    cooldowns = Cooldowns(timeouts=True)
    _times_out(cooldowns, "exa:exa-deep", TIMEOUT_COOLDOWN_AFTER - 1)
    cooldowns.note("exa", _timeout())
    assert cooldowns.remaining("exa:exa-instant") is not None


def _summed(*errors: BaseException) -> ProviderError:
    summed = ProviderError("slow", "every URL failed", kind=ERROR_KIND_TIMEOUT)
    summed.call_errors = errors
    return summed


def test_a_throttle_among_an_extracts_calls_cools_it_at_once() -> None:
    cooldowns = Cooldowns(timeouts=True)
    cooldowns.note("slow", _summed(ProviderError("slow", "HTTP 429", kind=ERROR_KIND_RATE_LIMIT), _timeout()))
    left = cooldowns.remaining("slow")
    assert left is not None and left[1] is None
    refusal = cooldowns.refusal("slow")
    assert refusal is not None and refusal.kind == ERROR_KIND_RATE_LIMIT


@pytest.mark.parametrize(
    ("errors", "counts"),
    [((DeadlineExceededError("slow", 2.0), _timeout()), False), ((_timeout(), _timeout()), True)],
    ids=["one call cut off by the caller's deadline", "every call's own timeout"],
)
def test_an_extracts_timeouts_count_only_when_every_call_was_its_own(errors: Any, counts: bool) -> None:
    cooldowns = Cooldowns(timeouts=True)
    for _ in range(TIMEOUT_COOLDOWN_AFTER):
        cooldowns.note("slow", _summed(*errors))
    assert (cooldowns.remaining("slow") is not None) is counts
