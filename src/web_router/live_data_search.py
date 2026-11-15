"""Searching a live data catalogue by keyword, through each vendor's own search where it has one.

Vendors differ here, so the search follows what each one offers:

- **native** -- the vendor's catalogue API takes the keywords and ranks the
  matches itself. Apify's Store does (``search`` matches an Actor's title,
  name, description, owner and readme). A backend says so by also deriving
  from :class:`NativeSourceSearch`.
- **local** -- the vendor's listing takes no keywords (Nimble's Extract
  Templates, Bright Data's dataset list) or there is no listing API at all
  (Oxylabs' documented sources), so the whole catalogue is read page by page
  and matched here on each source's name, site, kind of page and description.

Either way the answer is a :class:`~web_router.LiveDataSourcePage` whose
``query`` and ``search`` say what was asked and how it was matched. Kept out
of :mod:`web_router.providers.base`, which is at its size limit; the
catalogue reads a local search makes are :mod:`web_router.live_data_catalogue`.
"""

from __future__ import annotations

import abc
import logging
from typing import Any

from web_router.constants_live_data import (
    LIVE_DATA_SEARCH_LOCAL,
    LIVE_DATA_SEARCH_NATIVE,
    MSG_LIVE_DATA_SEARCH_CAPPED,
    MSG_LIVE_DATA_SOURCES_FAILED,
)
from web_router.live_data_catalogue import aread_catalogue, check_page, read_catalogue
from web_router.providers.base import LiveDataProvider
from web_router.types_live_data import LiveDataSource, LiveDataSourcePage

logger = logging.getLogger(__name__)


class NativeSourceSearch(abc.ABC):
    """A live data backend whose catalogue API searches by keyword itself.

    Mixed into a :class:`~web_router.LiveDataProvider` subclass whose vendor
    documents a search parameter on its listing. Only the request is the
    subclass's: errors are wrapped as for a listing, and the page is tagged
    with the query here.
    """

    @abc.abstractmethod
    def _search(self, query: str, *, limit: int | None, offset: int | None) -> LiveDataSourcePage:
        """One page of the vendor's matches for ``query``, in the vendor's order."""

    @abc.abstractmethod
    async def _asearch(self, query: str, *, limit: int | None, offset: int | None) -> LiveDataSourcePage:
        """Async counterpart of :meth:`_search`."""


def search_mode(provider: LiveDataProvider) -> str:
    """``"native"`` when ``provider``'s vendor searches its own catalogue, else ``"local"``."""
    return LIVE_DATA_SEARCH_NATIVE if isinstance(provider, NativeSourceSearch) else LIVE_DATA_SEARCH_LOCAL


def search_live_data_sources(
    provider: LiveDataProvider, query: str, *, limit: int | None = None, offset: int | None = None
) -> LiveDataSourcePage:
    """One page of ``provider``'s sources that match ``query``, searched the way its vendor allows."""
    check_page(limit, offset)
    if isinstance(provider, NativeSourceSearch):
        native = provider
        page = provider._wrapped(
            lambda: native._search(query, limit=limit, offset=offset), MSG_LIVE_DATA_SOURCES_FAILED
        )
        return page.model_copy(update={"query": query, "search": LIVE_DATA_SEARCH_NATIVE})
    pages, complete = read_catalogue(provider)
    return _matched(provider.name, pages, query, limit=limit, offset=offset, complete=complete)


async def asearch_live_data_sources(
    provider: LiveDataProvider, query: str, *, limit: int | None = None, offset: int | None = None
) -> LiveDataSourcePage:
    """Async counterpart of :func:`search_live_data_sources`."""
    check_page(limit, offset)
    if isinstance(provider, NativeSourceSearch):
        page = await provider._awrapped(
            provider._asearch(query, limit=limit, offset=offset), MSG_LIVE_DATA_SOURCES_FAILED
        )
        return page.model_copy(update={"query": query, "search": LIVE_DATA_SEARCH_NATIVE})
    pages, complete = await aread_catalogue(provider)
    return _matched(provider.name, pages, query, limit=limit, offset=offset, complete=complete)


def matches(source: LiveDataSource, query: str) -> bool:
    """Whether every word of ``query`` appears, ignoring case, in what ``source`` says about itself."""
    text = _haystack(source)
    return all(word in text for word in query.casefold().split())


# -- helpers ------------------------------------------------------------------


def _haystack(source: LiveDataSource) -> str:
    title = source.extra.get("title")
    parts = (source.name, source.domain, source.entity_type, title if isinstance(title, str) else None)
    return " ".join(part for part in (*parts, source.description) if part).casefold()


def _named_by(source: LiveDataSource, words: list[str]) -> bool:
    """Every word is in the source's name or site, which ranks it above a match in its description alone."""
    text = " ".join(part for part in (source.name, source.domain) if part).casefold()
    return all(word in text for word in words)


def _matched(
    provider: str,
    pages: list[LiveDataSourcePage],
    query: str,
    *,
    limit: int | None,
    offset: int | None,
    complete: bool,
) -> LiveDataSourcePage:
    """One page of the local matches: name and site matches first, then the rest, each in catalogue order.

    ``raw`` holds the backend's untouched listing payloads the search read, in
    order, so a raw handle shows what the vendor answered, not what matched.
    """
    sources = [source for page in pages for source in page.sources]
    if not complete:
        logger.warning(MSG_LIVE_DATA_SEARCH_CAPPED.format(provider=provider, query=query, read=len(sources)))
    words = query.casefold().split()
    found = [source for source in sources if matches(source, query)]
    found.sort(key=lambda source: not _named_by(source, words))  # stable, so catalogue order holds within each
    start = offset or 0
    page = found[start:] if limit is None else found[start : start + limit]
    raw: list[Any] = [listing.raw for listing in pages if listing.raw is not None]
    return LiveDataSourcePage(
        provider=provider,
        sources=page,
        total=len(found) if complete else None,
        offset=start,
        limit=limit,
        query=query,
        search=LIVE_DATA_SEARCH_LOCAL,
        raw=raw or None,
    )


__all__: list[str] = [
    "NativeSourceSearch",
    "asearch_live_data_sources",
    "matches",
    "search_live_data_sources",
    "search_mode",
]
