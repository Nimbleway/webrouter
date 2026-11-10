"""Vendor data on the routers: a vendor's own API, called with your credentials.

Not a method with interchangeable backends. ``search`` asks "who can answer
this?"; a vendor call asks "what does *my* Google Ads account say?", and only
Google Ads can answer. So there is no ``auto``, nothing is normalized, and the
vendor's answer comes back in its own shape -- the ``run_template`` rule,
for the same reason. What the router adds is everything around the call: a
catalog to find the endpoint in, credentials placed the way the vendor wants
them, OAuth tokens refreshed, and three guarantees -- credentials never leave
the vendor's host, no input is dropped or invented, and writes are opt-in.

:class:`~web_router.WebRouter` and :class:`~web_router.AsyncWebRouter` take these
as mixins, so the methods sit beside ``search`` and ``extract``.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from web_router.constants_vendors import EXTRA_VENDORS
from web_router.exceptions import ProviderNotInstalledError
from web_router.vendors.catalog import get_catalog
from web_router.vendors.credentials import CredentialSource, credential_source, summarize
from web_router.vendors.schema import Vendor, VendorEndpoint
from web_router.vendors.types import VendorResponse, VendorSummary

if TYPE_CHECKING:
    from web_router.vendors.caller import VendorCaller
    from web_router.vendors.plan import _Plan

VendorCredentials = CredentialSource | Mapping[str, Mapping[str, str]] | None
"""``vendor_credentials=``: a source, or ``{vendor: {name: value}}`` read before the environment."""


class _VendorLookup:
    """What both routers share: the catalog lookups, and the caller built on first use."""

    _vendor_credentials: VendorCredentials
    _vendor_caller: VendorCaller | None
    _vendor_lock: threading.Lock

    def _init_vendors(self, vendor_credentials: VendorCredentials) -> None:
        self._vendor_credentials = vendor_credentials
        self._vendor_caller = None
        self._vendor_lock = threading.Lock()

    def _caller(self) -> VendorCaller:
        with self._vendor_lock:
            if self._vendor_caller is None:
                try:
                    from web_router.vendors.caller import VendorCaller
                except ImportError as exc:  # httpx is the vendors extra
                    raise ProviderNotInstalledError("vendors", EXTRA_VENDORS) from exc
                self._vendor_caller = VendorCaller(self._vendor_credentials)
            return self._vendor_caller

    def vendor_credential_source(self) -> CredentialSource:
        """Where this router reads vendor credentials: ``vendor_credentials=`` first, then the environment."""
        return credential_source(self._vendor_credentials)

    def vendors(
        self, *, category: str | None = None, configured: bool | None = None, data: str | None = None
    ) -> list[VendorSummary]:
        """Every vendor in the catalog, by name, with whether this router holds its credentials.

        ``configured=True`` keeps the vendors every required credential is set
        for, ``False`` the ones still missing one -- each row names the
        variable to set. ``data`` keeps the vendors whose API answers with that
        kind of data: ``'public'``, ``'account'`` or ``'service'``.
        """
        source = self.vendor_credential_source()
        rows = [summarize(vendor, source) for vendor in get_catalog().all()]
        if category is not None:
            rows = [row for row in rows if row.category == category]
        if configured is not None:
            rows = [row for row in rows if row.configured is configured]
        if data is not None:
            rows = [row for row in rows if row.data == data]
        return rows

    def vendor(self, name: str) -> Vendor:
        """One vendor's catalog entry: its API, its auth, its settings, and every endpoint it names."""
        return get_catalog().vendor(name)

    def vendor_endpoint(self, endpoint: str) -> VendorEndpoint:
        """One endpoint, by its id (``'<vendor>.<job>'``): its inputs, notes, and a recorded answer if any."""
        return get_catalog().endpoint(endpoint)

    def find_vendor_endpoints(
        self,
        query: str,
        *,
        vendor: str | None = None,
        category: str | None = None,
        kind: str | None = None,
        limit: int | None = None,
    ) -> list[VendorEndpoint]:
        """Endpoints matching ``query``'s words, best first -- "google ads spend by campaign", say.

        ``kind='read'`` leaves out every endpoint that changes data.
        """
        from web_router.constants_vendors import VENDOR_FIND_LIMIT

        return get_catalog().find(
            query, vendor=vendor, category=category, kind=kind, limit=VENDOR_FIND_LIMIT if limit is None else limit
        )

    @staticmethod
    def _endpoint_plan(endpoint: str, params: Mapping[str, Any] | None) -> _Plan:
        """The request ``call_vendor`` sends, built once for both routers."""
        from web_router.vendors.plan import plan_endpoint

        found = get_catalog().endpoint(endpoint)
        return plan_endpoint(get_catalog().vendor(found.vendor), found, dict(params or {}))

    @staticmethod
    def _raw_plan(
        vendor: str,
        method: str,
        path: str,
        *,
        query: Mapping[str, Any] | Sequence[tuple[str, Any]] | None,
        body: Any,
        headers: Mapping[str, str] | None,
        host: str | None,
    ) -> _Plan:
        """The request ``vendor_request`` sends, built once for both routers."""
        from web_router.vendors.plan import plan_raw

        return plan_raw(get_catalog().vendor(vendor), method, path, query=query, body=body, headers=headers, host=host)


