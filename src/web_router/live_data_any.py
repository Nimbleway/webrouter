"""Live data without naming a vendor: one catalogue search over every configured backend.

``search`` and ``extract`` take no ``provider`` and the router picks one.
Live data cannot pick for a run -- a source's name, inputs and fields belong
to one vendor -- but it can for finding one:
:meth:`~web_router.WebRouter.live_data_sources` with no ``provider`` asks
every live data backend this router can use, at once, and merges what they
answer into one page whose sources each name their own ``provider``. A run
then takes that :class:`~web_router.LiveDataSource` whole, so the caller
never names the vendor:

    >>> source = router.live_data_sources(query="amazon product").sources[0]
    >>> router.live_data(source, {"url": "https://www.amazon.com/dp/B0CHX1W1XY"})

A backend that fails is left out and named in the page's ``failures``; the
page raises only when every backend failed. Kept out of
:mod:`web_router.client_live_data` so the routers stay thin; which backends
count, and which one a run goes to, is :mod:`web_router.live_data_backends`.
"""

from __future__ import annotations

import asyncio
import contextvars
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from web_router.constants_live_data import LIVE_DATA_SEARCH_PAGE
from web_router.constants_live_data_any import LIVE_DATA_EVERY_BACKEND, MSG_LIVE_DATA_EVERY_BACKEND_FAILED
from web_router.exceptions import ConfigurationError, ProviderError, ProviderNotInstalledError
from web_router.live_data_backends import usable_backends_or_raise
from web_router.live_data_catalogue import check_page
from web_router.live_data_search import asearch_live_data_sources, search_live_data_sources
from web_router.providers.base import LiveDataProvider
from web_router.types_live_data import LiveDataSource, LiveDataSourcePage

if TYPE_CHECKING:
    from web_router.client_live_data import _LiveDataLookup

_LEFT_OUT = (ConfigurationError, ProviderNotInstalledError, ProviderError)
"""What leaves one backend out of a page from every backend, rather than failing the call."""


def search_every_backend(
    router: _LiveDataLookup, query: str | None, *, limit: int | None, offset: int | None
) -> LiveDataSourcePage:
    """One page of the sources every usable backend lists, or matches for ``query``, asked at once."""
    backends, failures, want = _prepared(router, limit, offset)
    answers: dict[str, LiveDataSourcePage | BaseException] = {}
    with ThreadPoolExecutor(max_workers=len(backends)) as pool:
        # Each worker runs in a copy of this context, so the caller's deadline reaches every backend.
        futures = {
            backend.name: pool.submit(contextvars.copy_context().run, _ask, backend, query, want)
            for backend in backends
        }
    for name, future in futures.items():
        error = future.exception()
        answers[name] = error if error is not None else future.result()
    return _merged(answers, failures, query, limit=limit, offset=offset)


async def asearch_every_backend(
    router: _LiveDataLookup, query: str | None, *, limit: int | None, offset: int | None
) -> LiveDataSourcePage:
    """Async counterpart of :func:`search_every_backend`."""
    backends, failures, want = _prepared(router, limit, offset)
    results = await asyncio.gather(*(_aask(backend, query, want) for backend in backends), return_exceptions=True)
    answers = {backend.name: result for backend, result in zip(backends, results, strict=True)}
    return _merged(answers, failures, query, limit=limit, offset=offset)


# -- helpers ------------------------------------------------------------------


def _prepared(
    router: _LiveDataLookup, limit: int | None, offset: int | None
) -> tuple[list[LiveDataProvider], dict[str, str], int | None]:
    """The page checked, the backends to ask, the ones that failed to build, and how many sources to ask each for."""
    check_page(limit, offset)
    failures: dict[str, str] = {}
    backends = usable_backends_or_raise(router, failures)
    return backends, failures, None if limit is None else (offset or 0) + limit


