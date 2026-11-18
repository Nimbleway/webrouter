"""Planning one vendor call: each input placed where the endpoint says, before any credential is read.

Apart from :mod:`web_router.vendors.caller` so that refusing an undeclared or
missing input needs no HTTP client: a plan is built, and checked, without the
``vendors`` extra installed.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

from web_router.constants_vendors import MSG_VENDOR_BAD_PATH, MSG_VENDOR_CALLBACK_REFUSED, VENDOR_CALLBACK_KEY
from web_router.exceptions import ConfigurationError, MissingParameterError, UnsupportedParameterError
from web_router.vendors.catalog import get_catalog
from web_router.vendors.schema import Vendor, VendorEndpoint, VendorParam


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


def _callback_keys(value: Any, path: str = "") -> list[str]:
    """Where an object input names a webhook or callback URL, at any depth: the catalog leaves those out."""
    found: list[str] = []
    if isinstance(value, dict):
        for key, inner in value.items():
            where = f"{path}.{key}" if path else str(key)
            if VENDOR_CALLBACK_KEY.search(str(key)):
                found.append(where)
            found += _callback_keys(inner, where)
    elif isinstance(value, list):
        for inner in value:
            found += _callback_keys(inner, path)
    return found


def _off_constant(endpoint: VendorEndpoint, params: Mapping[str, Any]) -> dict[str, tuple[Any, Any]]:
    """Each input given another value than the only one its ``enum`` allows (a constant such as Majestic's
    ``cmd``), as (given, allowed). Compared as sent, so a JSON ``true`` matches a constant ``"true"``."""
    off: dict[str, tuple[Any, Any]] = {}
    for name, spec in endpoint.params.items():
        given = params.get(name)
        if spec.enum and len(spec.enum) == 1 and given is not None and _text(given) != _text(spec.enum[0]):
            off[name] = (given, spec.enum[0])
    return off


def _refuse_bad_inputs(vendor: Vendor, endpoint: VendorEndpoint, params: Mapping[str, Any]) -> None:
    """Undeclared, missing, callback-carrying and off-constant inputs, each refused before anything is sent."""
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
    callbacks = [where for name, value in params.items() for where in _callback_keys(value, name)]
    if callbacks:
        raise UnsupportedParameterError(vendor.vendor, callbacks, detail=MSG_VENDOR_CALLBACK_REFUSED)
    off = _off_constant(endpoint, params)
    if off:
        fixed = ", ".join(f"{name}={allowed!r}" for name, (_, allowed) in sorted(off.items()))
        given = {name: value for name, (value, _) in off.items()}
        raise UnsupportedParameterError(vendor.vendor, off, values=given, detail=f"{endpoint.id} takes only {fixed}.")


def plan_endpoint(vendor: Vendor, endpoint: VendorEndpoint, params: Mapping[str, Any]) -> _Plan:
    """Place each input where the endpoint declares it, refusing undeclared and missing ones."""
    _refuse_bad_inputs(vendor, endpoint, params)
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
        elif spec.location == "query" and spec.style == "json":
            plan.query.append((wire, _text(value)))
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


def endpoint_plan(endpoint: str, params: Mapping[str, Any] | None) -> _Plan:
    """The request ``call_vendor`` sends, built once for both routers: ``endpoint`` looked up in the catalog."""
    found = get_catalog().endpoint(endpoint)
    return plan_endpoint(get_catalog().vendor(found.vendor), found, dict(params or {}))


def raw_plan(
    vendor: str,
    method: str,
    path: str,
    *,
    query: Mapping[str, Any] | Sequence[tuple[str, Any]] | None,
    body: Any,
    headers: Mapping[str, str] | None,
    host: str | None,
) -> _Plan:
    """The request ``vendor_request`` sends, built once for both routers: ``vendor`` looked up in the catalog."""
    return plan_raw(get_catalog().vendor(vendor), method, path, query=query, body=body, headers=headers, host=host)
