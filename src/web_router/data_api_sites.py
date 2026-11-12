"""Data API discovery in ``extract``: the data APIs that serve each page's site, listed on the page.

A catalog entry names the websites its API serves in ``sites`` (bare hosts; a
subdomain matches its domain). Matching is local and free, as in search (see
:mod:`web_router.data_api_discovery`): a match is listed, never run.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from web_router.constants_data_api import DATA_API_EXTRACT_LIMIT
from web_router.data_api_discovery import listed
from web_router.types import ExtractedDocument, ExtractResponse
from web_router.types_data_api import DataApiMatch
from web_router.vendors.catalog import VendorCatalog
from web_router.vendors.credentials import CredentialSource
from web_router.vendors.schema import Vendor, VendorEndpoint


def site_endpoints(catalog: VendorCatalog, url: str) -> list[tuple[Vendor, VendorEndpoint]]:
    """The read endpoints of every data API that serves ``url``'s site: those whose words the URL's path holds first."""
    parts = urlsplit(url)
    found: list[tuple[Vendor, VendorEndpoint]] = []
    for owner in catalog.for_site(url):
        ranked = catalog.find(f"{parts.path} {parts.query}", vendor=owner.vendor, kind="read")
        rest = [endpoint for endpoint in owner.endpoints if endpoint.kind == "read" and endpoint not in ranked]
        found.extend((owner, endpoint) for endpoint in [*ranked, *rest])
    return found


def site_matches(
    catalog: VendorCatalog, url: str, source: CredentialSource, *, configured_only: bool = False
) -> list[DataApiMatch]:
    """At most :data:`DATA_API_EXTRACT_LIMIT` matches for the page at ``url``."""
    pairs = site_endpoints(catalog, url)
    return listed(pairs, source, configured_only=configured_only, limit=DATA_API_EXTRACT_LIMIT)


def with_site_matches(
    response: ExtractResponse, source: CredentialSource, *, catalog: VendorCatalog | None, configured_only: bool
) -> ExtractResponse:
    """``response`` with each page's data API matches, or untouched when there is no catalog to read them from."""
    if catalog is None:
        return response
    documents: list[ExtractedDocument] = []
    for document in response.documents:
        matches = site_matches(catalog, document.url, source, configured_only=configured_only)
        documents.append(document.model_copy(update={"data_apis": matches}) if matches else document)
    return response.model_copy(update={"documents": documents})
