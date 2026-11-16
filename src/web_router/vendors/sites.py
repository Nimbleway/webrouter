"""Which catalog vendors serve a web page's site, by the ``sites`` their files name."""

from __future__ import annotations

from urllib.parse import urlsplit

from web_router.vendors.catalog import VendorCatalog
from web_router.vendors.schema import Vendor


def vendors_for_site(catalog: VendorCatalog, url: str) -> list[Vendor]:
    """The vendors whose ``sites`` name ``url``'s host or a domain it belongs to, by name.

    A site matches its own host and its subdomains only: ``clips.test`` serves ``m.clips.test`` but not
    ``notclips.test``.
    """
    host = (urlsplit(url).hostname or "").lower()
    return [
        vendor for vendor in catalog.all() if any(host == site or host.endswith(f".{site}") for site in vendor.sites)
    ]
