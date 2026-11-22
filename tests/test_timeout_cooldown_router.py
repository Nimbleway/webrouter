"""The cooldown after timeouts on a router: which timeouts count, and which backends it passes over."""

from __future__ import annotations

import pytest

from tests.hop_helpers import _Hangs, _TimesOut, hop_members, hop_router
from web_router import AllProvidersFailedError, AsyncWebRouter, DeadlineExceededError, ProviderError
from web_router.constants_production import TIMEOUT_COOLDOWN_AFTER
from web_router.types_errors import ERROR_KIND_RATE_LIMIT, ERROR_KIND_TIMEOUT

pytestmark = pytest.mark.usefixtures(hop_members.__name__)


def test_auto_passes_a_backend_over_after_it_hung_calls_in_a_row() -> None:
    router = hop_router(hop_timeout=0.1)  # the cooldown is on by default
    for _ in range(TIMEOUT_COOLDOWN_AFTER):
        assert router.search("q", timeout=5).provider == "healthy"
    assert _Hangs.calls == TIMEOUT_COOLDOWN_AFTER

    response = router.search("q", timeout=5)
    assert response.provider == "healthy" and _Hangs.calls == TIMEOUT_COOLDOWN_AFTER, "not called while cooling"
    assert response.decision is not None
    assert [hop.error for hop in response.decision.hops] == ["cooling_down"]


def test_timeout_cooldown_false_keeps_calling_a_backend_that_hangs() -> None:
    router = hop_router(hop_timeout=0.1, timeout_cooldown=False)
    for _ in range(TIMEOUT_COOLDOWN_AFTER + 1):
        router.search("q", timeout=5)
    assert _Hangs.calls == TIMEOUT_COOLDOWN_AFTER + 1


async def test_the_async_router_cools_a_hanging_backend_down_by_default() -> None:
    router = hop_router(AsyncWebRouter, hop_timeout=0.1)
    for _ in range(TIMEOUT_COOLDOWN_AFTER + 1):
        assert (await router.search("q", timeout=5)).provider == "healthy"
    assert _Hangs.calls == TIMEOUT_COOLDOWN_AFTER


def test_a_callers_short_timeout_does_not_cool_a_healthy_backend_for_others() -> None:
    """``slow`` answers in 0.35 s: cut at its 0.25 s share of a 0.5 s call, yet it is not slow by its own measure."""
    router = hop_router(chain=("slow", "healthy"))
    for _ in range(TIMEOUT_COOLDOWN_AFTER):
        assert router.search("q", timeout=0.5).provider == "healthy"
    assert router._runtime.cooldowns.remaining("slow") is None
    assert router.search("q", timeout=10).provider == "slow"


async def test_the_call_deadline_cutting_an_async_hop_off_does_not_count() -> None:
    router = hop_router(AsyncWebRouter, chain=("hangs",), hop_timeout=False)
    for _ in range(TIMEOUT_COOLDOWN_AFTER):
        with pytest.raises(DeadlineExceededError):
            await router.search("q", timeout=0.2)
    assert router._runtime.cooldowns.remaining("hangs") is None


def test_the_last_backend_left_is_called_after_its_timeouts() -> None:
    router = hop_router(chain=("timesout",))
    for _ in range(TIMEOUT_COOLDOWN_AFTER + 1):
        with pytest.raises(AllProvidersFailedError):
            router.search("q", timeout=5)
    assert _TimesOut.calls == TIMEOUT_COOLDOWN_AFTER + 1, "never refused without a try"


def test_a_backend_passed_over_after_timeouts_is_a_timeout_failure() -> None:
    router = hop_router(chain=("timesout", "fails"))
    for _ in range(TIMEOUT_COOLDOWN_AFTER):
        with pytest.raises(AllProvidersFailedError):
            router.search("q", timeout=5)
    with pytest.raises(AllProvidersFailedError) as caught:
        router.search("q", timeout=5)
    assert _TimesOut.calls == TIMEOUT_COOLDOWN_AFTER
    assert caught.value.failures["timesout"].kind == ERROR_KIND_TIMEOUT


@pytest.mark.parametrize(("cooling", "answers"), [(True, "slow"), (False, "healthy")])
def test_a_hop_whose_only_fallbacks_cool_down_gets_all_the_time_left(cooling: bool, answers: str) -> None:
    router = hop_router(chain=("slow", "healthy"))
    if cooling:
        router._runtime.cooldowns.note("healthy", ProviderError("healthy", "HTTP 429", kind=ERROR_KIND_RATE_LIMIT))
    assert router.search("q", timeout=0.5).provider == answers
