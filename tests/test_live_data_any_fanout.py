"""How a search over every backend asks each one: in pages each can serve, and on the caller's time limit.

The backends are in memory (``tests/_live_data_paged.py``), registered as
custom backends; every built-in backend's variable is cleared, so no test
reaches a real vendor.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import ClassVar

import pytest

from tests._live_data_paged import PagedProvider
from tests.live_data_env_helpers import clear_live_data_credentials
from web_router import AsyncWebRouter, WebRouter
from web_router.exceptions import DeadlineExceededError
from web_router.providers._deadline import call_deadline, current_deadline
from web_router.registry import register_live_data_provider, unregister_live_data_provider
from web_router.types_live_data import LiveDataSource, LiveDataSourcePage


@pytest.fixture(autouse=True)
def no_vendor_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_live_data_credentials(monkeypatch)


class CappedProvider(PagedProvider):
    """500 sources, at most 100 to a page whatever is asked, as Nimble's listing does."""

    name = "capped"
    catalogue: ClassVar[list[LiveDataSource]] = [
        LiveDataSource(provider="capped", name=f"c{index}", raw={}) for index in range(500)
    ]

    def _list(self, *, limit: int | None, offset: int | None) -> LiveDataSourcePage:
        return super()._list(limit=min(limit if limit is not None else 100, 100), offset=offset)


@pytest.fixture
def capped() -> Iterator[None]:
    register_live_data_provider("capped", CappedProvider)
    yield
    unregister_live_data_provider("capped")


# -- each backend read within its page cap ------------------------------------


def test_a_deep_offset_reads_a_capped_backend_page_by_page(capped: None) -> None:
    router = WebRouter()

    named = router.live_data_sources(provider="capped", limit=10, offset=150)
    merged = router.live_data_sources(limit=10, offset=150)

    assert named.names == [f"c{index}" for index in range(150, 160)]
    assert merged.names == named.names
    assert merged.total == 500


def test_the_async_router_reads_a_capped_backend_page_by_page(capped: None) -> None:
    async def scenario() -> list[str]:
        router = AsyncWebRouter()
        page = await router.live_data_sources(limit=10, offset=150)
        await router.aclose()
        return page.names

    assert asyncio.run(scenario()) == [f"c{index}" for index in range(150, 160)]


# -- the caller's time limit reaches every backend -----------------------------


class RecordingProvider(PagedProvider):
    """Records whether a deadline was in force each time it was asked for a page."""

    saw_deadline: ClassVar[list[bool]] = []

    def _list(self, *, limit: int | None, offset: int | None) -> LiveDataSourcePage:
        RecordingProvider.saw_deadline.append(current_deadline() is not None)
        return super()._list(limit=limit, offset=offset)


class RecorderA(RecordingProvider):
    name = "rec_a"


class RecorderB(RecordingProvider):
    name = "rec_b"


@pytest.fixture
def recorders() -> Iterator[list[bool]]:
    seen: list[bool] = []
    RecordingProvider.saw_deadline = seen
    register_live_data_provider("rec_a", RecorderA)
    register_live_data_provider("rec_b", RecorderB)
    yield seen
    unregister_live_data_provider("rec_a")
    unregister_live_data_provider("rec_b")


def test_the_callers_deadline_reaches_every_backend_on_the_sync_path(recorders: list[bool]) -> None:
    router = WebRouter()

    with call_deadline(5):
        router.live_data_sources(provider="rec_a", limit=2)
    assert recorders == [True]

    recorders.clear()
    with call_deadline(5):
        router.live_data_sources(limit=2)
    assert recorders == [True, True]


# -- a backend that runs out of time is left out, like any failed backend -------


class SlowProvider(PagedProvider):
    """Runs past the caller's time limit on every listing."""

    name = "slow"

    def _list(self, *, limit: int | None, offset: int | None) -> LiveDataSourcePage:
        raise DeadlineExceededError("slow", 5.0)


class BrokenProvider(PagedProvider):
    """Raises a plain exception past the library's wrapper: a bug, not a failed backend."""

    name = "broken"

    def live_data_sources(self, *, limit: int | None = None, offset: int | None = None) -> LiveDataSourcePage:
        raise RuntimeError("bug")

    async def alive_data_sources(self, *, limit: int | None = None, offset: int | None = None) -> LiveDataSourcePage:
        raise RuntimeError("bug")


@pytest.fixture
def slow(capped: None) -> Iterator[None]:
    register_live_data_provider("slow", SlowProvider)
    yield
    unregister_live_data_provider("slow")


@pytest.fixture
def broken(capped: None) -> Iterator[None]:
    register_live_data_provider("broken", BrokenProvider)
    yield
    unregister_live_data_provider("broken")


def test_a_backend_past_the_time_limit_is_left_out_and_named(slow: None) -> None:
    page = WebRouter().live_data_sources(limit=2)

    assert page.names == ["c0", "c1"]
    assert list(page.failures) == ["slow"]


def test_the_async_router_leaves_out_a_backend_past_the_time_limit(slow: None) -> None:
    async def scenario() -> LiveDataSourcePage:
        router = AsyncWebRouter()
        page = await router.live_data_sources(limit=2)
        await router.aclose()
        return page

    page = asyncio.run(scenario())
    assert page.names == ["c0", "c1"]
    assert list(page.failures) == ["slow"]


def test_a_plain_exception_from_a_backend_still_propagates(broken: None) -> None:
    with pytest.raises(RuntimeError, match="bug"):
        WebRouter().live_data_sources(limit=2)

    async def scenario() -> None:
        router = AsyncWebRouter()
        try:
            await router.live_data_sources(limit=2)
        finally:
            await router.aclose()

    with pytest.raises(RuntimeError, match="bug"):
        asyncio.run(scenario())
