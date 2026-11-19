"""Fake ``auto`` members for the hop-timeout tests -- one hangs, one answers, one answers slowly, one fails, one times
out on its own -- and a router over a chain of them."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator
from typing import Any

import pytest

from tests.conftest import FakeProvider
from web_router import ProviderError, WebRouter
from web_router.providers._deadline import call_budget
from web_router.registry import register_provider, unregister_provider
from web_router.types import SearchRequest, SearchResponse
from web_router.types_errors import ERROR_KIND_TIMEOUT, ERROR_KIND_UPSTREAM

HANG = 30.0
"""How long the hanging member's request would wait on its own: far past any cap under test."""


class _Hangs(FakeProvider):
    """A member that stalls until its request's timeout, clamped to the time left, runs out."""

    name = "hangs"
    calls = 0

    def _search(self, request: SearchRequest) -> SearchResponse:
        type(self).calls += 1
        time.sleep(call_budget(HANG))
        raise TimeoutError("read timed out")

    async def _asearch(self, request: SearchRequest) -> SearchResponse:
        type(self).calls += 1
        await asyncio.sleep(HANG)
        raise AssertionError("cut off before this")


class _Healthy(FakeProvider):
    name = "healthy"


SLOW = 0.35
"""How long the slow member takes to answer: more than a quarter of a 1 s call, less than half."""


class _Slow(FakeProvider):
    name = "slow"

    def _search(self, request: SearchRequest) -> SearchResponse:
        budget = call_budget(SLOW)  # as a transport would: the request waits no longer than the time left
        time.sleep(budget)
        if budget < SLOW:
            raise TimeoutError("read timed out")
        return self._respond(request)

    async def _asearch(self, request: SearchRequest) -> SearchResponse:
        await asyncio.sleep(SLOW)
        return self._respond(request)


class _Fails(FakeProvider):
    name = "fails"

    def _search(self, request: SearchRequest) -> SearchResponse:
        raise ProviderError(self.name, "HTTP 500", kind=ERROR_KIND_UPSTREAM)

    async def _asearch(self, request: SearchRequest) -> SearchResponse:
        return self._search(request)


class _TimesOut(FakeProvider):
    """A member whose own transport timeout fires at once: the backend's own slowness, which counts."""

    name = "timesout"
    calls = 0

    def _search(self, request: SearchRequest) -> SearchResponse:
        type(self).calls += 1
        raise ProviderError(self.name, "read timed out", kind=ERROR_KIND_TIMEOUT)

    async def _asearch(self, request: SearchRequest) -> SearchResponse:
        return self._search(request)


_MEMBERS = {"hangs": _Hangs, "healthy": _Healthy, "slow": _Slow, "fails": _Fails, "timesout": _TimesOut}


@pytest.fixture
def hop_members() -> Iterator[None]:
    _Hangs.calls = _TimesOut.calls = 0
    for name, member in _MEMBERS.items():
        register_provider(name, member)
    yield
    for name in _MEMBERS:
        unregister_provider(name)


def hop_router(cls: type = WebRouter, chain: tuple[str, ...] = ("hangs", "healthy"), **options: Any) -> Any:
    return cls(provider_config={"auto": {"chain": list(chain)}}, **options)
