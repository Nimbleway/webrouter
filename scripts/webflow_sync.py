"""Sync the exported data API catalog into the Web Catalog's Webflow CMS collections, as drafts.

Reads ``build/catalog.json`` (``scripts.catalog_export``) and, for the Categories
and Vendors collections, creates the items that are missing, updates the ones
whose catalog fields changed and archives the ones the catalog no longer lists.

It never publishes. New items are created as drafts, updates leave an item's
publish status alone, and nothing here calls a Webflow publish endpoint:
going live stays a manual step in Webflow, after marketing approves.

Fields marketing owns (logo, featured, SEO title and description once set) are
written only when an item is created, never overwritten.

    WEBFLOW_TOKEN=... WEBFLOW_CATEGORIES_COLLECTION=... WEBFLOW_VENDORS_COLLECTION=... \\
        uv run python -m scripts.webflow_sync [--dry-run] [--category finance] [--limit 10]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
API = "https://api.webflow.com/v2"
BATCH = 100
Record = dict[str, Any]
FieldsFn = Callable[[Record], Record]
VERIFICATION_OPTIONS = {"docs": "Checked from docs", "live": "Verified live"}


class Webflow:
    def __init__(self, token: str) -> None:
        self.token = token

    def request(self, method: str, path: str, body: Any = None) -> Any:
        data = None if body is None else json.dumps(body).encode()
        for attempt in range(6):
            req = urllib.request.Request(API + path, data=data, method=method)
            req.add_header("Authorization", f"Bearer {self.token}")
            req.add_header("accept", "application/json")
            if data is not None:
                req.add_header("content-type", "application/json")
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    raw = resp.read()
                    return json.loads(raw) if raw else None
            except urllib.error.HTTPError as err:
                if err.code == 429 and attempt < 5:
                    time.sleep(float(err.headers.get("Retry-After") or 30))
                    continue
                raise RuntimeError(f"{method} {path}: {err.code} {err.read().decode()[:500]}") from err
        raise RuntimeError(f"{method} {path}: rate limited")

    def collection(self, collection_id: str) -> dict[str, Any]:
        schema: dict[str, Any] = self.request("GET", f"/collections/{collection_id}")
        return schema

    def items(self, collection_id: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        offset = 0
        while True:
            page = self.request("GET", f"/collections/{collection_id}/items?limit={BATCH}&offset={offset}")
            out += page["items"]
            offset += BATCH
            if offset >= page["pagination"]["total"]:
                return out

    def create(self, collection_id: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        created: list[dict[str, Any]] = []
        for start in range(0, len(items), BATCH):
            chunk = [{"fieldData": fields, "isDraft": True} for fields in items[start : start + BATCH]]
            result = self.request("POST", f"/collections/{collection_id}/items", {"items": chunk})
            created += result.get("items", [result]) if isinstance(result, dict) else []
        return created

    def update(self, collection_id: str, items: list[dict[str, Any]]) -> None:
        for start in range(0, len(items), BATCH):
            self.request("PATCH", f"/collections/{collection_id}/items", {"items": items[start : start + BATCH]})


def category_fields(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": record["name"],
        "slug": record["slug"],
        "txt-description": record["description"],
        "num-vendor-count": record["vendor_count"],
        "num-endpoint-count": record["endpoint_count"],
        "sync-hash": record["hash"],
    }


def _apis(name: str) -> str:
    """'Finance data APIs', 'Company data APIs', 'SEO data APIs'."""
    return name + (" APIs" if name.lower().endswith("data") else " data APIs")


def category_seo(record: dict[str, Any]) -> dict[str, Any]:
    noun = record["name"] if record["name"].isupper() else record["name"].lower()
    return {
        "title-tag": f"{_apis(record['name'])} for AI agents | Nimble",
        "meta-description": f"{record['vendor_count']} {_apis(noun)} your agent can call "
        f"through one interface: {record['description']}",
    }


def vendor_fields(record: dict[str, Any], category_ids: dict[str, str], option_ids: dict[str, str]) -> dict[str, Any]:
    return {
        "name": record["name"],
        "slug": record["slug"],
        "txt-summary": record["summary"],
        "ref-category": category_ids.get(record["category"]),
        "txt-vendor-id": record["vendor_id"],
        "lnk-docs": record["docs_url"],
        "lnk-pricing": record["pricing_url"],
        "txt-auth": record["auth"],
        "txt-env-vars": record["env_vars"],
        "num-endpoint-count": record["endpoint_count"],
        "rch-endpoints": record["endpoints_html"],
        "txt-code-sample": record["code_sample"],
        "txt-sites": record["sites"],
        "opt-verification": option_ids.get(VERIFICATION_OPTIONS[record["verification"]]),
        "dt-checked": f"{record['checked']}T00:00:00.000Z",
        "sync-hash": record["hash"],
    }


def vendor_seo(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "title-tag": f"{record['name']} API for AI agents | Nimble",
        "meta-description": record["summary"][:300],
    }


def plan(
    records: list[Record], existing: list[Record], fields: FieldsFn, seo: FieldsFn
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """(to create, to update, to archive) for one collection, matched by slug."""
    by_slug = {item["fieldData"]["slug"]: item for item in existing}
    wanted = {record["slug"] for record in records}
    create, update = [], []
    for record in records:
        item = by_slug.get(record["slug"])
        if item is None:
            create.append({**fields(record), **seo(record)})
        elif item["fieldData"].get("sync-hash") != record["hash"] or item.get("isArchived"):
            update.append({"id": item["id"], "isArchived": False, "fieldData": fields(record)})
    archive = [
        {"id": item["id"], "isArchived": True, "fieldData": {}}
        for slug, item in by_slug.items()
        if slug not in wanted and not item.get("isArchived")
    ]
    return create, update, archive


def sync_collection(
    api: Webflow | None,
    collection_id: str,
    records: list[Record],
    existing: list[Record],
    fields: FieldsFn,
    seo: FieldsFn,
    label: str,
) -> list[Record]:
    create, update, archive = plan(records, existing, fields, seo)
    print(f"{label}: {len(create)} to create, {len(update)} to update, {len(archive)} to archive", file=sys.stderr)
    if api is None:
        print(json.dumps({"collection": label, "create": create, "update": update, "archive": archive}, indent=1))
        return []
    created = api.create(collection_id, create) if create else []
    if update or archive:
        api.update(collection_id, update + archive)
    return created


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--catalog", type=Path, default=ROOT / "build" / "catalog.json")
    parser.add_argument("--dry-run", action="store_true", help="print the planned writes instead of sending them")
    parser.add_argument("--category", help="sync only this category's vendors (all categories are still synced)")
    parser.add_argument("--limit", type=int, help="sync at most this many vendors")
    args = parser.parse_args(argv)

    catalog = json.loads(args.catalog.read_text())
    vendors = [v for v in catalog["vendors"] if not args.category or v["category"] == args.category]
    vendors = vendors[: args.limit] if args.limit else vendors

    token = os.environ.get("WEBFLOW_TOKEN", "")
    categories_id = os.environ.get("WEBFLOW_CATEGORIES_COLLECTION", "")
    vendors_id = os.environ.get("WEBFLOW_VENDORS_COLLECTION", "")
    if not (token and categories_id and vendors_id):
        print("Set WEBFLOW_TOKEN, WEBFLOW_CATEGORIES_COLLECTION and WEBFLOW_VENDORS_COLLECTION.", file=sys.stderr)
        return 2
    api = Webflow(token)
    writer = None if args.dry_run else api

    existing_categories = api.items(categories_id)
    sync_collection(
        writer, categories_id, catalog["categories"], existing_categories, category_fields, category_seo, "categories"
    )
    category_ids = {item["fieldData"]["slug"]: item["id"] for item in api.items(categories_id)}
    schema = api.collection(vendors_id)
    option_ids = {
        option["name"]: option["id"]
        for field in schema["fields"]
        if field["slug"] == "opt-verification"
        for option in field["validations"]["options"]
    }
    existing_vendors = api.items(vendors_id)
    if args.category or args.limit:
        # A partial run must not archive the vendors it was told to leave out.
        keep = {v["slug"] for v in vendors}
        existing_vendors = [item for item in existing_vendors if item["fieldData"]["slug"] in keep]
    sync_collection(
        writer,
        vendors_id,
        vendors,
        existing_vendors,
        lambda record: vendor_fields(record, category_ids, option_ids),
        vendor_seo,
        "vendors",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
