"""How a search over every backend orders and pages what they answer: one scale, and pages that line up.

The backends are in memory: the ``fake`` and ``paged`` of
``tests/_live_data_any_fixtures.py``, and the vendors below, registered as
custom backends; no test reaches a real vendor.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import ClassVar

import pytest

from tests._live_data_any_fixtures import no_vendor_keys_fixture, two_backends_fixture  # noqa: F401  (fixtures)
from tests._live_data_paged import PagedProvider
from web_router import WebRouter
from web_router.live_data_search import NativeSourceSearch
from web_router.registry import register_live_data_provider, unregister_live_data_provider
from web_router.types_live_data import LiveDataSource, LiveDataSourcePage


def _source(provider: str, name: str, domain: str) -> LiveDataSource:
    return LiveDataSource(provider=provider, name=name, domain=domain, description="Pages from Amazon")


class NativeProvider(PagedProvider, NativeSourceSearch):
    """A vendor that searches its own catalogue and answers in its own order, not ours: two weak matches first."""

    name = "native"
    catalogue: ClassVar[list[LiveDataSource]] = [
        _source("native", "weak_one", "example.com"),
        _source("native", "weak_two", "example.com"),
        _source("native", "amazon_best", "amazon.com"),
    ]

    def _search(self, query: str, *, limit: int | None, offset: int | None) -> LiveDataSourcePage:
        return self._list(limit=limit, offset=offset)

    async def _asearch(self, query: str, *, limit: int | None, offset: int | None) -> LiveDataSourcePage:
        return self._list(limit=limit, offset=offset)


class RankedNative(NativeProvider):
    """The same vendor, answering already in score order."""

    name = "ranked_native"
    catalogue: ClassVar[list[LiveDataSource]] = [NativeProvider.catalogue[2], *NativeProvider.catalogue[:2]]


class LocalProvider(PagedProvider):
    """Three like sources on a backend searched here, which ranks them itself."""

    name = "local"
    catalogue: ClassVar[list[LiveDataSource]] = [
        _source("local", "local_weak_one", "example.com"),
        _source("local", "local_weak_two", "example.com"),
        _source("local", "local_best", "amazon.com"),
    ]


@pytest.fixture
def only(request: pytest.FixtureRequest) -> Iterator[None]:
    """Just the backends the test names, registered for it."""
    providers: list[type[PagedProvider]] = request.param
    for provider in providers:
        register_live_data_provider(provider.name, provider)
    yield
    for provider in providers:
        unregister_live_data_provider(provider.name)


def _paged(query: str, count: int) -> list[str]:
    router = WebRouter()
    return [
        name
        for offset in range(count + 1)
        for name in router.live_data_sources(query=query, limit=1, offset=offset).names
    ]


@pytest.mark.parametrize(
    "only",
    [[NativeProvider], [RankedNative], [LocalProvider], [NativeProvider, LocalProvider]],
    indirect=True,
    ids=["native", "native-in-score-order", "local", "native-and-local"],
)
def test_pages_of_one_line_up_with_the_unpaged_answer(only: None) -> None:
    """A vendor's own order is not re-sorted page by page: no source repeats, none goes missing."""
    whole = WebRouter().live_data_sources(query="amazon").names

    assert _paged("amazon", len(whole)) == whole
    assert len(set(whole)) == len(whole)


@pytest.mark.parametrize("only", [[NativeProvider, LocalProvider]], indirect=True)
def test_a_strong_match_on_one_backend_still_leads_a_weak_one_on_another(only: None) -> None:
    names = WebRouter().live_data_sources(query="amazon").names

    # local ranks its amazon.com source first, and it leads native's weak first matches.
    assert names[:2] == ["local_best", "weak_one"]


def test_a_query_ranks_every_backend_on_one_scale(two_backends: None) -> None:
    """Taking turns would put paged's weak match second; scored alike, fake's two strong matches lead."""
    page = WebRouter().live_data_sources(query="example", limit=3)

    # Both of fake's are on example.com itself, with a schema and a sample; shop_reviews only mentions it.
    assert page.names[:2] == ["books_pdp", "jobs_listing"]
    assert "shop_reviews" not in page.names
