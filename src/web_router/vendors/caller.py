"""Sending one vendor call: resolve the credentials, place the inputs, forward the answer.

The caller is vendor-blind. Everything it does is read off the vendor's
catalog file -- where the API lives, what to put on each request, where each
input goes -- and the vendor's answer comes back as the vendor sent it. Three
guarantees hold for every vendor:

* **Credentials only go to the vendor's own host.** The URL is built from the
  file's ``base_url`` and checked after joining, redirects are never followed,
  and a ``base_url`` that varies by region only takes values its file lists.
* **Nothing is dropped or invented.** An input the endpoint does not declare
  raises, and a required one left out raises, before anything is sent.
* **Writes are opt-in.** An endpoint marked ``write``, or a raw request with a
  method that is not a read, raises unless the call passes ``allow_writes``.
"""

from __future__ import annotations

from collections.abc import Mapping

import httpx

from web_router.constants_vendors import (
    MSG_VENDOR_HOST_ESCAPE,
    MSG_VENDOR_WRITE_REFUSED,
    VENDOR_HTTP_TIMEOUT,
    VENDOR_READ_METHODS,
    VENDOR_REQUEST_HEADERS,
)
from web_router.exceptions import ConfigurationError
from web_router.exceptions_vendors import VendorWriteRefusedError
from web_router.vendors.catalog import VendorCatalog, get_catalog
from web_router.vendors.credentials import CredentialSource, credential_source
from web_router.vendors.plan import _Plan
from web_router.vendors.response import Prepared, response, send_kwargs, transport_error
from web_router.vendors.schema import ACCESS_TOKEN
from web_router.vendors.tokens import TokenCache, TokenRequest, token_request
from web_router.vendors.types import VendorResponse
from web_router.vendors.values import applied, render, resolve, wants_token


class VendorCaller:
    """Sends vendor calls for one router: its credentials, its token cache, its HTTP clients."""

    def __init__(
        self,
        credentials: CredentialSource | Mapping[str, Mapping[str, str]] | None = None,
        *,
        catalog: VendorCatalog | None = None,
        timeout: float = VENDOR_HTTP_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
        async_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.credentials = credential_source(credentials)
        self._catalog = catalog
        self._timeout = timeout
        self._transport = transport
        self._async_transport = async_transport
        self._tokens = TokenCache()
        self._sync: httpx.Client | None = None
        self._async: httpx.AsyncClient | None = None

    @property
    def catalog(self) -> VendorCatalog:
        return self._catalog if self._catalog is not None else get_catalog()

    # -- building ----------------------------------------------------------

    def _check_write(self, plan: _Plan, *, allow_writes: bool) -> None:
        writes = plan.method not in VENDOR_READ_METHODS
        if plan.endpoint is not None:
            writes = self.catalog.endpoint(plan.endpoint).writes
        if writes and not allow_writes:
            target = plan.endpoint or f"{plan.method} {plan.path}"
            vendor = plan.vendor.vendor
            raise VendorWriteRefusedError(vendor, target, MSG_VENDOR_WRITE_REFUSED.format(target=target, vendor=vendor))

    def _prepare(self, plan: _Plan, values: dict[str, str]) -> Prepared:
        vendor = plan.vendor
        base = render(vendor, vendor.base_for(plan.host), values).rstrip("/")
        url = base + plan.path
        expected, actual = httpx.URL(base), httpx.URL(url)
        if actual.scheme != "https" or actual.host != expected.host or actual.port != expected.port:
            raise ConfigurationError(
                MSG_VENDOR_HOST_ESCAPE.format(vendor=vendor.vendor, host=actual.host, expected=expected.host)
            )
        headers = dict(VENDOR_REQUEST_HEADERS)
        headers.update({name: render(vendor, value, values) for name, value in vendor.headers.items()})
        headers.update(plan.headers)
        query = list(plan.query)
        body = plan.body
        masked: set[str] = set()
        for injection in applied(plan, values):
            rendered = render(vendor, injection.value, values)
            if injection.location == "header":
                headers[injection.name] = rendered
            elif injection.location == "body":
                if body is None:
                    body = {}
                if not isinstance(body, dict):
                    raise ConfigurationError(
                        f"{vendor.vendor} takes its credential in a JSON object body; this call's body is not one"
                    )
                body = {**body, injection.name: rendered}
            else:
                query = [pair for pair in query if pair[0] != injection.name] + [(injection.name, rendered)]
                masked.add(injection.name)
        auth = None
        if vendor.auth.basic is not None:
            auth = (
                render(vendor, vendor.auth.basic.username, values),
                render(vendor, vendor.auth.basic.password, values),
            )
        secret_names = set(vendor.auth.credentials) | {ACCESS_TOKEN}
        secrets = [value for name, value in values.items() if name in secret_names]
        return Prepared(plan, url, query, headers, auth, secrets, masked, body)

    def _token_request(self, plan: _Plan, values: dict[str, str]) -> TokenRequest | None:
        vendor = plan.vendor
        oauth = vendor.auth.oauth2
        if oauth is None or not wants_token(plan):
            return None
        for template in oauth.templates():
            render(vendor, template, values)
        return token_request(vendor.vendor, oauth, values)

    # -- sending -----------------------------------------------------------

    def _client(self) -> httpx.Client:
        if self._sync is None:
            self._sync = httpx.Client(timeout=self._timeout, follow_redirects=False, transport=self._transport)
        return self._sync

    def _aclient(self) -> httpx.AsyncClient:
        """The async client, built once and kept: an async router stays on the event loop it first ran on, as
        the providers' clients do."""
        if self._async is None:
            self._async = httpx.AsyncClient(
                timeout=self._timeout, follow_redirects=False, transport=self._async_transport
            )
        return self._async

    def _begin(self, plan: _Plan, *, allow_writes: bool) -> tuple[dict[str, str], TokenRequest | None]:
        """Everything before the network, shared by both paths: the write check, the values, the token to mint."""
        # Checked before anything else: a refused write should cost nothing, not even a token.
        self._check_write(plan, allow_writes=allow_writes)
        values = resolve(plan, self.credentials)
        return values, self._token_request(plan, values)

    def send(self, plan: _Plan, *, allow_writes: bool = False) -> VendorResponse:
        values, token = self._begin(plan, allow_writes=allow_writes)
        if token is not None:
            values[ACCESS_TOKEN] = self._tokens.get(token, self._client())
        prepared = self._prepare(plan, values)
        try:
            answer = self._client().request(plan.method, prepared.url, **send_kwargs(prepared))
        except httpx.HTTPError as exc:
            raise transport_error(prepared, exc) from None
        return response(prepared, answer)

    async def asend(self, plan: _Plan, *, allow_writes: bool = False) -> VendorResponse:
        values, token = self._begin(plan, allow_writes=allow_writes)
        if token is not None:
            values[ACCESS_TOKEN] = await self._tokens.aget(token, self._aclient())
        prepared = self._prepare(plan, values)
        try:
            answer = await self._aclient().request(plan.method, prepared.url, **send_kwargs(prepared))
        except httpx.HTTPError as exc:
            raise transport_error(prepared, exc) from None
        return response(prepared, answer)

    def close(self) -> None:
        if self._sync is not None:
            self._sync.close()
            self._sync = None

    async def aclose(self) -> None:
        if self._async is not None:
            await self._async.aclose()
            self._async = None
        self.close()
