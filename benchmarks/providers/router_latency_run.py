"""router-latency's measurements: routers timed interleaved, cold builds, and a routed call's added wall time.

Every router is called through the library's own
:class:`~web_router.routing.invoke.AttachedRouter`, so a sample is exactly the
``router_ms`` a decision record would hold, thread or loop hop included. Each
round calls every router once, in a seeded random order, so drift over the
session lands on all of them alike. A call is given the router's own deadline
-- its ``timeout_s``, as a routed call is -- unless ``--deadline-s`` sets
another; a call that hits it is **censored**: counted, never used as a value.
The added wall time is a stubbed search through ``auto`` with the router
attached against the same with ``route_tools=()``, interleaved, so candidate
building, pricing and the hop are in it and no backend's noise is. Imported as
a sibling, by name, as ``_common`` is.

Experimental until 1.0, like the router contract it measures.
"""

from __future__ import annotations

import asyncio
import math
import random
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import Any

from _common import _pct

from web_router import AsyncWebRouter, WebRouter, registry
from web_router.providers.base import SearchProvider
from web_router.routing.contract import RouteRequest
from web_router.routing.invoke import AttachedRouter, RouterOutcome
from web_router.types import NORMALIZED_PARAMETERS, SearchRequest, SearchResponse

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
        # A timeout or an error gave no answer: each ranks above every answer, so a router that mostly
        # fails has no p50 to weigh, however fast its few answers were.
        ranked = sorted(self.samples_ms) + [math.inf] * (self.censored + sum(self.errors.values()))
        p50, p90 = (_pct(ranked, f) for f in (P50, P90))
        return {
            "calls": calls,
            "answered": len(self.samples_ms),
            "timeouts": self.censored,
            "errors": dict(self.errors),
            "timeout_rate": self.censored / calls if calls else None,
            "fallback_rate": (calls - len(self.samples_ms)) / calls if calls else None,
            "p50_ms": None if p50 is None or math.isinf(p50) else p50,
            "p90_ms": None if p90 is None or math.isinf(p90) else p90,
            "min_ms": min(self.samples_ms, default=None),
            "max_ms": max(self.samples_ms, default=None),
        }


def _deadline(attached: AttachedRouter, override: float | None) -> float:
    return attached.timeout_s if override is None else override


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
    use_async: bool,
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

    if use_async:
        asyncio.run(arun())
    else:
        for i, name, req in calls:
            keep(i, name, attached[name].call(req))
    return {name: {"first_call": first.stats(), "warm": warm.stats()} for name, (first, warm) in series.items()}


def cold_builds(
    builders: Mapping[str, Any],
    plan: Sequence[RouteRequest],
    *,
    deadline_s: float | None,
    seed: int,
    use_async: bool,
) -> dict[str, dict[str, Any]]:
    """Each router built anew for every call of ``plan``: what a short-lived process pays on each one.

    The call's time is the first call's on a fresh router; ``build_*`` is the build's own (a model load, a client).
    """
    from router_latency_routers import close

    calls: dict[str, Series] = {name: Series() for name in builders}
    builds: dict[str, list[float]] = {name: [] for name in builders}

    def built(name: str) -> Any:
        started = time.perf_counter()
        router = builders[name]()
        builds[name].append((time.perf_counter() - started) * 1000)
        return router

    def ready(router: Any, req: RouteRequest) -> tuple[AttachedRouter, RouteRequest]:
        attached = AttachedRouter(router)  # inside the caller's try: a router it refuses is still closed
        return attached, replace(req, deadline_s=_deadline(attached, deadline_s))

    def fresh() -> Iterator[tuple[str, Any, RouteRequest]]:
        """Each call's router, built just before it, in the seeded order."""
        for i, name in _rounds(random.Random(seed), list(builders), len(plan)):
            yield name, built(name), plan[i]

    async def arun() -> None:
        for name, router, req in fresh():
            try:
                attached, req = ready(router, req)
                calls[name].add(await attached.acall(req))
            finally:
                close(router)

    if use_async:
        asyncio.run(arun())
    else:
        for name, router, req in fresh():
            try:
                attached, req = ready(router, req)
                calls[name].add(attached.call(req))
            finally:
                close(router)
    return {
        name: {**s.stats(), "build_p50_ms": _pct(builds[name], P50), "build_p90_ms": _pct(builds[name], P90)}
        for name, s in calls.items()
    }


