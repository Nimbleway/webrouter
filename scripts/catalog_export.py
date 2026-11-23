"""Export the data API catalog as one JSON file for the public Web Catalog site.

Reads the same vendor files as ``make vendors`` and writes, per category and per
vendor, the fields a catalog page shows. Only public catalog data goes in: what
each vendor's API holds, its endpoints, how its key is sent and its docs links.

    uv run python -m scripts.catalog_export                  # writes build/catalog.json
    uv run python -m scripts.catalog_export --out path.json
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import sys
from pathlib import Path
from typing import Any

from scripts.vendors import auth_label, credential_vars
from web_router.constants_vendors import VENDOR_CATEGORIES, VENDOR_CATEGORY_DESCRIPTIONS
from web_router.vendors.catalog import VendorCatalog, builtin_catalog
from web_router.vendors.schema import Vendor, VendorEndpoint

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "build" / "catalog.json"

# Category slugs read better as words on a public page than as catalog ids.
CATEGORY_TITLES = {
    "ai": "AI",
    "seo": "SEO",
    "company-data": "Company data",
}


def category_title(category: str) -> str:
    return CATEGORY_TITLES.get(category, category.replace("-", " ").capitalize())


def _fingerprint(fields: dict[str, Any]) -> str:
    """A short hash of the synced fields, so an unchanged item is never rewritten."""
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()[:16]


def _endpoint_html(endpoint: VendorEndpoint) -> str:
    params = ", ".join(name + ("" if param.required else " (optional)") for name, param in endpoint.params.items())
    line = f"<strong>{html.escape(endpoint.name)}</strong> · {endpoint.method} {html.escape(endpoint.path)}"
    parts = [f"<p>{line}</p>"]
    if endpoint.summary:
        parts.append(f"<p>{html.escape(endpoint.summary)}</p>")
    if params:
        parts.append(f"<p>Inputs: {html.escape(params)}</p>")
    return "<li>" + "".join(parts) + "</li>"


def endpoints_html(vendor: Vendor) -> str:
    return "<ul>" + "".join(_endpoint_html(endpoint) for endpoint in vendor.endpoints) + "</ul>"


def code_sample(vendor: Vendor) -> str:
    """A Python call of the vendor's first endpoint, with each required input's documented example."""
    if not vendor.endpoints:
        return ""
    endpoint = vendor.endpoints[0]
    args = {
        name: param.example if param.example is not None else f"<{name}>"
        for name, param in endpoint.params.items()
        if param.required
    }
    body = json.dumps(args, ensure_ascii=False)
    return (
        "from web_router import WebRouter\n\n"
        "router = WebRouter()\n"
        f'response = router.call_data_api("{endpoint.id}", {body})\n'
        "response.data"
    )


def verification(vendor: Vendor) -> tuple[str, str]:
    """('live' or 'docs', ISO date): live when any endpoint was matched by a real call."""
    live = [endpoint.verified.live for endpoint in vendor.endpoints if endpoint.verified.live]
    return ("live", max(live)) if live else ("docs", vendor.curated)


def vendor_record(vendor: Vendor) -> dict[str, Any]:
    status, checked = verification(vendor)
    fields = {
        "name": vendor.name,
        "slug": vendor.vendor,
        "category": vendor.category,
        "summary": vendor.summary,
        "vendor_id": vendor.vendor,
        "docs_url": vendor.docs_url,
        "pricing_url": vendor.pricing_url,
        "auth": auth_label(vendor),
        "env_vars": ", ".join(credential_vars(vendor)),
        "endpoint_count": len(vendor.endpoints),
        "endpoints_html": endpoints_html(vendor),
        "code_sample": code_sample(vendor),
        "sites": ", ".join(vendor.sites),
        "verification": status,
        "checked": checked,
    }
    return {**fields, "hash": _fingerprint(fields)}


def category_record(category: str, vendors: list[Vendor]) -> dict[str, Any]:
    fields = {
        "name": category_title(category),
        "slug": category,
        "description": VENDOR_CATEGORY_DESCRIPTIONS[category],
        "vendor_count": len(vendors),
        "endpoint_count": sum(len(vendor.endpoints) for vendor in vendors),
    }
    return {**fields, "hash": _fingerprint(fields)}


def export(catalog: VendorCatalog) -> dict[str, Any]:
    vendors = sorted(catalog.all(), key=lambda vendor: vendor.vendor)
    by_category = {category: [v for v in vendors if v.category == category] for category in VENDOR_CATEGORIES}
    return {
        "vendor_count": len(vendors),
        "endpoint_count": sum(len(vendor.endpoints) for vendor in vendors),
        "categories": [category_record(c, vs) for c, vs in by_category.items() if vs],
        "vendors": [vendor_record(vendor) for vendor in vendors],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    data = export(builtin_catalog())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    print(f"{args.out}: {len(data['categories'])} categories, {data['vendor_count']} vendors", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
