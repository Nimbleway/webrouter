"""The Web Catalog export and its Webflow sync plan: drafts only, matched by slug, never publishing."""

from __future__ import annotations

from pathlib import Path

from scripts import webflow_sync
from scripts.catalog_export import export
from scripts.webflow_sync import category_seo, plan
from web_router.vendors.catalog import builtin_catalog


def _fields(record):
    return {"name": record["name"], "slug": record["slug"], "sync-hash": record["hash"]}


def _seo(record):
    return {"title-tag": record["name"]}


def _item(slug, sync_hash, *, archived=False):
    return {"id": f"id-{slug}", "isArchived": archived, "fieldData": {"slug": slug, "sync-hash": sync_hash}}


def test_export_lists_every_vendor_once_with_public_fields():
    data = export(builtin_catalog())
    slugs = [vendor["slug"] for vendor in data["vendors"]]
    assert len(slugs) == len(set(slugs)) == data["vendor_count"]
    assert sum(category["vendor_count"] for category in data["categories"]) == data["vendor_count"]
    fmp = next(vendor for vendor in data["vendors"] if vendor["slug"] == "fmp")
    assert fmp["env_vars"] == "FMP_API_KEY"
    assert 'call_data_api("fmp.' in fmp["code_sample"]


def test_plan_creates_missing_updates_changed_and_archives_removed():
    records = [
        {"name": "New", "slug": "new", "hash": "h1"},
        {"name": "Same", "slug": "same", "hash": "h2"},
        {"name": "Changed", "slug": "changed", "hash": "h3"},
    ]
    existing = [_item("same", "h2"), _item("changed", "old"), _item("gone", "h4")]
    create, update, archive = plan(records, existing, _fields, _seo)
    assert [item["slug"] for item in create] == ["new"]
    assert create[0]["title-tag"] == "New"  # SEO fields only on create
    assert [item["id"] for item in update] == ["id-changed"]
    assert "title-tag" not in update[0]["fieldData"]
    assert "isDraft" not in update[0]  # an update keeps the item's publish status
    assert [item["id"] for item in archive] == ["id-gone"]


def test_plan_restores_an_archived_item_the_catalog_lists_again():
    _, update, archive = plan(
        [{"name": "Back", "slug": "back", "hash": "h"}], [_item("back", "h", archived=True)], _fields, _seo
    )
    assert update[0]["isArchived"] is False
    assert archive == []


def test_category_seo_does_not_double_data():
    assert category_seo({"name": "Company data", "vendor_count": 2, "description": "x"})["title-tag"].startswith(
        "Company data APIs"
    )


def test_sync_never_calls_a_publish_endpoint():
    source = Path(webflow_sync.__file__).read_text()
    assert "/publish" not in source
    assert '"isDraft": True' in source
