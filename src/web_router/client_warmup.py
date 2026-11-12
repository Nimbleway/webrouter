"""``warmup()`` on the routers: pay a first call's setup at startup instead of on the first request.

A router builds nothing until a call needs it -- the provider classes are
imported, their SDKs loaded and their HTTP clients (and TLS contexts) built on
first use. That is cheap for a script and a stall for a service: the first async
call blocked its event loop for a few hundred milliseconds. ``warmup()`` does
that work up front for search and extract: it builds the default backend of
each, every member of an ``auto`` chain, and the client the router will call
them through. It sends nothing and bills nothing. A member that cannot be built
here -- a missing extra, a missing key -- is left out, as ``auto`` would leave it.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, Any, ClassVar

from web_router.constants_production import LOG_WARMUP_FAILED
from web_router.exceptions import WebRouterError

if TYPE_CHECKING:
    from web_router.providers.base import ExtractProvider, SearchProvider

logger = logging.getLogger(__name__)


class _Warmup:
    _client_attribute: ClassVar[str]
    """The client this router calls its providers through: the sync one, or the async one."""

    if TYPE_CHECKING:

        def get_provider(self, name: Any = None) -> SearchProvider: ...

        def get_extract_provider(self, name: Any = None) -> ExtractProvider: ...

    def warmup(self) -> list[str]:
        """Build the search and extract backends a first call would, with their clients; return the names built.

        A backend whose client cannot be built is left out of the list and logged at INFO.

        Call it once at startup, before the router serves requests. Safe to call again: what is built is reused.
        """
        warmed: list[str] = []
        for lookup in (self.get_provider, self.get_extract_provider):
            for backend in _backends(lookup):
                try:
                    getattr(backend, self._client_attribute, None)
                except Exception as exc:  # a client that cannot be built now fails the call that needs it, as before
                    logger.info(LOG_WARMUP_FAILED, backend.name, type(exc).__name__)
                    continue
                warmed.append(backend.name)
        return list(dict.fromkeys(warmed))


def _backends(lookup: Callable[[], Any]) -> Iterator[Any]:
    """The router's default backend for one method, or each member of its ``auto`` chain that can be built."""
    try:
        chain = lookup()
    except WebRouterError:
        return  # the default serves another method only
    members = getattr(chain, "members", None)
    if members is None:
        yield chain
        return
    for position in range(len(members)):
        try:
            yield chain._member(position)
        except (WebRouterError, ImportError):
            continue


class SyncWarmup(_Warmup):
    _client_attribute: ClassVar[str] = "_client"


class AsyncWarmup(_Warmup):
    _client_attribute: ClassVar[str] = "_async_client"
