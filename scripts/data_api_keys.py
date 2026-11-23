"""The key badges on the generated Data APIs pages: whether a vendor, and each of its endpoints, needs a key.

A vendor's key can be optional while some of its endpoints cannot be called without it, so a vendor says "Key for
some calls" and those endpoints name the variable they need.
"""

from __future__ import annotations

from scripts.data_api_mdx import code, literal
from scripts.vendors import endpoint_keys, needs_key, some_calls_need_key
from web_router.vendors.schema import Vendor, VendorEndpoint


def key_badge(vendor: Vendor) -> str:
    """A vendor page's key badge: none needed, optional, needed by some calls, or needed (naming the variables)."""
    if not vendor.auth.credentials:
        return f'<span className="wc-badge wc-badge-free">{literal("No key needed")}</span>'
    if some_calls_need_key(vendor):
        return f'<span className="wc-badge">{literal("Key for some calls")}</span>'
    if not needs_key(vendor):
        return f'<span className="wc-badge">{literal("Key optional")}</span>'
    required = [spec.env[0] for spec in vendor.auth.credentials.values() if spec.required]
    names = literal(", ").join(code(name) for name in required)
    return f'<span className="wc-badge wc-badge-key">{literal("Needs ")}{names}</span>'


def endpoint_key_badge(vendor: Vendor, endpoint: VendorEndpoint) -> str:
    """A badge on an endpoint that needs a key its vendor otherwise leaves optional."""
    keys = [] if needs_key(vendor) else endpoint_keys(vendor, endpoint)
    names = literal(", ").join(code(name) for name in keys)
    return f'<span className="wc-badge wc-badge-key">{literal("Needs ")}{names}</span>' if keys else ""
