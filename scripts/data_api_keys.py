"""The key badges on the generated Data APIs pages: whether a vendor, and each of its endpoints, needs a key.

A vendor's key can be optional while some of its endpoints cannot be called without it, so a vendor says "Key for
some calls" and those endpoints name the variable they need.
"""

from __future__ import annotations

from scripts.data_api_mdx import code, literal
from scripts.vendors import endpoint_keys, needs_key, some_calls_need_key
from web_router.vendors.schema import Vendor, VendorEndpoint


def _required(vendor: Vendor) -> list[str]:
    """The variables the vendor's badge names, once each: several credentials can read one variable."""
    return list(dict.fromkeys(spec.env[0] for spec in vendor.auth.credentials.values() if spec.required))


def key_badge(vendor: Vendor) -> str:
    """A vendor page's key badge: none needed, optional, needed by some calls, or needed (naming the variables)."""
    if not vendor.auth.credentials:
        return f'<span className="wc-badge wc-badge-free">{literal("No key needed")}</span>'
    if some_calls_need_key(vendor):
        return f'<span className="wc-badge">{literal("Key for some calls")}</span>'
    if not needs_key(vendor):
        return f'<span className="wc-badge">{literal("Key optional")}</span>'
    names = literal(", ").join(code(name) for name in _required(vendor))
    return f'<span className="wc-badge wc-badge-key">{literal("Needs ")}{names}</span>'


def endpoint_key_badge(vendor: Vendor, endpoint: VendorEndpoint) -> str:
    """A badge on an endpoint whose keys are not the ones its vendor's badge names: a key the vendor leaves
    optional, a different key, or only some of the vendor's keys."""
    keys = list(dict.fromkeys(endpoint_keys(vendor, endpoint)))
    if set(keys) == set(_required(vendor)):
        keys = []
    names = literal(", ").join(code(name) for name in keys)
    return f'<span className="wc-badge wc-badge-key">{literal("Needs ")}{names}</span>' if keys else ""
