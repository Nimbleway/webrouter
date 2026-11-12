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
from web_router import AsyncWebRouter, WebRouter
from web_router.constants_live_data import LIVE_DATA_ENV_BY_PROVIDER
from web_router.registry import register_live_data_provider, unregister_live_data_provider
from web_router.types_live_data import LiveDataSource, LiveDataSourcePage


@pytest.fixture(autouse=True)
def no_vendor_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for env_vars in LIVE_DATA_ENV_BY_PROVIDER.values():
        for env_var in env_vars:
            monkeypatch.delenv(env_var, raising=False)


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
