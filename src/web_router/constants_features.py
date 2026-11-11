"""The built-in ``features`` router: its name and the reason its decisions give.

Experimental: :mod:`web_router.routing` is new and may change before 1.0.
Kept apart from :mod:`web_router.constants_invoke`, so that file stops growing.
"""

from __future__ import annotations

from typing import Final

FEATURES_ROUTER_NAME: Final = "features"
"""The name ``router=`` takes for the built-in keyless router that reads a prompt's features, and the default."""

FEATURES_REASON: Final = "features: verb={verb} has_url={has_url} plural={plural} kw_research={kw_research}"
"""What its decision gives as the reason: the row of the table it read the method from."""
