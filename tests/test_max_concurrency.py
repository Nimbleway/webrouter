"""``max_concurrency=``: the most vendor calls one router has in flight, across every call it serves."""

from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest

from tests.conftest import FakeExtractProvider
from web_router import AsyncWebRouter, DeadlineExceededError, InvalidParameterError, WebRouter
from web_router.registry import register_extract_provider, unregister_extract_provider
from web_router.types import ExtractRequest, ExtractResponse


class _Counted(FakeExtractProvider):
    """One URL per call, each taking a moment, counting how many run at once."""

    name = "counted"
    lock = threading.Lock()
    running = 0
    peak = 0
    pause = 0.05

    @classmethod
    def _enter(cls) -> None:
        with cls.lock:
            cls.running += 1
            cls.peak = max(cls.peak, cls.running)

    @classmethod
    def _leave(cls) -> None:
        with cls.lock:
            cls.running -= 1

    def _extract(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
        self._enter()
        try:
            time.sleep(self.pause)
            return self._respond(request, urls)
        finally:
            self._leave()

    async def _aextract(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
        self._enter()
        try:
            await asyncio.sleep(self.pause)
            return self._respond(request, urls)
        finally:
            self._leave()


@pytest.fixture(autouse=True)
def _counted() -> Any:
    _Counted.running, _Counted.peak, _Counted.pause = 0, 0, 0.05
    register_extract_provider("counted", _Counted)
    yield
    unregister_extract_provider("counted")


URLS = [f"https://example.com/{n}" for n in range(6)]


async def test_one_extract_fanned_out_takes_no_more_slots_than_the_cap() -> None:
    async with AsyncWebRouter("counted", max_concurrency=2) as router:
        response = await router.extract(URLS)
    assert len(response.documents) == len(URLS)
    assert _Counted.peak == 2


async def test_concurrent_calls_share_the_routers_cap() -> None:
    async with AsyncWebRouter("counted", max_concurrency=3) as router:
        await asyncio.gather(*(router.extract(url) for url in URLS))
    assert _Counted.peak == 3


async def test_with_no_cap_the_per_call_fan_out_is_all_that_limits() -> None:
    async with AsyncWebRouter("counted") as router:
        await asyncio.gather(*(router.extract(url) for url in URLS))
    assert _Counted.peak == len(URLS)


def test_the_sync_routers_cap_holds_across_threads() -> None:
    with WebRouter("counted", max_concurrency=2) as router, ThreadPoolExecutor(6) as pool:
        list(pool.map(router.extract, URLS))
    assert _Counted.peak == 2


async def test_urls_still_waiting_for_a_slot_at_the_timeout_are_failures_beside_the_pages_read() -> None:
    _Counted.pause = 0.3
    async with AsyncWebRouter("counted", max_concurrency=1) as router:
        response = await router.extract(URLS[:3], timeout=0.45)
    assert [document.url for document in response.documents] == URLS[:1]
    assert [failure.url for failure in response.failures] == URLS[1:3]
    assert {failure.kind for failure in response.failures} == {"timeout"}


async def test_waiting_for_a_slot_counts_against_the_timeout() -> None:
    _Counted.pause = 0.5
    async with AsyncWebRouter("counted", max_concurrency=1) as router:
        busy = asyncio.create_task(router.extract(URLS[0]))
        await asyncio.sleep(0.01)
        with pytest.raises(DeadlineExceededError):
            await router.extract(URLS[1], timeout=0.1)
        await busy


@pytest.mark.parametrize("value", [0, -1, 1.5, True, "2"])
def test_max_concurrency_takes_a_whole_number_of_one_or_more(value: Any) -> None:
    with pytest.raises(InvalidParameterError) as caught:
        WebRouter(max_concurrency=value)
    assert caught.value.field == "max_concurrency"