class SyncVendors(_VendorLookup):
    def call_vendor(
        self,
        endpoint: str,
        params: Mapping[str, Any] | None = None,
        *,
        allow_writes: bool = False,
    ) -> VendorResponse:
        """Call one catalog endpoint with ``params`` and return the vendor's answer, untouched.

        ``params`` are the endpoint's own, named as :meth:`vendor_endpoint`
        lists them; each goes where the endpoint declares -- path, query, body
        or header. One it does not declare raises
        :class:`~web_router.UnsupportedParameterError`, and a required one
        left out :class:`~web_router.MissingParameterError`, before anything is
        sent. An endpoint marked ``write`` raises
        :class:`~web_router.VendorWriteRefusedError` unless ``allow_writes``.
        An HTTP error raises :class:`~web_router.ProviderError` with every
        credential masked out of the vendor's message.
        """
        return self._caller().send(self._endpoint_plan(endpoint, params), allow_writes=allow_writes)

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
        """Call any path on ``vendor``'s API with its credentials, for an endpoint the catalog does not name.

        ``path`` is relative to the vendor's API (``'/api/v1/monitor'``); a
        scheme or a host raises, so the credentials only ever go to the
        vendor. ``body`` is sent as JSON. A method other than GET, HEAD or
        OPTIONS needs ``allow_writes`` -- even when the vendor reads over POST,
        since nothing here can tell which. ``host`` picks one of the vendor's
        other named API hosts (its catalog entry lists them under ``hosts``).
        """
        plan = self._raw_plan(vendor, method, path, query=query, body=body, headers=headers, host=host)
        return self._caller().send(plan, allow_writes=allow_writes)

    def _close_vendors(self) -> None:
        if self._vendor_caller is not None:
            self._vendor_caller.close()


class AsyncVendors(_VendorLookup):
    async def call_vendor(
        self,
        endpoint: str,
        params: Mapping[str, Any] | None = None,
        *,
        allow_writes: bool = False,
    ) -> VendorResponse:
        """Call one catalog endpoint and return the vendor's answer, untouched. As on the sync router."""
        return await self._caller().asend(self._endpoint_plan(endpoint, params), allow_writes=allow_writes)

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
        """Call any path on ``vendor``'s API with its credentials. As on the sync router."""
        plan = self._raw_plan(vendor, method, path, query=query, body=body, headers=headers, host=host)
        return await self._caller().asend(plan, allow_writes=allow_writes)

    async def _aclose_vendors(self) -> None:
        if self._vendor_caller is not None:
            await self._vendor_caller.aclose()
