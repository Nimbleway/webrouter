from __future__ import annotations

from scripts.data_api_keys import endpoint_key_badge, key_badge
from scripts.vendors import endpoint_keys, some_calls_need_key
from web_router.vendors.catalog import builtin_catalog


def test_an_endpoint_that_needs_an_optional_key_names_it_and_the_vendor_says_some_calls_do() -> None:
    catalog = builtin_catalog()
    vendor = catalog.vendor("context7")
    usage = next(endpoint for endpoint in vendor.endpoints if endpoint.id == "context7.usage.get")
    others = [endpoint for endpoint in vendor.endpoints if endpoint.id != "context7.usage.get"]

    assert some_calls_need_key(vendor)
    assert "Key for some calls" in key_badge(vendor)
    assert endpoint_keys(vendor, usage) == ["CONTEXT7_API_KEY"]
    assert "CONTEXT7_API_KEY" in endpoint_key_badge(vendor, usage)
    assert all(endpoint_key_badge(vendor, endpoint) == "" for endpoint in others)


def test_a_vendor_that_needs_a_key_everywhere_badges_the_vendor_not_each_endpoint() -> None:
    vendor = builtin_catalog().vendor("hunter")
    assert not some_calls_need_key(vendor)
    assert "Needs " in key_badge(vendor)
    assert all(endpoint_key_badge(vendor, endpoint) == "" for endpoint in vendor.endpoints)


def test_a_keyless_vendor_has_no_key_badges() -> None:
    vendor = builtin_catalog().vendor("rcc-acis")
    assert "No key needed" in key_badge(vendor) or "Key optional" in key_badge(vendor)
    assert all(endpoint_key_badge(vendor, endpoint) == "" for endpoint in vendor.endpoints)
