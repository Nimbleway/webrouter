"""Data API discovery: catalog endpoints listed beside a ``search``'s web results and an ``extract``'s pages.

Search lists the data APIs a query names (see :mod:`web_router.data_api_names`);
extract lists those whose catalog ``sites`` serve each page's site. Matching is
local and free: it reads the catalog and calls nothing. A match is
only ever listed; running one stays :meth:`~web_router.WebRouter.call_data_api`'s.
Only read endpoints are listed: a search or a page read is a question, and an endpoint that
changes data is found with :meth:`~web_router.WebRouter.find_data_apis`.
"""

from __future__ import annotations

import contextlib
import functools
import itertools
import logging
from collections.abc import Callable, Iterable
from typing import Any, TypeVar

from web_router.client_requests import build_search_request
from web_router.constants_data_api import (
    DATA_API_EXTRACT_LIMIT,
    DATA_API_SEARCH_LIMIT,
    DATA_APIS_ONLY_PROVIDER,
    MSG_DATA_API_CATALOG_SKIPPED,
    MSG_DATA_API_MATCHING_FAILED,
    MSG_DATA_APIS_ONLY_CONFLICT,
    MSG_DATA_APIS_ONLY_OFF,
)
from web_router.data_api_names import named_endpoints
from web_router.exceptions import ConfigurationError
from web_router.types import ExtractedDocument, ExtractResponse, SearchResponse
from web_router.types_data_api import DataApiMatch
from web_router.vendors.catalog import VendorCatalog, get_catalog
from web_router.vendors.credentials import CredentialSource, missing_credentials
from web_router.vendors.schema import Vendor, VendorEndpoint
from web_router.vendors.sites import site_endpoints

logger = logging.getLogger(__name__)

_Answer = TypeVar("_Answer", SearchResponse, ExtractResponse)


def _match(owner: Vendor, endpoint: VendorEndpoint, source: CredentialSource) -> DataApiMatch:
    missing = missing_credentials(owner, source, endpoint)
    return DataApiMatch(
        endpoint=endpoint.id,
        data_api=owner.vendor,
        name=endpoint.name,
        summary=endpoint.summary,
        kind=endpoint.kind,
        docs_url=endpoint.docs_url,
        configured=not missing,
        missing=missing,
    )


def _collect(
    candidates: Iterable[tuple[Vendor, VendorEndpoint]], source: CredentialSource, *, configured_only: bool, limit: int
) -> list[DataApiMatch]:
    """The first ``limit`` of ``candidates`` as matches; ``configured_only`` keeps the callable ones."""
    matches = (_match(owner, endpoint, source) for owner, endpoint in candidates)
    return list(itertools.islice((match for match in matches if match.configured or not configured_only), limit))


def find_matches(
    query: str,
    source: CredentialSource,
    *,
    configured_only: bool = False,
    limit: int | None = None,
    catalog: VendorCatalog | None = None,
) -> list[DataApiMatch]:
    """The endpoints of the data APIs ``query`` names, best first; ``configured_only`` keeps the callable ones."""
    candidates = named_endpoints(catalog or get_catalog(), query)
    limit = DATA_API_SEARCH_LIMIT if limit is None else limit
    return _collect(candidates, source, configured_only=configured_only, limit=limit)


def load_catalog(data_apis: bool) -> VendorCatalog | None:
    """The catalog a web search lists matches from, read before the backend is called; ``None`` when it will list none.

    A catalog that does not load -- a broken file in ``WEB_ROUTER_VENDOR_CATALOG`` -- is logged and skipped, so the
    web search it rides on still answers.
    """
    if not data_apis:
        return None
    try:
        return get_catalog()
    except (ConfigurationError, OSError) as exc:
        logger.warning(MSG_DATA_API_CATALOG_SKIPPED, exc)
        return None


def warm_catalog(data_apis: bool) -> None:
    """Read the catalog into its cache, quietly: an async search runs this in a thread, off the event loop."""
    if data_apis:
        with contextlib.suppress(ConfigurationError, OSError):
            get_catalog()


def _guarded(
    response: _Answer, catalog: VendorCatalog | None, fields: Callable[[VendorCatalog], dict[str, Any]]
) -> _Answer:
    """``response`` with the ``fields`` its matches fill; with no catalog, or on a matching bug (logged), untouched."""
    if catalog is None:
        return response
    try:
        return response.model_copy(update=fields(catalog))
    except Exception:
        logger.exception(MSG_DATA_API_MATCHING_FAILED)
        return response


def with_matches(
    response: SearchResponse, source: CredentialSource, *, catalog: VendorCatalog | None, configured_only: bool
) -> SearchResponse:
    """``response`` with its data API matches, or untouched when there is no catalog to read them from."""
    matches = functools.partial(find_matches, response.query, source, configured_only=configured_only)
    return _guarded(response, catalog, lambda c: {"data_apis": matches(catalog=c)})


def site_matches(
    url: str, source: CredentialSource, *, configured_only: bool = False, catalog: VendorCatalog | None = None
) -> list[DataApiMatch]:
    """The read endpoints of every data API that serves ``url``'s site, best first (see :func:`site_endpoints`)."""
    candidates = site_endpoints(catalog or get_catalog(), url)
    return _collect(candidates, source, configured_only=configured_only, limit=DATA_API_EXTRACT_LIMIT)


def with_site_matches(
    response: ExtractResponse, source: CredentialSource, *, catalog: VendorCatalog | None, configured_only: bool
) -> ExtractResponse:
    """``response`` with each page's data API matches, or untouched when there is no catalog to read them from."""

    def page(document: ExtractedDocument, catalog: VendorCatalog) -> ExtractedDocument:
        matches = site_matches(document.url, source, configured_only=configured_only, catalog=catalog)
        return document.model_copy(update={"data_apis": matches})

    return _guarded(response, catalog, lambda c: {"documents": [page(document, c) for document in response.documents]})


def matches_only(
    query: str,
    source: CredentialSource,
    *,
    data_apis: bool,
    configured_only: bool,
    max_results: int | None,
    web_options: dict[str, Any],
) -> SearchResponse:
    """A ``search(data_apis_only=True)``: the matches alone, with no web backend called.

    A web option set alongside it raises rather than being dropped, since nothing here would honour it. A catalog
    that does not load raises too: the matches are all this search asked for.
    """
    if not data_apis:
        raise ValueError(MSG_DATA_APIS_ONLY_OFF)
    # The web path's own checks -- a query, and max_results of at least 1 -- hold here too.
    request = build_search_request(query, provider_options=None, max_results=max_results)
    refused = sorted(name for name, value in web_options.items() if value is not None)
    if refused:
        raise ValueError(MSG_DATA_APIS_ONLY_CONFLICT.format(names=", ".join(refused)))
    matches = find_matches(request.query, source, configured_only=configured_only, limit=request.max_results)
    return SearchResponse(provider=DATA_APIS_ONLY_PROVIDER, query=request.query, data_apis=matches)
