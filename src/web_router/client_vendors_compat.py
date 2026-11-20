"""The routers' data API methods under the names they had as vendor methods, each warning and calling its replacement.

Kept apart from :mod:`web_router.client_vendors`: these go when the old names
do. :class:`VendorLookupMethods` holds the lookups both routers share;
:class:`SyncVendorMethods` and :class:`AsyncVendorMethods` the calls.
"""

from __future__ import annotations

import warnings
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, cast

from web_router.constants_vendors import MSG_DATA_API_DEPRECATED

if TYPE_CHECKING:
    from web_router.client_vendors import AsyncVendors, SyncVendors, _VendorLookup
    from web_router.vendors.schema import Vendor, VendorEndpoint
    from web_router.vendors.types import VendorResponse, VendorSummary


def warn_data_api_renamed(old: str, new: str, *, stacklevel: int = 3) -> None:
    """The one :class:`DeprecationWarning` for a vendor-era name, pointed at the caller ``stacklevel`` frames up."""
    warnings.warn(MSG_DATA_API_DEPRECATED.format(old=old, new=new), DeprecationWarning, stacklevel=stacklevel)


def renamed_credentials(new: Any, old: Any) -> Any:
    """``data_api_credentials=``, or the value passed under its old name ``vendor_credentials=``, with a warning."""
    if old is None:
        return new
    warn_data_api_renamed("vendor_credentials", "data_api_credentials", stacklevel=4)
    return old if new is None else new


class VendorLookupMethods:
    """The old lookup names, shared by both routers."""

    def vendors(self, *, category: str | None = None, configured: bool | None = None) -> list[VendorSummary]:
        """Deprecated: :meth:`data_apis`."""
        warn_data_api_renamed("vendors", "data_apis")
        return cast("_VendorLookup", self).data_apis(category=category, configured=configured)

    def vendor(self, name: str) -> Vendor:
        """Deprecated: :meth:`data_api`."""
        warn_data_api_renamed("vendor", "data_api")
        return cast("_VendorLookup", self).data_api(name)

    def vendor_endpoint(self, endpoint: str) -> VendorEndpoint:
        """Deprecated: :meth:`data_api_endpoint`."""
        warn_data_api_renamed("vendor_endpoint", "data_api_endpoint")
        return cast("_VendorLookup", self).data_api_endpoint(endpoint)

    def find_vendor_endpoints(self, query: str, **kwargs: Any) -> list[VendorEndpoint]:
        """Deprecated: :meth:`find_data_apis`."""
        warn_data_api_renamed("find_vendor_endpoints", "find_data_apis")
        return cast("_VendorLookup", self).find_data_apis(query, **kwargs)


class SyncVendorMethods:
    """The sync router's old call names."""

    def call_vendor(
        self, endpoint: str, params: Mapping[str, Any] | None = None, *, allow_writes: bool = False
    ) -> VendorResponse:
        """Deprecated: :meth:`call_data_api`."""
        warn_data_api_renamed("call_vendor", "call_data_api")
        return cast("SyncVendors", self).call_data_api(endpoint, params, allow_writes=allow_writes)

    def vendor_request(
        self,
        vendor: str,
        method: str,
        path: str,
        *,
        query: Mapping[str, Any] | Sequence[tuple[str, Any]] | None = None,
        body: Any = None,
        headers: Mapping[str, str] | None = None,
        host: str | None = None,
        allow_writes: bool = False,
    ) -> VendorResponse:
        """Deprecated: :meth:`data_api_request`."""
        warn_data_api_renamed("vendor_request", "data_api_request")
        return cast("SyncVendors", self).data_api_request(
            vendor, method, path, query=query, body=body, headers=headers, host=host, allow_writes=allow_writes
        )


class AsyncVendorMethods:
    """The async router's old call names."""

    async def call_vendor(
        self, endpoint: str, params: Mapping[str, Any] | None = None, *, allow_writes: bool = False
    ) -> VendorResponse:
        """Deprecated: :meth:`call_data_api`."""
        warn_data_api_renamed("call_vendor", "call_data_api")
        return await cast("AsyncVendors", self).call_data_api(endpoint, params, allow_writes=allow_writes)

    async def vendor_request(
        self,
        vendor: str,
        method: str,
        path: str,
        *,
        query: Mapping[str, Any] | Sequence[tuple[str, Any]] | None = None,
        body: Any = None,
        headers: Mapping[str, str] | None = None,
        host: str | None = None,
        allow_writes: bool = False,
    ) -> VendorResponse:
        """Deprecated: :meth:`data_api_request`."""
        warn_data_api_renamed("vendor_request", "data_api_request")
        return await cast("AsyncVendors", self).data_api_request(
            vendor, method, path, query=query, body=body, headers=headers, host=host, allow_writes=allow_writes
        )
