"""``timeout=`` on search and extract: the shortcut for ``Budget(time_limit_s=...)``, and the pages kept in time."""

from __future__ import annotations

import asyncio
import time
from typing import Any, ClassVar

import pytest

from tests.conftest import FakeExtractProvider, FakeProvider
from web_router import AsyncWebRouter, Budget, DeadlineExceededError, InvalidParameterError, WebRouter
from web_router.client_requests import with_timeout
from web_router.providers._deadline import current_deadline
from web_router.registry import (
    register_extract_provider,
    register_provider,
    unregister_extract_provider,
    unregister_provider,
)
from web_router.types import ExtractRequest, ExtractResponse, SearchRequest, SearchResponse

SLOW = "https://slow.example/page"
FAST = ("https://a.example/1", "https://b.example/2")


class _SlowPage(FakeExtractProvider):
    """One URL per call; the slow page hangs well past any timeout here."""

    name = "slowpage"

    def _extract(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
        if urls == [SLOW]:
            time.sleep(0.4)
            raise TimeoutError("read timed out")
        return self._respond(request, urls)

    async def _aextract(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
        if urls == [SLOW]:
            await asyncio.sleep(5)
        return self._respond(request, urls)


class _Hangs(FakeExtractProvider):
    name = "hangs"

    async def _aextract(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
        await asyncio.sleep(5)
        raise AssertionError("unreachable")


class _SeesDeadline(FakeProvider):
    name = "seesdeadline"
    seen: ClassVar[list[float | None]] = []

    def _search(self, request: SearchRequest) -> SearchResponse:
        deadline = current_deadline()
        type(self).seen.append(None if deadline is None else deadline.seconds)
        return self._respond(request)


@pytest.fixture(autouse=True)
def _providers() -> Any:
    register_extract_provider("slowpage", _SlowPage)
    register_extract_provider("hangs", _Hangs)
    register_provider("seesdeadline", _SeesDeadline)
    _SeesDeadline.seen = []
    yield
    unregister_extract_provider("slowpage")
    unregister_extract_provider("hangs")
    unregister_provider("seesdeadline")


def test_timeout_is_the_budgets_time_cap_and_the_tighter_one_holds() -> None:
    assert with_timeout(None, None) is None
    assert with_timeout(None, 2.0) == Budget(time_limit_s=2.0)
    assert with_timeout(Budget(max_cost_usd=0.1), 2.0) == Budget(max_cost_usd=0.1, time_limit_s=2.0)
    assert with_timeout(Budget(time_limit_s=1.0), 2.0) == Budget(time_limit_s=1.0)
    assert with_timeout(Budget(time_limit_s=5.0), 2.0) == Budget(time_limit_s=2.0)


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf"), "soon"])
def test_a_timeout_that_is_not_a_number_of_seconds_is_refused_by_its_own_name(value: Any) -> None:
    with pytest.raises(InvalidParameterError) as caught:
        with_timeout(None, value)
    assert caught.value.field == "timeout"


def test_search_runs_under_the_timeout_as_its_deadline() -> None:
    with WebRouter("seesdeadline") as router:
        router.search("q", timeout=7.0)
        router.search("q")
    assert _SeesDeadline.seen == [7.0, None]


def test_sync_extract_keeps_the_pages_that_came_back_in_time() -> None:
    with WebRouter("slowpage") as router:
        response = router.extract([*FAST, SLOW], timeout=0.2)
    assert [document.url for document in response.documents] == list(FAST)
    assert [failure.url for failure in response.failures] == [SLOW]


async def test_async_extract_keeps_the_pages_that_came_back_in_time() -> None:
    async with AsyncWebRouter("slowpage") as router:
        started = time.monotonic()
        response = await router.extract([*FAST, SLOW], timeout=0.2)
    assert time.monotonic() - started < 2, "the slow page is cut off at the timeout, not awaited"
    assert [document.url for document in response.documents] == list(FAST)
    assert [failure.url for failure in response.failures] == [SLOW]
    assert response.failures[0].kind == "timeout"


async def test_an_extract_where_every_call_ran_out_raises_the_deadline() -> None:
    async with AsyncWebRouter("hangs") as router:
        with pytest.raises(DeadlineExceededError) as caught:
            await router.extract(list(FAST), timeout=0.1)
    assert caught.value.seconds == 0.1
