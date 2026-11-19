"""The vendor modes each extract backend declares, and the wire each one sends.

The extract counterpart of :mod:`web_router.constants_mode_parts`: each wire was checked against the
vendor's docs (the ``source_url`` of its price row). An extract has no depth, so these are tiers and
flags only; none writes the URLs, which stay the router's.
"""

from __future__ import annotations

from collections.abc import Mapping

from web_router.modes import Flag, Tier

TAVILY_EXTRACT_TIERS: Mapping[str, Tier] = {"tavily-advanced": Tier(wire={"extract_depth": "advanced"})}
"""Tables and embedded content too, at two credits per five URLs instead of one."""

LINKUP_EXTRACT_TIERS: Mapping[str, Tier] = {"linkup-pro": Tier(wire={"mode": "pro"})}
"""Linkup's fetch for harder pages; unset, a fetch runs in ``standard``."""
LINKUP_EXTRACT_FLAGS: Mapping[str, Flag] = {"linkup-render-js": Flag(wire={"renderJs": True}, writes="renderJs")}
"""Renders the page's JavaScript before reading it."""

OXYLABS_EXTRACT_TIERS: Mapping[str, Tier] = {"oxylabs-render": Tier(wire={"render": "html"})}
"""The page rendered in a browser first; unset, Oxylabs fetches it without JavaScript."""

NIMBLE_EXTRACT_TIERS: Mapping[str, Tier] = {
    "nimble-vx8": Tier(wire={"driver": "vx8"}),
    "nimble-vx8-pro": Tier(wire={"driver": "vx8-pro"}),
    "nimble-vx10": Tier(wire={"driver": "vx10"}),
    "nimble-vx10-pro": Tier(wire={"driver": "vx10-pro"}),
    "nimble-max": Tier(wire={"driver": "max"}),
}
"""Nimble's browser drivers past its default HTTP ``vx6``: headless, headful, stealth; ``max`` races them all."""

EXA_EXTRACT_FLAGS: Mapping[str, Flag] = {
    "exa-highlights": Flag(wire={"highlights": True}, writes="highlights"),
    "exa-summary": Flag(wire={"summary": True}, writes="summary"),
}
"""Content types beside the page text, each billed per page; they land in each document's ``extra``."""

BROWSERBASE_EXTRACT_FLAGS: Mapping[str, Flag] = {"browserbase-proxies": Flag(wire={"proxies": True}, writes="proxies")}
"""Fetches through Browserbase's proxies, which web-router never turns on itself."""
