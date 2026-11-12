"""Seltz search: its endpoint, limits and the option mappings.

Kept apart from :mod:`web_router.constants`, as the other newer backends' are,
so that file stops growing. The env var and provider name stay there, beside
every other backend's.
"""

from __future__ import annotations

from web_router.types_options import Recency

# POST /v1/search (docs.seltz.ai/api-reference/search), authenticated with the
# x-api-key header. Mapped from Seltz's OpenAPI 3.1 spec
# (docs.seltz.ai/api-reference/openapi.json); not yet checked against the live API.

SELTZ_BASE_URL = "https://api.seltz.ai"
SELTZ_SEARCH_PATH = "/v1/search"
SELTZ_API_KEY_HEADER = "x-api-key"

SELTZ_SEARCH_PARAMETERS = frozenset({"max_results", "include_domains", "exclude_domains", "recency"})
"""What Seltz search maps.

``max_results``, ``include_domains`` and ``exclude_domains`` keep their names;
``recency`` goes to ``from_date`` as an offset from now (``now-7d``).

``country`` and ``locale`` stay unmapped and raise: Seltz documents neither.
``search_depth`` and ``effort`` too: a document's body comes back unless
``fields`` turns it off, and ``tier`` (``base`` or ``pro``, Pro unless set)
reorders the same results rather than searching harder. Both are reachable
through ``provider_options``.
"""

SELTZ_SEARCH_NAMES = {
    "max_results": "max_results",
    "include_domains": "include_domains",
    "exclude_domains": "exclude_domains",
}

SELTZ_RECENCY: dict[Recency, str] = {"day": "now-1d", "week": "now-7d", "month": "now-1M", "year": "now-1y"}
"""``from_date`` offsets: ``M`` is a calendar month and ``y`` a calendar year, ``m`` would be a minute."""
