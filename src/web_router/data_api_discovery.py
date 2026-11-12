"""Data API discovery in ``search``: the data APIs a query names, listed beside the web results.

Matching is local and free: it reads the catalog and calls nothing (see
:mod:`web_router.data_api_names` for when a query names a data API). A match is
only ever listed; running one stays :meth:`~web_router.WebRouter.call_vendor`'s.
Only read endpoints are listed: a search is a question, and an endpoint that
changes data is found with :meth:`~web_router.WebRouter.find_vendor_endpoints`.
"""

from __future__ import annotations

import logging
from typing import Any

from web_router.client_requests import build_search_request
from web_router.constants_data_api import (
    DATA_API_SEARCH_LIMIT,
    DATA_APIS_ONLY_PROVIDER,
    MSG_DATA_API_CATALOG_SKIPPED,
    MSG_DATA_APIS_ONLY_CONFLICT,
    MSG_DATA_APIS_ONLY_OFF,
)
from web_router.data_api_names import named_endpoints
from web_router.exceptions import ConfigurationError
from web_router.types import SearchResponse
from web_router.types_data_api import DataApiMatch
from web_router.vendors.catalog import VendorCatalog, get_catalog
from web_router.vendors.credentials import CredentialSource, missing_credentials

logger = logging.getLogger(__name__)


def find_matches(
    query: str,
    source: CredentialSource,
    *,
    configured_only: bool = False,
    limit: int | None = None,
    catalog: VendorCatalog | None = None,
) -> list[DataApiMatch]:
    """The endpoints of the data APIs ``query`` names, best first; ``configured_only`` keeps the callable ones."""
    matches: list[DataApiMatch] = []
    for owner, endpoint in named_endpoints(catalog or get_catalog(), query):
        missing = missing_credentials(owner, source, endpoint)
        if configured_only and missing:
            continue
        matches.append(
            DataApiMatch(
                endpoint=endpoint.id,
                data_api=owner.vendor,
                name=endpoint.name,
                summary=endpoint.summary,
                kind=endpoint.kind,
                docs_url=endpoint.docs_url,
                configured=not missing,
                missing=missing,
            )
        )
        if len(matches) >= (DATA_API_SEARCH_LIMIT if limit is None else limit):
            break
    return matches


def load_catalog(data_apis: bool) -> VendorCatalog | None:
    """The catalog a web search lists matches from, read before the backend is called; ``None`` when it will list none.

    A catalog that does not load -- a broken file in ``WEB_ROUTER_VENDOR_CATALOG`` -- is logged and skipped, so the
    web search it rides on still answers.
    """
    if not data_apis:
        return None
    try:
        return get_catalog()
    except ConfigurationError as exc:
        logger.warning(MSG_DATA_API_CATALOG_SKIPPED, exc)
        return None


def with_matches(
    response: SearchResponse, source: CredentialSource, *, catalog: VendorCatalog | None, configured_only: bool
) -> SearchResponse:
    """``response`` with its data API matches, or untouched when there is no catalog to read them from."""
    if catalog is None:
        return response
    matches = find_matches(response.query, source, configured_only=configured_only, catalog=catalog)
    return response.model_copy(update={"data_apis": matches}) if matches else response


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
