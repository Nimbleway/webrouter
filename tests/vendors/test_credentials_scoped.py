"""Vendor-wide headers and HTTP Basic follow the endpoint's own credential list, as injections do."""

from __future__ import annotations

import copy
from contextlib import closing
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from tests.vendors.conftest import Recorder
from tests.vendors.data import ACME, CREDENTIALS, _endpoint
from web_router.vendors.caller import VendorCaller
from web_router.vendors.catalog import VendorCatalog, builtin_catalog
from web_router.vendors.plan import plan_endpoint
from web_router.vendors.schema import Vendor

_KEY_ONLY = _endpoint("acme.auth.validate", "GET", "/v1/validate", credentials=["api_key"])
_EVERY_KEY = _endpoint("acme.status.get", "GET", "/v1/status")


def _send(vendor: dict[str, Any], endpoint: str) -> httpx.Request:
    catalog = VendorCatalog([Vendor.model_validate(vendor)])
    recorder = Recorder()
    with closing(VendorCaller(CREDENTIALS, catalog=catalog, transport=httpx.MockTransport(recorder))) as caller:
        found = catalog.endpoint(endpoint)
        caller.send(plan_endpoint(catalog.vendor("acme"), found, {}))
    return recorder.last


def _acme(**changes: Any) -> dict[str, Any]:
    vendor = copy.deepcopy(ACME)
    vendor.update(changes)
    vendor["endpoints"] = [_KEY_ONLY, _EVERY_KEY]
    return vendor


def test_a_vendor_header_naming_an_excluded_credential_is_left_off() -> None:
    vendor = _acme(headers={"Acme-Version": "2026-01", "X-App-Echo": "{app_key}"})
    sent = _send(vendor, "acme.auth.validate")
    assert "x-app-echo" not in sent.headers
    assert sent.headers["acme-version"] == "2026-01"
    assert _send(vendor, "acme.status.get").headers["x-app-echo"] == "acme-app-secret"


def test_basic_auth_naming_an_excluded_credential_is_not_sent() -> None:
    vendor = _acme()
    vendor["auth"] = {**vendor["auth"], "basic": {"username": "{api_key}", "password": "{app_key}"}, "inject": []}
    assert "authorization" not in _send(vendor, "acme.auth.validate").headers
    assert _send(vendor, "acme.status.get").headers["authorization"].startswith("Basic ")


def _coingecko(host: str | None) -> httpx.Request:
    catalog = builtin_catalog()
    values = {"demo_api_key": "demo-key-123", "pro_api_key": "pro-key-456", **({"host": host} if host else {})}
    recorder = Recorder()
    caller = VendorCaller({"coingecko": values}, catalog=catalog, transport=httpx.MockTransport(recorder))
    with closing(caller):
        found = catalog.endpoint("coingecko.ping")
        caller.send(plan_endpoint(catalog.vendor("coingecko"), found, {}))
    return recorder.last


def test_each_coingecko_key_goes_only_to_its_own_host() -> None:
    demo = _coingecko(None)
    assert demo.url.host == "api.coingecko.com"
    assert demo.headers["x-cg-demo-api-key"] == "demo-key-123" and "x-cg-pro-api-key" not in demo.headers
    pro = _coingecko("pro-api.coingecko.com")
    assert pro.url.host == "pro-api.coingecko.com"
    assert pro.headers["x-cg-pro-api-key"] == "pro-key-456" and "x-cg-demo-api-key" not in pro.headers


def test_a_when_must_name_a_setting_choice() -> None:
    vendor = _acme()
    vendor["auth"]["inject"][0]["when"] = {"region": "mars"}
    with pytest.raises(ValidationError, match="names no choice"):
        Vendor.model_validate(vendor)
