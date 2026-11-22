"""A mode that times out moves to its backend's next mode only when that mode's p90 fits the time left.

Offline: Exa's API and Tavily are faked (:mod:`tests.attempts_helpers`); nothing is sent.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from tests.attempts_helpers import INSTANT, TAVILY, Reply, fake_backends, price, routed
from web_router import AsyncWebRouter, Budget
from web_router.prices import estimate_latency
from web_router.providers import _mode_timeout
from web_router.providers._deadline import Deadline, call_deadline
from web_router.routing.failure_kind import BACKEND_FAILED

REASONING = "search:exa:exa-deep-reasoning"
_TIMEOUTS: list[Reply] = [httpx.ReadTimeout("read timed out"), 408]


def _p90(mode: str) -> float:
    measured = estimate_latency("exa", "search", mode)
    assert measured is not None, f"{mode} has a measured p90"
    return measured[1]


def _hops(response: Any) -> dict[str, str | None]:
    return {hop.id: hop.error for hop in (response.decision.hops if response.decision else None) or ()}


@pytest.mark.parametrize("reply", _TIMEOUTS, ids=["read-timeout", "408"])
def test_a_timed_out_mode_moves_to_a_next_mode_that_fits_the_time_left(
    monkeypatch: pytest.MonkeyPatch, reply: Reply
) -> None:
    calls = fake_backends(monkeypatch, reply)
    with routed(REASONING, INSTANT, TAVILY) as wr:
        response = wr.search("q", timeout=30)
    assert (calls.exa, calls.tavily, response.provider) == (["deep-reasoning", "instant"], [], "exa")
    assert BACKEND_FAILED not in _hops(response).values()


async def test_the_async_walk_moves_to_it_the_same(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = fake_backends(monkeypatch, httpx.ReadTimeout("read timed out"))
    async with routed(REASONING, INSTANT, TAVILY, cls=AsyncWebRouter) as wr:
        response = await wr.search("q", timeout=30)
    assert (calls.exa, response.provider) == (["deep-reasoning", "instant"], "exa")


def _unmeasured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_mode_timeout, "estimate_latency", lambda *args: None)


def _no_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """The first mode spent the call's time: less than ``exa-instant``'s p90 is left once it times out."""
    short = Deadline(at=time.monotonic() + _p90("exa-instant") / 2, seconds=30)
    monkeypatch.setattr(_mode_timeout, "current_deadline", lambda: short)


@pytest.mark.parametrize("unfit", [_unmeasured, _no_time], ids=["unknown-p90", "p90-over-time-left"])
def test_a_next_mode_that_does_not_fit_stops_the_backend(monkeypatch: pytest.MonkeyPatch, unfit: Any) -> None:
    calls = fake_backends(monkeypatch, httpx.ReadTimeout("read timed out"))
    unfit(monkeypatch)
    with routed(REASONING, INSTANT, TAVILY) as wr:
        response = wr.search("q", timeout=30)
    assert (calls.exa, len(calls.tavily), response.provider) == (["deep-reasoning"], 1, "tavily")
    assert _hops(response)[INSTANT] == BACKEND_FAILED


def test_without_a_time_limit_a_timeout_stops_the_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = fake_backends(monkeypatch, httpx.ReadTimeout("read timed out"))
    with routed(REASONING, INSTANT, TAVILY) as wr:
        response = wr.search("q")
    assert (calls.exa, response.provider) == (["deep-reasoning"], "tavily")


def test_the_timed_out_attempt_stays_charged(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_backends(monkeypatch, httpx.ReadTimeout("read timed out"))
    with routed(REASONING, INSTANT, TAVILY) as wr:
        response = wr.search("q", budget=Budget(max_cost_usd=1.0, time_limit_s=30))
    record = response.decision
    charged = (price("exa-deep-reasoning") or 0) + (price("exa-instant") or 0)
    assert record is not None and price("exa-deep-reasoning")
    assert record.estimated_cost_usd == pytest.approx(charged), "a timeout is not refunded; only a 400/422 is"


def test_a_job_walk_or_a_failure_other_than_a_timeout_never_moves_on() -> None:
    order = [(0, "exa", "exa-deep-reasoning"), (1, "tavily", None), (0, "exa", "exa-instant")]
    timeout = SimpleNamespace(kind="timeout")
    with call_deadline(30):
        assert _mode_timeout.next_mode_fits(_walk(order, hops=True), 0, "exa-deep-reasoning", timeout)
        assert not _mode_timeout.next_mode_fits(_walk(order, hops=False), 0, "exa-deep-reasoning", timeout)
        assert not _mode_timeout.next_mode_fits(_walk(order), 0, "exa-deep-reasoning", SimpleNamespace(kind="upstream"))
        assert not _mode_timeout.next_mode_fits(_walk(order), 0, "exa-instant", timeout), "the last of its backend"
    assert _mode_timeout.next_at(order, 0, "exa-deep-reasoning") == ("exa", "exa-instant")


def _walk(order: list[tuple[int, str, str | None]], *, hops: bool = True) -> Any:
    return SimpleNamespace(hops=hops, order=order, tool="search")
