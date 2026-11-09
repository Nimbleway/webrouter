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

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import httpx

from web_router.constants_vendors import (
    MSG_VENDOR_BAD_PATH,
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
    MissingParameterError,
    ProviderError,
    UnsupportedParameterError,
    VendorWriteRefusedError,
)
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
from web_router.vendors.schema import ACCESS_TOKEN, Vendor, VendorEndpoint, VendorParam, placeholders
from web_router.vendors.types import VendorResponse


@dataclass
class _Plan:
    """A request before authentication: what the caller asked to send."""

    vendor: Vendor
    endpoint: str | None
    method: str
    path: str
    query: list[tuple[str, str]] = field(default_factory=list)
    body: Any = None
    body_type: str = "json"
    headers: dict[str, str] = field(default_factory=dict)
    host: str | None = None
    credentials: list[str] | None = None
    """The credentials this call needs; ``None`` for every one the vendor requires."""


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


def _text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, separators=(",", ":"))
    return str(value)


def _query_pairs(name: str, value: Any) -> list[tuple[str, str]]:
    if isinstance(value, (list, tuple)):
        return [(name, _text(item)) for item in value]
    return [(name, _text(value))]


def plan_endpoint(vendor: Vendor, endpoint: VendorEndpoint, params: Mapping[str, Any]) -> _Plan:
    """Place each input where the endpoint declares it, refusing undeclared and missing ones."""
    unknown = set(params) - set(endpoint.params)
    if unknown:
        detail = f"{endpoint.id} takes: {', '.join(sorted(endpoint.params)) or 'no parameters'}."
        raise UnsupportedParameterError(vendor.vendor, unknown, detail=detail)
    # A path placeholder cannot be left out, whatever the entry says: the URL has no shape without it.
    missing = {
        name
        for name, spec in endpoint.params.items()
        if (spec.required or spec.location == "path") and params.get(name) is None
    }
    if missing:
        raise MissingParameterError(vendor.vendor, missing, detail=f"{endpoint.id} requires them.")
    plan = _Plan(vendor=vendor, endpoint=endpoint.id, method=endpoint.method, path=endpoint.path)
    plan.body_type = endpoint.body_type
    plan.host = endpoint.host
    plan.credentials = endpoint.credentials
    path_values: dict[str, str] = {}
    body: dict[str, Any] = {}
    for name, value in params.items():
        if value is None:
            continue
        spec: VendorParam = endpoint.params[name]
        wire = spec.wire_name or name
        if spec.location == "path":
            path_values[wire] = quote(_text(value), safe="")
        elif spec.location == "query":
            plan.query += _query_pairs(wire, value)
        elif spec.location == "header":
            plan.headers[wire] = _text(value)
        else:
            body[wire] = value
    plan.path = endpoint.path.format_map(path_values)
    if body:
        plan.body = [body] if endpoint.body_array else body
    return plan


def plan_raw(
    vendor: Vendor,
    method: str,
    path: str,
    *,
    query: Mapping[str, Any] | Sequence[tuple[str, Any]] | None,
    body: Any,
    headers: Mapping[str, str] | None,
    host: str | None = None,
) -> _Plan:
    """A request to any path on the vendor's API, for the endpoints the catalog does not name."""
    if not path.startswith("/") or path.startswith("//") or "://" in path or "\\" in path:
        raise ConfigurationError(MSG_VENDOR_BAD_PATH.format(path=path, vendor=vendor.vendor, base_url=vendor.base_url))
    if host is not None and host not in vendor.hosts:
        known = ", ".join(sorted(vendor.hosts)) or "none"
        raise ConfigurationError(f"{vendor.vendor} has no host named {host!r}; its other hosts: {known}")
    plan = _Plan(vendor=vendor, endpoint=None, method=method.upper(), path=path, host=host)
    items = query.items() if isinstance(query, Mapping) else (query or ())
    for name, value in items:
        if value is not None:
            plan.query += _query_pairs(name, value)
    plan.body = body
    plan.headers = dict(headers or {})
    return plan


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
        needed = self._needed(plan)
        for injection in vendor.auth.inject:
            absent = {name for name in placeholders(injection.value) if name not in values}
            # An optional injection, or one carrying only credentials this call does not need, is left off.
            if absent and (injection.optional or not (absent & needed or absent - set(vendor.auth.credentials))):
                continue
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

    def _token_request(self, vendor: Vendor, values: dict[str, str]) -> TokenRequest | None:
        oauth = vendor.auth.oauth2
        if oauth is None:
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
        content_type = response.headers.get(VENDOR_CONTENT_TYPE_HEADER)
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
        return values, self._token_request(plan.vendor, values)

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