class _Stub(SearchProvider):
    """A search backend that answers at once with nothing: the call's own cost, with no backend in it."""

    name = "stub"
    supported_parameters = NORMALIZED_PARAMETERS

    def _search(self, request: SearchRequest) -> SearchResponse:
        return SearchResponse(provider=self.name, query=request.query, results=[])

    async def _asearch(self, request: SearchRequest) -> SearchResponse:
        return self._search(request)


@contextmanager
def stubbed(chain: Sequence[str]) -> Iterator[None]:
    """Every backend of ``chain`` replaced by a stub of the same name, so candidates are priced as the real ones."""
    before = {name: registry._CUSTOM_PROVIDERS.get(name) for name in chain}  # a backend of your own, put back
    for name in chain:
        registry.register_provider(name, type(f"Stub_{name}", (_Stub,), {"name": name}))
    try:
        yield
    finally:
        for name, kept in before.items():
            if kept is None:
                registry.unregister_provider(name)
            else:
                registry.register_provider(name, kept)


def _clients(cls: type, router: Any, chain: Sequence[str]) -> tuple[Any, Any]:
    config = {"auto": {"chain": list(chain)}}
    routed = cls(provider_config=config, router=router, route_tools=("search",))  # whatever the router declares
    return routed, cls(provider_config=config, router=router, route_tools=())


@dataclass
class _Paired:
    """Routed and unrouted times per router, and their paired differences, past the warm-up."""

    warmup: int
    times: dict[str, dict[str, list[float]]] = field(default_factory=dict)

    def record(self, i: int, name: str, took: Mapping[int, float]) -> None:
        if i < self.warmup:
            return
        t = self.times.setdefault(name, {"routed": [], "unrouted": [], "added": []})
        t["routed"].append(took[0])
        t["unrouted"].append(took[1])
        t["added"].append(took[0] - took[1])

    def summary(self) -> dict[str, dict[str, Any]]:
        keys = ("routed", "unrouted", "added")
        return {
            name: {
                "calls": len(t["added"]),
                **{f"{key}_p50_ms": _pct(t[key], P50) for key in keys},
                **{f"{key}_p90_ms": _pct(t[key], P90) for key in keys},
            }
            for name, t in self.times.items()
        }


def _pair(rng: random.Random, clients: tuple[Any, Any]) -> list[tuple[int, Any]]:
    """The routed (0) and unrouted (1) client in a random order."""
    pair = list(enumerate(clients))
    rng.shuffle(pair)
    return pair


def _timed(clients: Mapping[str, tuple[Any, Any]], prompts: Sequence[str], paired: _Paired,
           rng: random.Random) -> Iterator[tuple[Any, str]]:  # fmt: skip
    """Each search to make, as a client and its prompt; the time from yielding it to resuming is the call's."""
    for i, name in _rounds(rng, list(clients), len(prompts)):
        took = {}
        for which, client in _pair(rng, clients[name]):
            started = time.perf_counter()
            yield client, prompts[i]
            took[which] = (time.perf_counter() - started) * 1000
        paired.record(i, name, took)


def _e2e_sync(routers: Mapping[str, Any], prompts: Sequence[str], chain: Sequence[str], paired: _Paired,
              rng: random.Random) -> None:  # fmt: skip
    clients = {name: _clients(WebRouter, router, chain) for name, router in routers.items()}
    for client, prompt in _timed(clients, prompts, paired, rng):
        client.search(prompt)
    for both in clients.values():
        for client in both:
            client.close()


async def _e2e_async(routers: Mapping[str, Any], prompts: Sequence[str], chain: Sequence[str], paired: _Paired,
                     rng: random.Random) -> None:  # fmt: skip
    clients = {name: _clients(AsyncWebRouter, router, chain) for name, router in routers.items()}
    for client, prompt in _timed(clients, prompts, paired, rng):
        await client.search(prompt)
    for both in clients.values():
        for client in both:
            await client.aclose()


def added_wall_time(
    routers: Mapping[str, Any],
    prompts: Sequence[str],
    chain: Sequence[str],
    *,
    warmup: int,
    seed: int,
    use_async: bool,
) -> dict[str, dict[str, Any]]:
    """A stubbed ``search`` routed against the same unrouted, per router: both p50s and the paired difference's."""
    paired = _Paired(warmup)
    rng = random.Random(seed)
    with stubbed(chain):
        if use_async:
            asyncio.run(_e2e_async(routers, prompts, chain, paired, rng))
        else:
            _e2e_sync(routers, prompts, chain, paired, rng)
    return paired.summary()
