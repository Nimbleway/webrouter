"""Provider implementations.

Concrete providers are imported lazily by :mod:`web_router.registry`, so a
missing optional dependency only fails when that provider is actually used.

There is a base class per method, and a REST-backed flavour of each:

- :class:`SearchProvider` / :class:`ExtractProvider` -- wrap an API that has an
  SDK worth depending on.
- :class:`HttpSearchProvider` / :class:`HttpExtractProvider` -- talk to a REST
  endpoint directly. The shorter path, and what most providers here use.

A vendor serving both methods writes one class per method; nothing requires a
backend to serve both.

:func:`build_payload` assembles a request body from the options a provider maps.
"""

from typing import TYPE_CHECKING, Any

from web_router.constants import EXTRA_SEARCH
from web_router.constants_providers import HTTPX_MODULE, MSG_EXTRA_MISSING
from web_router.providers._utils import build_payload
from web_router.providers.base import ExtractProvider, SearchProvider

if TYPE_CHECKING:
    from web_router.providers._http import HttpExtractProvider, HttpSearchProvider

_HTTP_BASES = ("HttpSearchProvider", "HttpExtractProvider")

__all__ = [
    "ExtractProvider",
    "HttpExtractProvider",
    "HttpSearchProvider",
    "SearchProvider",
    "build_payload",
]


def __getattr__(name: str) -> Any:
    """Resolve the HTTP base classes on first use.

    They need ``httpx``, which ships with the method extras rather than the base
    package. Importing them eagerly here would make ``import web_router`` fail
    for anyone who installed web-router without any extra.
    """
    if name in _HTTP_BASES:
        try:
            import web_router.providers._http as http
        except ModuleNotFoundError as exc:
            if exc.name != HTTPX_MODULE:
                raise
            # Still a ModuleNotFoundError, so code catching that keeps working, but naming the fix.
            message = MSG_EXTRA_MISSING.format(what=name, module=HTTPX_MODULE, extra=EXTRA_SEARCH)
            raise ModuleNotFoundError(message, name=HTTPX_MODULE) from exc
        return getattr(http, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
