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
from dataclasses import dataclass
from typing import Any

import httpx

from web_router.constants_vendors import (
    MSG_VENDOR_HOST_ESCAPE,
    MSG_VENDOR_HTTP_ERROR,
    MSG_VENDOR_MISSING_CREDENTIAL,
    MSG_VENDOR_SETTING_CHOICE,
    MSG_VENDOR_WRITE_REFUSED,
    VENDOR_CONTENT_TYPE_HEADER,
    VENDOR_DROPPED_RESPONSE_HEADERS,
    VENDOR_ERROR_BODY_LIMIT,
    VENDOR_HTTP_TIMEOUT,
    VENDOR_MASK,
    VENDOR_READ_METHODS,
    VENDOR_REQUEST_HEADERS,
)
from web_router.exceptions import (
    ConfigurationError,
    ProviderError,
)
from web_router.exceptions_vendors import VendorWriteRefusedError
from web_router.vendors.catalog import VendorCatalog, get_catalog
from web_router.vendors.credentials import (
    CredentialSource,
    TokenCache,
    TokenRequest,
    credential_source,
    mask,
    mask_answer,
    token_request,
)
from web_router.vendors.plan import _Plan
from web_router.vendors.schema import ACCESS_TOKEN, Vendor, VendorInjection, placeholders
from web_router.vendors.types import VendorResponse


