"""router-latency's added wall time: a stubbed search routed against the same unrouted, per router.

The search goes through ``auto`` with the router attached against the same
with ``route_tools=()``, interleaved, so candidate building, pricing and the
hop are in it and no backend's noise is. It always runs under the router's own
``timeout_s``. Imported as a sibling, by name, as ``_common`` is.

Experimental until 1.0, like the router contract it measures.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from _common import _pct
from router_latency_run import P50, P90, _rounds

from web_router import AsyncWebRouter, WebRouter, registry
from web_router.providers.base import SearchProvider
from web_router.types import NORMALIZED_PARAMETERS, SearchRequest, SearchResponse


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
    clients: dict[str, Any] = {}
    try:  # one that fails to build or to search still closes those built before it
        for name, router in routers.items():
            clients[name] = _clients(WebRouter, router, chain)
        for client, prompt in _timed(clients, prompts, paired, rng):
            client.search(prompt)
    finally:
        for both in clients.values():
            for client in both:
                client.close()


async def _e2e_async(routers: Mapping[str, Any], prompts: Sequence[str], chain: Sequence[str], paired: _Paired,
                     rng: random.Random) -> None:  # fmt: skip
    clients: dict[str, Any] = {}
    try:
        for name, router in routers.items():
            clients[name] = _clients(AsyncWebRouter, router, chain)
        for client, prompt in _timed(clients, prompts, paired, rng):
            await client.search(prompt)
    finally:
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
    loop: asyncio.AbstractEventLoop | None,
) -> dict[str, dict[str, Any]]:
    """A stubbed ``search`` routed against the same unrouted, per router: both p50s and the paired difference's."""
    paired = _Paired(warmup)
    rng = random.Random(seed)
    with stubbed(chain):
        if loop is not None:
            loop.run_until_complete(_e2e_async(routers, prompts, chain, paired, rng))
        else:
            _e2e_sync(routers, prompts, chain, paired, rng)
    return paired.summary()
