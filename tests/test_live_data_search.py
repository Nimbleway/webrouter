"""Searching a live data catalogue: by the vendor's own search where it has one, else matched here.

The vendor-native side (Apify's Store ``search``) is tested with its provider in
``tests/providers/test_apify.py``; this file holds the local search every
other backend gets, run against an in-memory catalogue that pages as a real
one does.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

import pytest

from web_router import AsyncWebRouter, WebRouter
from web_router.constants_live_data import LIVE_DATA_SEARCH_PAGE
from web_router.live_data_search import NativeSourceSearch, matches, search_live_data_sources, search_mode
from web_router.providers.base import LiveDataProvider
from web_router.types_live_data import (
    LIVE_DATA_PARAMETERS,
    LiveDataRequest,
    LiveDataResponse,
    LiveDataSource,
    LiveDataSourcePage,
)


def _source(name: str, *, domain: str = "example.com", description: str = "") -> LiveDataSource:
    return LiveDataSource(provider="paged", name=name, domain=domain, description=description, raw={"name": name})


FILLER = [_source(f"filler_{index}", description="a site nobody searches for") for index in range(230)]
"""Enough sources that a search has to read three pages to see them all."""

CATALOGUE = [
    _source("shop_reviews", domain="reviews.example", description="Reviews of Amazon products, by ASIN"),
    *FILLER[:120],
    _source("amazon_product", domain="amazon.com", description="One product page"),
    *FILLER[120:],
    _source("amazon_search", domain="amazon.com", description="A search results page"),
]


class PagedProvider(LiveDataProvider):
    """A live data backend whose listing pages by limit and offset and counts its catalogue, as Nimble's does."""

    name = "paged"
    supported_parameters = LIVE_DATA_PARAMETERS
    catalogue: ClassVar[list[LiveDataSource]] = CATALOGUE

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.listed: list[tuple[int | None, int | None]] = []

    def _list(self, *, limit: int | None, offset: int | None) -> LiveDataSourcePage:
        self.listed.append((limit, offset))
        start = offset or 0
        page = self.catalogue[start:] if limit is None else self.catalogue[start : start + limit]
        return LiveDataSourcePage(provider=self.name, sources=page, total=len(self.catalogue), offset=start)

    async def _alist(self, *, limit: int | None, offset: int | None) -> LiveDataSourcePage:
        return self._list(limit=limit, offset=offset)

    def _get(self, name: str) -> LiveDataSource:
        raise NotImplementedError

    async def _aget(self, name: str) -> LiveDataSource:
        raise NotImplementedError

    def _run(self, request: LiveDataRequest) -> LiveDataResponse:
        raise NotImplementedError

    async def _arun(self, request: LiveDataRequest) -> LiveDataResponse:
        raise NotImplementedError


def test_a_local_search_reads_the_whole_catalogue_and_ranks_name_and_site_matches_first() -> None:
    provider = PagedProvider()
    page = WebRouter().live_data_sources(provider=provider, query="Amazon")

    assert provider.listed == [(LIVE_DATA_SEARCH_PAGE, 0), (LIVE_DATA_SEARCH_PAGE, 100), (LIVE_DATA_SEARCH_PAGE, 200)]
    # Both amazon.com sources by name and site, in catalogue order; the description-only match after them.
    assert page.names == ["amazon_product", "amazon_search", "shop_reviews"]
    assert page.search == "local" and page.query == "Amazon"
    assert page.total == 3 and page.offset == 0
    assert page.raw == [{"name": name} for name in page.names]


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


def test_a_catalogue_past_the_cap_is_searched_in_part_and_says_so(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr("web_router.live_data_search.LIVE_DATA_SEARCH_MAX_SOURCES", LIVE_DATA_SEARCH_PAGE)
    with caplog.at_level(logging.WARNING, logger="web_router.live_data_search"):
        page = search_live_data_sources(PagedProvider(), "amazon")

    assert page.names == ["shop_reviews"], "only the first page was read"
    assert page.total is None, "a count of a catalogue read in part would be wrong"
    assert "stopped after 100 sources" in caplog.text


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
