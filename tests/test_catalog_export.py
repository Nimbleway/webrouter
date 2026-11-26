"""The catalog export: every vendor once, with the public fields a catalog page shows."""

from __future__ import annotations

from scripts.catalog_export import export
from web_router.vendors.catalog import builtin_catalog


def test_export_lists_every_vendor_once_with_public_fields():
    data = export(builtin_catalog())
    slugs = [vendor["slug"] for vendor in data["vendors"]]
    assert len(slugs) == len(set(slugs)) == data["vendor_count"]
    assert sum(category["vendor_count"] for category in data["categories"]) == data["vendor_count"]
    fmp = next(vendor for vendor in data["vendors"] if vendor["slug"] == "fmp")
    assert fmp["env_vars"] == "FMP_API_KEY"
    assert 'call_data_api("fmp.' in fmp["code_sample"]


def test_record_hash_changes_with_its_fields():
    first = export(builtin_catalog())["vendors"][0]
    again = export(builtin_catalog())["vendors"][0]
    assert first["hash"] == again["hash"]
    assert first["hash"] != export(builtin_catalog())["vendors"][1]["hash"]
