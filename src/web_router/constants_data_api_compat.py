"""The constants the vendor-era names need: kept apart, they go when those names do.

See :mod:`web_router.client_vendors_compat` for the router methods.
"""

from __future__ import annotations

from typing import Final

VENDOR_ENV_ALLOW_WRITES_OLD: Final = "WEB_ROUTER_ALLOW_VENDOR_WRITES"
"""The variable's old name, still read: the first write it allows logs a deprecation warning, once."""

MSG_DATA_API_DEPRECATED: Final = "`{old}` is deprecated and will be removed: use `{new}`. Vendors are now data APIs."
"""The warning for a vendor-era name: a router method, a keyword, an MCP tool or a variable."""