@dataclass
class _Prepared:
    """A request ready to send, and the secrets to mask out of anything it returns."""

    plan: _Plan
    url: str
    query: list[tuple[str, str]]
    headers: dict[str, str]
    auth: tuple[str, str] | None
    secrets: list[str]
    masked_query: set[str]
    body: Any = None


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

    @staticmethod
    def _needed(plan: _Plan) -> set[str]:
        """The credentials a call cannot be sent without: the endpoint's own list, else the vendor's required ones."""
        if plan.credentials is not None:
            return set(plan.credentials)
        return {name for name, spec in plan.vendor.auth.credentials.items() if spec.required}

    @staticmethod
    def _wants_token(plan: _Plan) -> bool:
        """Whether the call needs the vendor's OAuth token: always, unless its own credential list names none of
        the credentials the token is minted from (a key-only endpoint on an OAuth vendor)."""
        oauth = plan.vendor.auth.oauth2
        if oauth is None:
            return False
        if plan.credentials is None:
            return True
        minted_from = {name for template in oauth.templates() for name in placeholders(template)}
        return bool(set(plan.credentials) & minted_from)

    def _values(self, plan: _Plan) -> dict[str, str]:
        vendor = plan.vendor
        needed = self._needed(plan)
        values: dict[str, str] = {}
        for name, setting in vendor.settings.items():
            found = self.credentials.get(vendor.vendor, name, setting.env)
            value = found or setting.default
            if value is None:
                continue
            if setting.choices and value not in setting.choices:
                source = "/".join(setting.env) if found else "its default"
                raise ConfigurationError(
                    MSG_VENDOR_SETTING_CHOICE.format(
                        vendor=vendor.vendor, name=name, choices=", ".join(setting.choices), value=value, source=source
                    )
                )
            values[name] = value
        for name, cred in vendor.auth.credentials.items():
            value = self.credentials.get(vendor.vendor, name, cred.env)
            if value:
                values[name] = value
            elif name in needed:
                raise self._missing_error(vendor, name)
        return values

    def _missing_error(self, vendor: Vendor, name: str) -> ConfigurationError:
        spec = vendor.auth.credentials.get(name)
        setting = vendor.settings.get(name)
        env = spec.env if spec else (setting.env if setting else [])
        what = spec.description if spec and spec.description else name
        return ConfigurationError(
            MSG_VENDOR_MISSING_CREDENTIAL.format(
                vendor=vendor.vendor, what=what, variables=" or ".join(env) or name, name=name
            )
        )

    def _render(self, vendor: Vendor, template: str, values: Mapping[str, str]) -> str:
        for name in placeholders(template):
            if name not in values:
                raise self._missing_error(vendor, name)
        return template.format_map(values)

    def _check_write(self, plan: _Plan, *, allow_writes: bool) -> None:
        writes = plan.method not in VENDOR_READ_METHODS
        if plan.endpoint is not None:
            writes = self.catalog.endpoint(plan.endpoint).writes
        if writes and not allow_writes:
            target = plan.endpoint or f"{plan.method} {plan.path}"
            vendor = plan.vendor.vendor
            raise VendorWriteRefusedError(vendor, target, MSG_VENDOR_WRITE_REFUSED.format(target=target, vendor=vendor))

    def _applied(self, plan: _Plan, values: Mapping[str, str]) -> list[VendorInjection]:
        """The injections this call carries.

        Left off: one naming a credential the endpoint's own list excludes (even when configured), an optional
        one missing a value, and one missing only credentials this call does not need -- or the access token,
        when this call mints none.
        """
        vendor = plan.vendor
        needed = self._needed(plan)
        excluded = set(vendor.auth.credentials) - set(plan.credentials) if plan.credentials is not None else set()
        skippable = set(vendor.auth.credentials) | (set() if self._wants_token(plan) else {ACCESS_TOKEN})
        applied = []
        for injection in vendor.auth.inject:
            names = placeholders(injection.value)
            if names & excluded:
                continue
            absent = {name for name in names if name not in values}
            if absent and (injection.optional or not (absent & needed or absent - skippable)):
                continue
            applied.append(injection)
        return applied

    def _prepare(self, plan: _Plan, values: dict[str, str]) -> _Prepared:
        vendor = plan.vendor
        base = self._render(vendor, vendor.base_for(plan.host), values).rstrip("/")
        url = base + plan.path
        expected, actual = httpx.URL(base), httpx.URL(url)
        if actual.scheme != "https" or actual.host != expected.host or actual.port != expected.port:
            raise ConfigurationError(
                MSG_VENDOR_HOST_ESCAPE.format(vendor=vendor.vendor, host=actual.host, expected=expected.host)
            )
        headers = dict(VENDOR_REQUEST_HEADERS)
        headers.update({name: self._render(vendor, value, values) for name, value in vendor.headers.items()})
        headers.update(plan.headers)
        query = list(plan.query)
        body = plan.body
        masked: set[str] = set()
        for injection in self._applied(plan, values):
            rendered = self._render(vendor, injection.value, values)
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
                self._render(vendor, vendor.auth.basic.username, values),
                self._render(vendor, vendor.auth.basic.password, values),
            )
        secret_names = set(vendor.auth.credentials) | {ACCESS_TOKEN}
        secrets = [value for name, value in values.items() if name in secret_names]
        return _Prepared(plan, url, query, headers, auth, secrets, masked, body)

    def _token_request(self, plan: _Plan, values: dict[str, str]) -> TokenRequest | None:
        vendor = plan.vendor
        oauth = vendor.auth.oauth2
        if oauth is None or not self._wants_token(plan):
            return None
        for template in oauth.templates():
            self._render(vendor, template, values)
        return token_request(vendor.vendor, oauth, values)

    # -- sending -----------------------------------------------------------

    def _client(self) -> httpx.Client:
        if self._sync is None:
            self._sync = httpx.Client(timeout=self._timeout, follow_redirects=False, transport=self._transport)
        return self._sync

    def _aclient(self) -> httpx.AsyncClient:
        if self._async is None:
            self._async = httpx.AsyncClient(
                timeout=self._timeout, follow_redirects=False, transport=self._async_transport
            )
        return self._async

    def _send_kwargs(self, prepared: _Prepared) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"params": prepared.query, "headers": prepared.headers}
        if prepared.auth is not None:
            kwargs["auth"] = prepared.auth
        body = prepared.body
        if body is not None:
            if prepared.plan.body_type == "form":
                kwargs["data"] = body
            else:
                kwargs["json"] = body
        return kwargs

    def _response(self, prepared: _Prepared, response: httpx.Response) -> VendorResponse:
        plan = prepared.plan
        shown = httpx.URL(prepared.url).copy_with(
            params=[(k, VENDOR_MASK if k in prepared.masked_query else v) for k, v in prepared.query]
        )
        if response.status_code >= 400:
            body = mask(response.text, prepared.secrets)[:VENDOR_ERROR_BODY_LIMIT]
            detail = MSG_VENDOR_HTTP_ERROR.format(
                status=response.status_code, method=plan.method, path=plan.path, body=body or "(empty body)"
            )
            raise ProviderError(plan.vendor.vendor, detail)
        data: Any = None
        text: str | None = None
        if response.content:
            try:
                data = mask_answer(response.json(), prepared.secrets)
            except ValueError:
                text = mask_answer(response.text, prepared.secrets)
        headers = {
            key: mask_answer(value, prepared.secrets)
            for key, value in response.headers.items()
            if key.lower() not in VENDOR_DROPPED_RESPONSE_HEADERS
        }
        # From the masked headers, so a secret the vendor echoed into the content type stays masked there too.
        content_type = next(
            (value for key, value in headers.items() if key.lower() == VENDOR_CONTENT_TYPE_HEADER), None
        )
        return VendorResponse(
            vendor=plan.vendor.vendor,
            endpoint=plan.endpoint,
            method=plan.method,
            url=str(shown),
            status_code=response.status_code,
            data=data,
            text=text,
            content_type=content_type,
            headers=headers,
        )

    def _transport_error(self, prepared: _Prepared, exc: httpx.HTTPError) -> ProviderError:
        message = mask(f"{type(exc).__name__}: {exc}", prepared.secrets)
        return ProviderError(prepared.plan.vendor.vendor, message)

    def _begin(self, plan: _Plan, *, allow_writes: bool) -> tuple[dict[str, str], TokenRequest | None]:
        """Everything before the network, shared by both paths: the write check, the values, the token to mint."""
        # Checked before anything else: a refused write should cost nothing, not even a token.
        self._check_write(plan, allow_writes=allow_writes)
        values = self._values(plan)
        return values, self._token_request(plan, values)

    def send(self, plan: _Plan, *, allow_writes: bool = False) -> VendorResponse:
        values, token = self._begin(plan, allow_writes=allow_writes)
        if token is not None:
            values[ACCESS_TOKEN] = self._tokens.get(token, self._client())
        prepared = self._prepare(plan, values)
        try:
            response = self._client().request(plan.method, prepared.url, **self._send_kwargs(prepared))
        except httpx.HTTPError as exc:
            raise self._transport_error(prepared, exc) from None
        return self._response(prepared, response)

    async def asend(self, plan: _Plan, *, allow_writes: bool = False) -> VendorResponse:
        values, token = self._begin(plan, allow_writes=allow_writes)
        if token is not None:
            values[ACCESS_TOKEN] = await self._tokens.aget(token, self._aclient())
        prepared = self._prepare(plan, values)
        try:
            response = await self._aclient().request(plan.method, prepared.url, **self._send_kwargs(prepared))
        except httpx.HTTPError as exc:
            raise self._transport_error(prepared, exc) from None
        return self._response(prepared, response)

    def close(self) -> None:
        if self._sync is not None:
            self._sync.close()
            self._sync = None

    async def aclose(self) -> None:
        if self._async is not None:
            await self._async.aclose()
            self._async = None
        self.close()
