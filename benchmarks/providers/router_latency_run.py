"""router-latency's measurements: routers timed interleaved, and each built fresh for a call.

Every router is called through the library's own
:class:`~web_router.routing.invoke.AttachedRouter`, so a sample is exactly the
``router_ms`` a decision record would hold, thread or loop hop included. Each
round calls every router once, in a seeded random order, so drift over the
session lands on all of them alike. A call is given the router's own deadline
-- its ``timeout_s``, as a routed call is -- or ``--deadline-s`` when that is
shorter; a call that hits it is **censored**: counted, never used as a value.
An async run passes one event loop as ``loop`` to every phase, so a router
keeps its connections on one loop and is closed on it. The added wall time is
in ``router_latency_e2e``. Imported as a sibling, by name, as ``_common`` is.

Experimental until 1.0, like the router contract it measures.
"""

from __future__ import annotations

import asyncio
import math
import random
import time
from collections.abc import Coroutine, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from _common import _pct

from web_router.routing.contract import RouteRequest
from web_router.routing.invoke import AttachedRouter, RouterOutcome
from web_router.routing.registry import aclose_router, discard_router

TIMEOUT = "router_timeout"
P50, P90 = 0.5, 0.9


@dataclass
class Series:
    """One router's samples in milliseconds: answered calls only; timeouts and errors counted beside them."""

    samples_ms: list[float] = field(default_factory=list)
    censored: int = 0
    errors: dict[str, int] = field(default_factory=dict)

    def add(self, outcome: RouterOutcome) -> None:
        if outcome.failure is None:
            self.samples_ms.append(outcome.elapsed_ms)
        elif outcome.failure == TIMEOUT:
            self.censored += 1
        else:
            self.errors[outcome.failure] = self.errors.get(outcome.failure, 0) + 1

    def stats(self) -> dict[str, Any]:
        """p50 and p90 with every timeout and error ranked above every answer; ``None`` where that rank is one."""
        calls = len(self.samples_ms) + self.censored + sum(self.errors.values())
        # A timeout or an error gave no answer: each ranks above every answer, so a router that fails on half
        # its calls or more has no p50 to weigh, however fast its answers were -- exactly half included, which
        # the nearest rank would give the slowest answer.
        ranked = sorted(self.samples_ms) + [math.inf] * (self.censored + sum(self.errors.values()))
        p50, p90 = (_pct(ranked, f) for f in (P50, P90))
        return {
            "calls": calls,
            "answered": len(self.samples_ms),
            "timeouts": self.censored,
            "errors": dict(self.errors),
            "timeout_rate": self.censored / calls if calls else None,
            "fallback_rate": (calls - len(self.samples_ms)) / calls if calls else None,
            "p50_ms": None if p50 is None or math.isinf(p50) or 2 * len(self.samples_ms) <= calls else p50,
            "p90_ms": None if p90 is None or math.isinf(p90) else p90,
            "min_ms": min(self.samples_ms, default=None),
            "max_ms": max(self.samples_ms, default=None),
        }


def _deadline(attached: AttachedRouter, override: float | None) -> float:
    return attached.timeout_s if override is None else min(override, attached.timeout_s)


def _rounds(rng: random.Random, names: Sequence[str], count: int) -> Iterator[tuple[int, str]]:
    """``count`` rounds of every name, each round in a fresh random order."""
    for i in range(count):
        order = list(names)
        rng.shuffle(order)
        for name in order:
            yield i, name


def interleaved(
    routers: Mapping[str, Any],
    plan: Sequence[RouteRequest],
    *,
    warmup: int,
    deadline_s: float | None,
    seed: int,
    loop: asyncio.AbstractEventLoop | None,
) -> dict[str, dict[str, Any]]:
    """Each router called once per request of ``plan``, in a random order per round.

    The first call of each is its **cold** call, reported apart; the next ``warmup`` are dropped; the rest
    are the series.
    """
    attached = {name: AttachedRouter(router) for name, router in routers.items()}
    series = {name: (Series(), Series()) for name in routers}  # first call, warm
    calls = [
        (i, name, replace(plan[i], deadline_s=_deadline(attached[name], deadline_s)))
        for i, name in _rounds(random.Random(seed), list(attached), len(plan))
    ]

    def keep(i: int, name: str, outcome: RouterOutcome) -> None:
        if i == 0 or i > warmup:
            series[name][0 if i == 0 else 1].add(outcome)

    async def arun() -> None:
        for i, name, req in calls:
            keep(i, name, await attached[name].acall(req))

    if loop is not None:
        loop.run_until_complete(arun())
    else:
        for i, name, req in calls:
            keep(i, name, attached[name].call(req))
    return {name: {"first_call": first.stats(), "warm": warm.stats()} for name, (first, warm) in series.items()}


def _now(step: Coroutine[Any, Any, None]) -> None:
    """Run a coroutine that never suspends -- a sync run's step -- to its end, with no event loop."""
    try:
        step.send(None)
    except StopIteration:
        return
    step.close()
    raise RuntimeError("a sync step awaited something")


def cold_builds(
    builders: Mapping[str, Any],
    plan: Sequence[RouteRequest],
    *,
    deadline_s: float | None,
    seed: int,
    loop: asyncio.AbstractEventLoop | None,
) -> dict[str, dict[str, Any]]:
    """Each router built anew for every call of ``plan``: what a short-lived process pays on each one.

    The call's time is the first call's on a fresh router; ``build_*`` is the build's own (a model load, a client).
    """
    calls: dict[str, Series] = {name: Series() for name in builders}
    builds: dict[str, list[float]] = {name: [] for name in builders}

    async def step(name: str, req: RouteRequest) -> None:
        """Build, call once and close: awaiting only in an async run, on its loop, which its connections use."""
        started = time.perf_counter()
        router = builders[name]()
        builds[name].append((time.perf_counter() - started) * 1000)
        try:
            attached = AttachedRouter(router)  # inside the try: a router it refuses is still closed
            req = replace(req, deadline_s=_deadline(attached, deadline_s))
            calls[name].add(attached.call(req) if loop is None else await attached.acall(req))
        finally:
            if loop is None:
                discard_router(router)
            else:
                await aclose_router(router)

    steps = (step(name, plan[i]) for i, name in _rounds(random.Random(seed), list(builders), len(plan)))
    if loop is not None:

        async def arun() -> None:
            for one in steps:
                await one

        loop.run_until_complete(arun())
    else:
        for one in steps:
            _now(one)
    return {
        name: {**s.stats(), "build_p50_ms": _pct(builds[name], P50), "build_p90_ms": _pct(builds[name], P90)}
        for name, s in calls.items()
    }