def _ask(backend: LiveDataProvider, query: str | None, want: int | None) -> LiveDataSourcePage:
    """One backend's first ``want`` sources, or matches for ``query``: the pages after are merged across backends.

    The listing is read in pages of at most :data:`LIVE_DATA_SEARCH_PAGE`, a
    backend's own cap on one page, so a deep offset still reaches its sources.
    """
    if query:
        return search_live_data_sources(backend, query, limit=want, offset=0)
    if want is None:
        return backend.live_data_sources(limit=None, offset=None)
    pages: list[LiveDataSourcePage] = []
    while (size := _next_page_size(pages, want)) is not None:
        pages.append(backend.live_data_sources(limit=size, offset=sum(len(page.sources) for page in pages)))
    return _joined(pages)


async def _aask(backend: LiveDataProvider, query: str | None, want: int | None) -> LiveDataSourcePage:
    """Async counterpart of :func:`_ask`."""
    if query:
        return await asearch_live_data_sources(backend, query, limit=want, offset=0)
    if want is None:
        return await backend.alive_data_sources(limit=None, offset=None)
    pages: list[LiveDataSourcePage] = []
    while (size := _next_page_size(pages, want)) is not None:
        pages.append(await backend.alive_data_sources(limit=size, offset=sum(len(page.sources) for page in pages)))
    return _joined(pages)


def _next_page_size(pages: list[LiveDataSourcePage], want: int) -> int | None:
    """How many sources to ask for next to reach ``want``, or ``None`` once read: enough, a short page, or ``total``."""
    if not pages:
        return min(want, LIVE_DATA_SEARCH_PAGE)
    read = sum(len(page.sources) for page in pages)
    last = pages[-1]
    asked = min(want - read + len(last.sources), LIVE_DATA_SEARCH_PAGE)
    if read >= want or not last.sources or (last.total is not None and read >= last.total):
        return None
    if last.total is None and len(last.sources) < asked:
        return None
    return min(want - read, LIVE_DATA_SEARCH_PAGE)


def _joined(pages: list[LiveDataSourcePage]) -> LiveDataSourcePage:
    """One backend's listing pages as one page from its start."""
    if len(pages) == 1:
        return pages[0]
    sources = [source for page in pages for source in page.sources]
    return pages[-1].model_copy(update={"sources": sources, "offset": 0, "raw": [page.raw for page in pages]})


def _merged(
    answers: dict[str, LiveDataSourcePage | BaseException],
    failures: dict[str, str],
    query: str | None,
    *,
    limit: int | None,
    offset: int | None,
) -> LiveDataSourcePage:
    """The backends' pages as one: their sources taken in turn, so each backend's best match comes first.

    A backend that raised what a backend may raise is named in ``failures``;
    anything else is a bug and propagates. When none answered, the page
    raises rather than coming back empty, as ``auto`` does on search.
    """
    pages: dict[str, LiveDataSourcePage] = {}
    for name, answer in answers.items():
        if isinstance(answer, LiveDataSourcePage):
            pages[name] = answer
        elif isinstance(answer, _LEFT_OUT):
            failures[name] = str(answer)
        else:
            raise answer
    if not pages:
        raise ProviderError(LIVE_DATA_EVERY_BACKEND, MSG_LIVE_DATA_EVERY_BACKEND_FAILED.format(failures=failures))
    start = offset or 0
    taken = list(_in_turn(page.sources for page in pages.values()))
    modes = {page.search for page in pages.values()}
    totals = [page.total for page in pages.values()]
    return LiveDataSourcePage(
        provider=LIVE_DATA_EVERY_BACKEND,
        sources=taken[start:] if limit is None else taken[start : start + limit],
        total=None if failures or None in totals else sum(total for total in totals if total is not None),
        offset=start,
        limit=limit,
        query=query,
        search=modes.pop() if query and len(modes) == 1 else None,
        raw={name: page.raw for name, page in pages.items()},
        failures=failures,
    )


def _in_turn(lists: Iterable[list[LiveDataSource]]) -> Iterable[LiveDataSource]:
    """The first of each list, then the second of each, and so on: no backend crowds out the others."""
    rows = [list(sources) for sources in lists]
    for index in range(max((len(sources) for sources in rows), default=0)):
        yield from (sources[index] for sources in rows if index < len(sources))


__all__: list[str] = ["asearch_every_backend", "search_every_backend"]
