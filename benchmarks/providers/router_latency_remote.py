"""router-latency's remote router: ``remote:<url>``, as is or behind a simulated link.

``remote:<url>`` is the library's own ``remote`` router, posting each request
to a router service in the wire format, schema 1. ``--remote-rtt-ms`` adds the
same service behind a simulated round trip. Imported as a sibling, by name, as
``_common`` is.

Experimental until 1.0, like the router contract it measures.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

from router_latency_routers import REMOTE, RouterSpec

from web_router._urls import host_of
from web_router.constants_remote import ENV_REMOTE_ROUTER_API_KEY
from web_router.routing.contract import RouteDecision, RouteRequest
from web_router.routing.registry import aclose_router, discard_router
from web_router.routing.remote import RemoteRouter

HANDSHAKE_RTTS = 3
"""Round trips a new connection's first call pays on a simulated link: TCP, TLS 1.3, then the request."""

MSG_LINK_TIMEOUT = "the simulated round trip took the whole deadline"


class DelayedRouter:
    """A router behind a simulated link: ``rtt_ms`` slept before each call, :data:`HANDSHAKE_RTTS` times on the first.

    It stands in for a router service on another host when none is at hand; the
    results file says the delay was simulated.
    """

    def __init__(self, inner: Any, rtt_ms: float) -> None:
        self.inner = inner
        self.rtt_s = rtt_ms / 1000
        self.timeout_s = inner.timeout_s
        self._first = True

    async def aroute(self, req: RouteRequest) -> RouteDecision:
        slept = self.rtt_s * (HANDSHAKE_RTTS if self._first else 1)
        self._first = False
        if slept >= req.deadline_s:
            await asyncio.sleep(req.deadline_s)
            raise TimeoutError(MSG_LINK_TIMEOUT)
        await asyncio.sleep(slept)
        return await self.inner.aroute(replace(req, deadline_s=req.deadline_s - slept))  # type: ignore[no-any-return]

    def close(self) -> None:
        discard_router(self.inner)

    async def aclose(self) -> None:
        await aclose_router(self.inner)


def remote_spec(spec: str, rtt_ms: float) -> RouterSpec:
    """``remote:<url>`` as the library's ``remote`` router: on loopback when ``rtt_ms`` is 0, else behind the link."""
    url = spec.removeprefix(REMOTE)
    shown = REMOTE + host_of(url)  # never the path, query or credentials: they may carry a token
    if not rtt_ms:
        name, build = shown, lambda: RemoteRouter(url=url)
        link = "as the URL lies (loopback: no network)"
    else:
        name, build = f"{shown} +{rtt_ms:g}ms", lambda: DelayedRouter(RemoteRouter(url=url), rtt_ms)
        link = f"plus a simulated {rtt_ms:g} ms RTT ({HANDSHAKE_RTTS} RTTs on a new connection)"
    return RouterSpec(
        name,
        f"web_router.routing.remote.RemoteRouter at {host_of(url)}, link {link}",
        build,
        optional_env=(ENV_REMOTE_ROUTER_API_KEY,),
        cost_note="free to the caller: the service's own cost is not counted",
    )
