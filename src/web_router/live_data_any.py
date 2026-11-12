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
:mod:`web_router.client_live_data` so the routers stay thin.
"""

from __future__ import annotations

import asyncio
import contextvars
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from web_router.constants_live_data import (
    LIVE_DATA_ENV_BY_PROVIDER,
    LIVE_DATA_EVERY_BACKEND,
    LIVE_DATA_SEARCH_PAGE,
    MSG_LIVE_DATA_EVERY_BACKEND_FAILED,
    MSG_LIVE_DATA_NO_BACKEND,
    MSG_LIVE_DATA_OTHER_BACKEND,
    MSG_LIVE_DATA_WHICH_BACKEND,
)
from web_router.exceptions import ConfigurationError, ProviderError, ProviderNotInstalledError
from web_router.live_data_catalogue import check_page
from web_router.live_data_search import asearch_live_data_sources, search_live_data_sources
from web_router.providers.base import LiveDataProvider
from web_router.registry import (
    available_live_data_providers,
    configured_live_data_providers,
    get_live_data_provider_class,
)
from web_router.types import Provider, provider_name
from web_router.types_live_data import LiveDataSource, LiveDataSourcePage

if TYPE_CHECKING:
    from web_router.client_live_data import _LiveDataLookup

_LEFT_OUT = (ConfigurationError, ProviderNotInstalledError, ProviderError)
"""What leaves one backend out of a page from every backend, rather than failing the call."""

_KEY_SETTINGS = ("api_key", "client")
"""Settings that configure a backend in code, beside a ``key_argument`` of its own where it has one."""


def usable_backends(router: _LiveDataLookup, failures: dict[str, str] | None = None) -> list[LiveDataProvider]:
    """Every live data backend ``router`` can use, in name order: a key in the environment or in code.

    A custom backend registered with :func:`~web_router.register_live_data_provider`
    is tried too, since registering it is the caller's say-so. One that cannot
    be built -- no key after all, its extra not installed -- is left out, and
    named in ``failures`` when the caller passes a dict for them.
    """
    builtin = set(LIVE_DATA_ENV_BY_PROVIDER)
    in_env = set(configured_live_data_providers())
    backends: list[LiveDataProvider] = []
    for name in available_live_data_providers():
        if name in builtin and name not in in_env and not _keyed_in_code(router, name):
            continue
        try:
            backends.append(router.get_live_data_provider(name))
        except (ConfigurationError, ProviderNotInstalledError) as error:
            if failures is not None:
                failures[name] = str(error)
    return backends


def backend_for(
    router: _LiveDataLookup,
    source: str | LiveDataSource,
    provider: str | Provider | LiveDataProvider | None,
) -> tuple[LiveDataProvider, str]:
    """The backend that runs or describes ``source``, and the name it knows the source by.

    A :class:`~web_router.LiveDataSource` names its own backend. A bare name
    needs ``provider``, unless this router can use exactly one live data
    backend, which is then the only place the name can be.

    Raises:
        ConfigurationError: ``provider`` contradicts the source's own, or a
            bare name could be on several backends, or on none.
    """
    if isinstance(source, LiveDataSource):
        if provider is not None and _name_of(provider) != source.provider:
            raise ConfigurationError(
                MSG_LIVE_DATA_OTHER_BACKEND.format(
                    source=source.name, owner=source.provider, provider=_name_of(provider)
                )
            )
        return router.get_live_data_provider(provider if provider is not None else source.provider), source.name
    if provider is not None:
        return router.get_live_data_provider(provider), source
    failures: dict[str, str] = {}
    backends = _or_raise(usable_backends(router, failures), failures)
    if len(backends) == 1:
        return backends[0], source
    raise ConfigurationError(
        MSG_LIVE_DATA_WHICH_BACKEND.format(source=source, providers=", ".join(backend.name for backend in backends))
    )


def search_every_backend(
    router: _LiveDataLookup, query: str | None, *, limit: int | None, offset: int | None
) -> LiveDataSourcePage:
    """One page of the sources every usable backend lists, or matches for ``query``, asked at once."""
    check_page(limit, offset)
    failures: dict[str, str] = {}
    backends = _or_raise(usable_backends(router, failures), failures)
    want = None if limit is None else (offset or 0) + limit
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
    check_page(limit, offset)
    failures: dict[str, str] = {}
    backends = _or_raise(usable_backends(router, failures), failures)
    want = None if limit is None else (offset or 0) + limit
    results = await asyncio.gather(*(_aask(backend, query, want) for backend in backends), return_exceptions=True)
    answers = {backend.name: result for backend, result in zip(backends, results, strict=True)}
    return _merged(answers, failures, query, limit=limit, offset=offset)


# -- helpers ------------------------------------------------------------------


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


def _keyed_in_code(router: _LiveDataLookup, name: str) -> bool:
    """Whether ``provider_config`` hands built-in backend ``name`` a key or a client.

    The plain settings are read first, without importing the backend; its own
    ``key_argument`` only after, and a backend whose SDK is missing has none
    to read, so it was never a candidate.
    """
    config = router._config_for(name)
    if any(config.get(setting) for setting in _KEY_SETTINGS):
        return True
    try:
        provider_class = get_live_data_provider_class(name)
    except ProviderNotInstalledError:
        return False
    # Oxylabs names its Web API key `web_api_key`, as `api_key` there is extract's password.
    return bool(config.get(getattr(provider_class, "key_argument", "api_key")))


def _name_of(provider: str | Provider | LiveDataProvider) -> str:
    return provider.name if isinstance(provider, LiveDataProvider) else provider_name(provider)


def _no_backend() -> ConfigurationError:
    names = sorted({env_var for env_vars in LIVE_DATA_ENV_BY_PROVIDER.values() for env_var in env_vars})
    return ConfigurationError(MSG_LIVE_DATA_NO_BACKEND.format(env_vars=", ".join(names)))


def _or_raise(backends: list[LiveDataProvider], failures: dict[str, str]) -> list[LiveDataProvider]:
    """``backends``, or the reason there are none: no key anywhere, or every keyed one failed to build."""
    if backends:
        return backends
    if failures:
        raise ConfigurationError(MSG_LIVE_DATA_EVERY_BACKEND_FAILED.format(failures=failures))
    raise _no_backend()


__all__: list[str] = ["asearch_every_backend", "backend_for", "search_every_backend", "usable_backends"]
