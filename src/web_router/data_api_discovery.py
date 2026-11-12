"""Data API discovery: catalog endpoints listed beside a ``search``'s web results and an ``extract``'s pages.

Search matches the query's words; extract matches each page's site against
the catalog's ``sites``. Matching is local and free: it reads the catalog and calls nothing. A match is
only ever listed; running one stays :meth:`~web_router.WebRouter.call_vendor`'s.
Only read endpoints are listed: a search or a page read is a question, and an endpoint that
changes data is found with :meth:`~web_router.WebRouter.find_vendor_endpoints`.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from web_router.client_requests import build_search_request
from web_router.constants_vendors import (
    DATA_API_EXTRACT_LIMIT,
    DATA_API_MIN_SCORE,
    DATA_API_SEARCH_LIMIT,
    DATA_APIS_ONLY_PROVIDER,
    MSG_DATA_APIS_ONLY_CONFLICT,
    MSG_DATA_APIS_ONLY_OFF,
)
from web_router.types import ExtractedDocument, ExtractResponse, SearchResponse
from web_router.types_data_api import DataApiMatch
from web_router.vendors.catalog import get_catalog
from web_router.vendors.credentials import CredentialSource, missing_credentials
from web_router.vendors.schema import Vendor, VendorEndpoint


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


def find_matches(
    query: str, source: CredentialSource, *, configured_only: bool = False, limit: int | None = None
) -> list[DataApiMatch]:
    """The endpoints matching ``query`` well enough to list, best first; ``configured_only`` keeps the callable ones."""
    catalog = get_catalog()
    matches: list[DataApiMatch] = []
    for endpoint in catalog.find(query, kind="read", min_score=DATA_API_MIN_SCORE):
        match = _match(catalog.vendor(endpoint.vendor), endpoint, source)
        if configured_only and not match.configured:
            continue
        matches.append(match)
        if len(matches) >= (DATA_API_SEARCH_LIMIT if limit is None else limit):
            break
    return matches


def with_matches(
    response: SearchResponse, source: CredentialSource, *, data_apis: bool, configured_only: bool
) -> SearchResponse:
    """``response`` with its data API matches, or untouched when discovery is off."""
    if not data_apis:
        return response
    matches = find_matches(response.query, source, configured_only=configured_only)
    return response.model_copy(update={"data_apis": matches}) if matches else response


def site_matches(url: str, source: CredentialSource, *, configured_only: bool = False) -> list[DataApiMatch]:
    """The read endpoints of every data API that serves ``url``'s site: those whose words the URL's path holds first."""
    catalog = get_catalog()
    path = urlsplit(url)
    matches: list[DataApiMatch] = []
    for owner in catalog.for_site(url):
        ranked = catalog.find(f"{path.path} {path.query}", vendor=owner.vendor, kind="read")
        endpoints = [*ranked, *(item for item in owner.endpoints if item.kind == "read" and item not in ranked)]
        for endpoint in endpoints:
            match = _match(owner, endpoint, source)
            if configured_only and not match.configured:
                continue
            matches.append(match)
            if len(matches) >= DATA_API_EXTRACT_LIMIT:
                return matches
    return matches


def with_site_matches(
    response: ExtractResponse, source: CredentialSource, *, data_apis: bool, configured_only: bool
) -> ExtractResponse:
    """``response`` with each page's data API matches, or untouched when discovery is off or nothing matched."""
    if not data_apis:
        return response
    documents: list[ExtractedDocument] = []
    for document in response.documents:
        matches = site_matches(document.url, source, configured_only=configured_only)
        documents.append(document.model_copy(update={"data_apis": matches}) if matches else document)
    return response.model_copy(update={"documents": documents})


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

    A web option set alongside it raises rather than being dropped, since nothing here would honour it.
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
