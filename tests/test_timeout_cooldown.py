"""``timeout_cooldown=True``: a backend that timed out several calls in a row is passed over for a while."""

from __future__ import annotations

from typing import Any

import pytest

from tests.hop_helpers import _Hangs, hop_members, hop_router
from web_router import ProviderError
from web_router.constants_production import TIMEOUT_COOLDOWN_AFTER, TIMEOUT_COOLDOWN_S
from web_router.exceptions import DeadlineExceededError
from web_router.providers._cooldown import Cooldowns
from web_router.types import SearchResponse
from web_router.types_errors import ERROR_KIND_TIMEOUT, ERROR_KIND_UPSTREAM


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


def test_the_deadline_cutting_a_backend_off_counts_as_a_timeout() -> None:
    cooldowns = Cooldowns(timeouts=True)
    for _ in range(TIMEOUT_COOLDOWN_AFTER):
        cooldowns.note("slow", DeadlineExceededError("slow", 2.0))
    assert cooldowns.remaining("slow") is not None


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


def test_timeouts_start_no_cooldown_unless_the_router_asked() -> None:
    cooldowns = Cooldowns()
    _times_out(cooldowns, "slow", TIMEOUT_COOLDOWN_AFTER * 2)
    assert cooldowns.remaining("slow") is None


def test_a_pinned_mode_counts_towards_its_backend() -> None:
    cooldowns = Cooldowns(timeouts=True)
    _times_out(cooldowns, "exa:exa-deep", TIMEOUT_COOLDOWN_AFTER - 1)
    cooldowns.note("exa", _timeout())
    assert cooldowns.remaining("exa:exa-instant") is not None


@pytest.mark.usefixtures(hop_members.__name__)
def test_auto_passes_a_backend_over_after_it_hung_calls_in_a_row() -> None:
    router = hop_router(hop_timeout=0.1, timeout_cooldown=True)
    for _ in range(TIMEOUT_COOLDOWN_AFTER):
        assert router.search("q", timeout=5).provider == "healthy"
    assert _Hangs.calls == TIMEOUT_COOLDOWN_AFTER

    response = router.search("q", timeout=5)
    assert response.provider == "healthy" and _Hangs.calls == TIMEOUT_COOLDOWN_AFTER, "not called while cooling"
    assert response.decision is not None
    assert [hop.error for hop in response.decision.hops] == ["cooling_down"]
