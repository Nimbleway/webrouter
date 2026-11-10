"""router-latency, offline: censoring, the interleaved timing and the fresh builds."""

from __future__ import annotations

import asyncio
import random
import time
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from tests.benchmarks._router_latency_fakes import (
    Closing,
    latency_fixture,  # noqa: F401 -- the latency fixture
    module,
    req,
)
from web_router import RouteDecision, RouteRequest


def test_a_timeout_is_censored_never_a_value(latency: ModuleType) -> None:
    run = module("router_latency_run")
    series = run.Series()
    for ms in (1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0):
        series.samples_ms.append(ms)
    series.censored = 2
    stats = series.stats()
    assert stats["p50_ms"] == 5.0
    assert stats["p90_ms"] is None, "the 90th rank of 10 is a timeout"
    assert stats["timeout_rate"] == 0.2 and stats["max_ms"] == 8.0


def test_an_error_ranks_above_every_answer_like_a_timeout(latency: ModuleType) -> None:
    series = module("router_latency_run").Series(samples_ms=[1.0], errors={"router_error": 3})
    assert series.stats()["p50_ms"] is None, "one fast answer among failures is no p50"
    half = module("router_latency_run").Series(samples_ms=[10.0], censored=1)
    assert half.stats()["p50_ms"] is None, "half the calls failed: no p50, as verdict() says"


def test_routers_are_timed_interleaved_first_call_and_warm_up_apart(latency: ModuleType) -> None:
    calls: list[str] = []

    def fast(req: RouteRequest) -> RouteDecision:
        calls.append("fast")
        return RouteDecision()

    def slow(req: RouteRequest) -> RouteDecision:
        calls.append("slow")
        time.sleep(0.05)
        return RouteDecision()

    plan = [req()] * 6
    out = module("router_latency_run").interleaved(
        {"fast": fast, "slow": slow}, plan, warmup=2, deadline_s=0.01, seed=0, loop=None
    )
    assert out["fast"]["first_call"]["answered"] == 1
    assert out["fast"]["warm"]["calls"] == 3 and out["fast"]["warm"]["fallback_rate"] == 0.0
    assert out["slow"]["warm"]["timeouts"] == 3 and out["slow"]["warm"]["p50_ms"] is None


def test_each_round_is_in_its_own_random_order(latency: ModuleType) -> None:
    rounds = list(module("router_latency_run")._rounds(random.Random(0), ["a", "b", "c"], 12))
    orders = {tuple(name for i, name in rounds if i == r) for r in range(12)}
    assert len(orders) > 1 and all(sorted(order) == ["a", "b", "c"] for order in orders)


def test_the_async_timing_runs_from_an_event_loop(latency: ModuleType) -> None:
    async def aroute(req: RouteRequest) -> RouteDecision:
        return RouteDecision()

    run = module("router_latency_run")
    loop = asyncio.new_event_loop()
    try:
        out = run.interleaved({"a": aroute}, [req()] * 3, warmup=1, deadline_s=1.0, seed=0, loop=loop)
    finally:
        loop.close()
    assert out["a"]["warm"]["answered"] == 1


def test_deadline_s_only_shortens_a_routers_own_deadline() -> None:
    deadline = module("router_latency_run")._deadline
    attached = SimpleNamespace(timeout_s=1.0)
    assert deadline(attached, None) == 1.0
    assert deadline(attached, 0.25) == 0.25
    assert deadline(attached, 5.0) == 1.0  # never past the router's real limit


def test_a_freshly_built_router_the_library_refuses_is_closed(latency: ModuleType) -> None:
    log: list[str] = []

    def refused() -> Any:
        router = Closing(log, "bad")
        router.timeout_s = -1  # type: ignore[attr-defined]
        return router

    with pytest.raises(Exception, match="timeout_s"):
        module("router_latency_run").cold_builds({"bad": refused}, [req()], deadline_s=None, seed=0, loop=None)
    assert log == ["bad"]
