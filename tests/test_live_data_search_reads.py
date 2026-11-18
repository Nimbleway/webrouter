"""How a local live data search reads the catalogue: every page up to the count, a cap, and a short-lived cache."""

from __future__ import annotations

import logging

import pytest

from tests._live_data_paged import CATALOGUE, PagedProvider, SmallPages
from web_router import AsyncWebRouter
from web_router.constants_live_data import LIVE_DATA_SEARCH_PAGE
from web_router.live_data_search import search_live_data_sources
from web_router.types_live_data import (
    LiveDataSourcePage,
)


def test_a_catalogue_past_the_cap_is_searched_in_part_and_says_so(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr("web_router.live_data_catalogue.LIVE_DATA_SEARCH_MAX_SOURCES", LIVE_DATA_SEARCH_PAGE)
    with caplog.at_level(logging.WARNING, logger="web_router.live_data_search"):
        page = search_live_data_sources(PagedProvider(), "amazon")

    assert page.names == ["shop_reviews"], "only the first page was read"
    assert page.total is None, "a count of a catalogue read in part would be wrong"
    assert "stopped after 100 sources" in caplog.text


def test_a_backend_that_pages_smaller_than_asked_is_still_read_to_its_count() -> None:
    provider = SmallPages()
    page = search_live_data_sources(provider, "amazon")

    assert len(provider.listed) == -(-len(CATALOGUE) // SmallPages.page_cap), "every short page, up to the count"
    assert page.names == ["amazon_product", "amazon_search", "shop_reviews"]
    assert page.total == 3


async def test_a_backend_that_pages_smaller_than_asked_is_read_through_async_too() -> None:
    page = await AsyncWebRouter().live_data_sources(provider=SmallPages(), query="amazon")

    assert page.names == ["amazon_product", "amazon_search", "shop_reviews"]


def test_without_a_count_a_short_page_against_the_backends_own_size_ends_the_read() -> None:
    class Uncounted(SmallPages):
        def _list(self, *, limit: int | None, offset: int | None) -> LiveDataSourcePage:
            page = super()._list(limit=limit, offset=offset)
            return page.model_copy(update={"total": None, "limit": self.page_cap})

    provider = Uncounted()
    page = search_live_data_sources(provider, "amazon")

    assert page.names == ["amazon_product", "amazon_search", "shop_reviews"], "full pages of 20 did not stop it"
    assert provider.listed[-1][1] == 220, "the short last page did"


def test_a_second_search_soon_after_reuses_the_catalogue_read() -> None:
    provider = PagedProvider()
    search_live_data_sources(provider, "amazon")
    read = len(provider.listed)
    page = search_live_data_sources(provider, "amazon search")

    assert len(provider.listed) == read, "nothing listed again"
    assert page.names == ["amazon_search"]


def test_a_catalogue_read_older_than_the_cache_is_read_again(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = PagedProvider()
    search_live_data_sources(provider, "amazon")
    read = len(provider.listed)
    monkeypatch.setattr("web_router.live_data_catalogue.LIVE_DATA_SEARCH_CACHE_SECONDS", -1.0)
    search_live_data_sources(provider, "amazon")

    assert len(provider.listed) == 2 * read


async def test_an_async_search_asks_for_every_page_once_the_count_is_known() -> None:
    provider = SmallPages()
    page = await AsyncWebRouter().live_data_sources(provider=provider, query="amazon")

    offsets = sorted(offset or 0 for _, offset in provider.listed)
    assert offsets == list(range(0, len(CATALOGUE), SmallPages.page_cap)), "each page once, stepping by the page size"
    assert page.total == 3
