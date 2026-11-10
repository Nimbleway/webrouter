"""Vendor data: a vendor's own API -- Google Ads, Datadog, Search Console -- called with your credentials.

The router methods are :meth:`~web_router.WebRouter.vendors`,
:meth:`~web_router.WebRouter.vendor`, :meth:`~web_router.WebRouter.vendor_endpoint`,
:meth:`~web_router.WebRouter.find_vendor_endpoints`,
:meth:`~web_router.WebRouter.call_vendor` and
:meth:`~web_router.WebRouter.vendor_request`. This package holds what they
share: the catalog's file format (:mod:`~web_router.vendors.schema`), its
loader (:mod:`~web_router.vendors.catalog`), where credentials come from
(:mod:`~web_router.vendors.credentials`) and the vendor-blind code that sends
a call (:mod:`~web_router.vendors.caller`, which needs the ``vendors`` extra).
"""

from web_router.vendors.catalog import VendorCatalog, get_catalog, load_vendor_file, reload_catalog
from web_router.vendors.credentials import (
    ChainedCredentials,
    CredentialSource,
    EnvCredentials,
    MappingCredentials,
)
from web_router.vendors.schema import (
    Vendor,
    VendorAuth,
    VendorCredential,
    VendorEndpoint,
    VendorParam,
    VendorSetting,
)
from web_router.vendors.types import VendorResponse, VendorSummary

__all__ = [
    "ChainedCredentials",
    "CredentialSource",
    "EnvCredentials",
    "MappingCredentials",
    "Vendor",
    "VendorAuth",
    "VendorCatalog",
    "VendorCredential",
    "VendorEndpoint",
    "VendorParam",
    "VendorResponse",
    "VendorSetting",
    "VendorSummary",
    "get_catalog",
    "load_vendor_file",
    "reload_catalog",
]
