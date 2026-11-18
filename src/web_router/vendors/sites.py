"""Which catalog vendors serve a web page's site, by the ``sites`` their files name, and their endpoints for it."""

from __future__ import annotations

from urllib.parse import urlsplit

from web_router.constants_vendors import VENDOR_FIND_STOP_WORDS
from web_router.vendors.catalog import VendorCatalog, _score, words
from web_router.vendors.schema import Vendor, VendorEndpoint


def vendors_for_site(catalog: VendorCatalog, url: str) -> list[Vendor]:
    """The vendors whose ``sites`` name ``url``'s host or a domain it belongs to, by name.

    A site matches its own host and its subdomains only: ``clips.test`` serves ``m.clips.test`` but not
    ``notclips.test``.
    """
    host = (urlsplit(url).hostname or "").lower()
    return [
        vendor for vendor in catalog.all() if any(host == site or host.endswith(f".{site}") for site in vendor.sites)
    ]


def site_endpoints(catalog: VendorCatalog, url: str) -> list[tuple[Vendor, VendorEndpoint]]:
    """The read endpoints of every vendor that serves ``url``'s site, best first.

    Those whose words the URL's path holds come first, across all those vendors, so one vendor's endpoints never
    crowd out another's that the path names. The query only orders what the path leaves tied (``/watch?list=...``),
    and the rest keep catalog order: vendors by name, each one's endpoints as its file lists them.
    """
    parts = urlsplit(url)
    path, query = (words(text) - VENDOR_FIND_STOP_WORDS for text in (parts.path, parts.query))
    candidates = [
        (vendor, endpoint)
        for vendor in vendors_for_site(catalog, url)
        for endpoint in vendor.endpoints
        if endpoint.kind == "read"
    ]
    return sorted(candidates, key=lambda pair: (-_score(path, pair[1], pair[0]), -_score(query, pair[1], pair[0])))
