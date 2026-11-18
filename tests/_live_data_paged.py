"""An in-memory live data catalogue that pages as a real one does, for the search tests."""

from __future__ import annotations

from typing import Any, ClassVar

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
        return LiveDataSourcePage(
            provider=self.name,
            sources=page,
            total=len(self.catalogue),
            offset=start,
            raw={"offset": start, "names": [source.name for source in page]},
        )

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


class SmallPages(PagedProvider):
    """A backend that caps its pages below what was asked, and counts its catalogue."""

    page_cap: ClassVar[int] = 20

    def _list(self, *, limit: int | None, offset: int | None) -> LiveDataSourcePage:
        return super()._list(limit=min(limit or self.page_cap, self.page_cap), offset=offset)
