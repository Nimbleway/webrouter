"""Fake ``auto`` members for the hop-timeout tests: one that hangs, one that answers, and a router over both."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator
from typing import Any

import pytest

from tests.conftest import FakeProvider
from web_router import WebRouter
from web_router.providers._deadline import call_budget
from web_router.registry import register_provider, unregister_provider
from web_router.types import SearchRequest, SearchResponse

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


@pytest.fixture
def hop_members() -> Iterator[None]:
    _Hangs.calls = 0
    register_provider("hangs", _Hangs)
    register_provider("healthy", _Healthy)
    yield
    unregister_provider("hangs")
    unregister_provider("healthy")


def hop_router(cls: type = WebRouter, **options: Any) -> Any:
    return cls(provider_config={"auto": {"chain": ["hangs", "healthy"]}}, **options)
