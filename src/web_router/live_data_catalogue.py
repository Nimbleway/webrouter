"""Reading a whole live data catalogue for a local search, and keeping the read for a while.

A vendor whose listing takes no keywords is searched by reading every page
and matching here (:mod:`web_router.live_data_search`). This module does the
reading: page by page on the sync path, several pages at once on the async
one, each read kept for :data:`LIVE_DATA_SEARCH_CACHE_SECONDS` so a second
search soon after reads nothing. It also holds the one check of a page's
``limit`` and ``offset`` that every live data listing makes.
"""

from __future__ import annotations

import asyncio
import time
import weakref

from web_router.constants_live_data import (
    LIVE_DATA_SEARCH_CACHE_SECONDS,
    LIVE_DATA_SEARCH_CONCURRENCY,
    LIVE_DATA_SEARCH_MAX_SOURCES,
    LIVE_DATA_SEARCH_PAGE,
    MSG_LIVE_DATA_BAD_PAGE,
)
from web_router.providers.base import LiveDataProvider
from web_router.types_live_data import LiveDataSourcePage

Catalogue = tuple[list[LiveDataSourcePage], bool]
"""A catalogue read for a local search: the listing pages, in order, and whether they reach its end."""

_read: weakref.WeakKeyDictionary[LiveDataProvider, tuple[float, Catalogue]] = weakref.WeakKeyDictionary()
"""Each provider's last catalogue read and when it was made, so a second search soon after reads nothing.

A catalogue changes slowly and is read whole to be searched -- dozens of
pages on a large one -- so it is kept for :data:`LIVE_DATA_SEARCH_CACHE_SECONDS`.
Only the listing is kept: a run still fetches its site on every call. Keyed
weakly, so a closed and dropped provider takes its read with it.
"""


def check_page(limit: int | None, offset: int | None) -> None:
    """Refuse a negative ``limit`` or ``offset``: Python's slicing would read either as counting from the end."""
    if (limit is not None and limit < 0) or (offset is not None and offset < 0):
        raise ValueError(MSG_LIVE_DATA_BAD_PAGE.format(limit=limit, offset=offset))


def read_catalogue(provider: LiveDataProvider) -> Catalogue:
    """``provider``'s whole catalogue, from the last read if it is recent, else read page by page and kept."""
    if (kept := _cached(provider)) is not None:
        return kept
    pages: list[LiveDataSourcePage] = []
    read, complete = 0, False
    while read < LIVE_DATA_SEARCH_MAX_SOURCES:
        pages.append(page := provider.live_data_sources(limit=LIVE_DATA_SEARCH_PAGE, offset=read))
        read += len(page.sources)
        if complete := _last_page(page, read=read):
            break
    return _keep(provider, (pages, complete))


async def aread_catalogue(provider: LiveDataProvider) -> Catalogue:
    """Async counterpart of :func:`read_catalogue`, asking for several pages at once."""
    if (kept := _cached(provider)) is not None:
        return kept
    return _keep(provider, await _aread(provider))


async def _aread(provider: LiveDataProvider) -> Catalogue:
    """Read a catalogue; once the first page gives its count, the rest are asked for at once.

    The first page's length is the backend's own page size, so the later
    offsets step by it, and a backend that caps its pages below what was asked
    is still read through. At most :data:`LIVE_DATA_SEARCH_CONCURRENCY` pages are
    in flight. Without a count, pages are read one after another until a short one.
    """
    first = await provider.alive_data_sources(limit=LIVE_DATA_SEARCH_PAGE, offset=0)
    pages, read = [first], len(first.sources)
    if _last_page(first, read=read):
        return pages, True
    if first.total is not None:
        gate = asyncio.Semaphore(LIVE_DATA_SEARCH_CONCURRENCY)
        end = min(first.total, LIVE_DATA_SEARCH_MAX_SOURCES)

        async def one(offset: int) -> LiveDataSourcePage:
            async with gate:
                return await provider.alive_data_sources(limit=LIVE_DATA_SEARCH_PAGE, offset=offset)

        pages += await asyncio.gather(*(one(offset) for offset in range(read, end, read)))
        read = sum(len(page.sources) for page in pages)
        return pages, read >= first.total
    while read < LIVE_DATA_SEARCH_MAX_SOURCES:
        pages.append(page := await provider.alive_data_sources(limit=LIVE_DATA_SEARCH_PAGE, offset=read))
        read += len(page.sources)
        if _last_page(page, read=read):
            return pages, True
    return pages, False


def _cached(provider: LiveDataProvider) -> Catalogue | None:
    kept = _read.get(provider)
    if kept is None or time.monotonic() - kept[0] > LIVE_DATA_SEARCH_CACHE_SECONDS:
        return None
    return kept[1]


def _keep(provider: LiveDataProvider, catalogue: Catalogue) -> Catalogue:
    _read[provider] = (time.monotonic(), catalogue)
    return catalogue


def _last_page(page: LiveDataSourcePage, *, read: int) -> bool:
    """Whether the catalogue has been read through.

    An empty page always ends it. Where the backend counts its catalogue, the
    count decides, so a backend that caps its pages below what was asked is
    still read to the end. Without a count, a page shorter than the backend's
    own page size -- or the size asked for, when it reports none -- is the last.
    """
    if not page.sources:
        return True
    if page.total is not None:
        return read >= page.total
    return len(page.sources) < (page.limit or LIVE_DATA_SEARCH_PAGE)
