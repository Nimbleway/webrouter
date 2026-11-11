"""Searching a live data catalogue: by the vendor's own search where it has one, else matched here.

The vendor-native side (Apify's Store ``search``) is tested with its provider in
``tests/providers/test_apify.py``; this file holds the local search every
other backend gets, run against the in-memory catalogue in
``tests/_live_data_paged.py``. How that search reads the catalogue is in
``test_live_data_search_reads.py``.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests._live_data_paged import CATALOGUE, PagedProvider
from web_router import AsyncWebRouter, WebRouter
from web_router.constants_live_data import LIVE_DATA_SEARCH_PAGE
from web_router.live_data_search import NativeSourceSearch, matches, search_live_data_sources, search_mode
from web_router.types_live_data import (
    LiveDataSourcePage,
)


def test_a_local_search_reads_the_whole_catalogue_and_ranks_name_and_site_matches_first() -> None:
    provider = PagedProvider()
    page = WebRouter().live_data_sources(provider=provider, query="Amazon")

    assert provider.listed == [(LIVE_DATA_SEARCH_PAGE, 0), (LIVE_DATA_SEARCH_PAGE, 100), (LIVE_DATA_SEARCH_PAGE, 200)]
    # Both amazon.com sources by name and site, in catalogue order; the description-only match after them.
    assert page.names == ["amazon_product", "amazon_search", "shop_reviews"]
    assert page.search == "local" and page.query == "Amazon"
    assert page.total == 3 and page.offset == 0
    # The vendor's listing pages the search read, untouched, not the matched sources.
    assert [listing["offset"] for listing in page.raw] == [0, 100, 200]


def test_every_word_of_the_query_must_match() -> None:
    page = WebRouter().live_data_sources(provider=PagedProvider(), query="amazon search")

    assert page.names == ["amazon_search"]


def test_local_matches_page_with_limit_and_offset() -> None:
    page = WebRouter().live_data_sources(provider=PagedProvider(), query="amazon", limit=1, offset=1)

    assert page.names == ["amazon_search"]
    assert page.total == 3 and page.offset == 1 and page.limit == 1


def test_a_blank_query_is_a_plain_listing() -> None:
    provider = PagedProvider()
    page = WebRouter().live_data_sources(provider=provider, query="  ", limit=5)

    assert provider.listed == [(5, None)]
    assert page.search is None and page.query is None


def test_a_negative_page_is_refused_before_anything_is_read() -> None:
    provider = PagedProvider()
    with pytest.raises(ValueError, match="limit=-1"):
        search_live_data_sources(provider, "amazon", limit=-1)
    assert provider.listed == []


async def test_the_async_router_searches_the_same_way() -> None:
    page = await AsyncWebRouter().live_data_sources(provider=PagedProvider(), query="amazon")

    assert page.names == ["amazon_product", "amazon_search", "shop_reviews"]
    assert page.search == "local"


def test_the_fake_backend_is_searched_locally(fake_live_data_provider: Any) -> None:
    page = WebRouter().live_data_sources(provider="fake", query="jobs")

    assert page.names == ["jobs_listing"]
    assert search_mode(WebRouter().get_live_data_provider("fake")) == "local"


def test_search_mode_names_a_native_searcher() -> None:
    class Native(PagedProvider, NativeSourceSearch):
        def _search(self, query: str, *, limit: int | None, offset: int | None) -> LiveDataSourcePage:
            return LiveDataSourcePage(provider=self.name)

        async def _asearch(self, query: str, *, limit: int | None, offset: int | None) -> LiveDataSourcePage:
            return LiveDataSourcePage(provider=self.name)

    page = WebRouter().live_data_sources(provider=Native(), query="anything")

    assert search_mode(Native()) == "native"
    assert page.search == "native" and page.query == "anything"


@pytest.mark.parametrize(
    ("query", "expected"),
    [("AMAZON", True), ("amazon.com", True), ("product page", True), ("amazon reviews", False)],
)
def test_matches_reads_name_site_and_description_ignoring_case(query: str, expected: bool) -> None:
    assert matches(CATALOGUE[121], query) is expected
