"""WebIQ search: the shared options it maps beyond ``max_results``, and the operators filters become.

Kept apart from :mod:`web_router.constants`, so that file stops growing. The
host, paths, key header and ``max_results`` name stay there. Checked against
WebIQ's OpenAPI document (``WebRequest``) on 2026-11-09.
"""

from __future__ import annotations

from typing import Any

from web_router.types_options import SearchDepth

WEBIQ_SEARCH_MAX_LENGTH = 500_000
"""WebIQ's ceiling on ``maxLength``, the characters of each result's content."""

WEBIQ_PARAMS_BY_DEPTH: dict[SearchDepth, dict[str, Any]] = {
    "lite": {"maxLength": 500},
    "fast": {"maxLength": 3000},
    "deep": {"contentFormat": "text", "maxLength": WEBIQ_SEARCH_MAX_LENGTH},
}
"""``search_depth`` -> the fields it sets, laid over the passages every search asks for.

``lite`` and ``fast`` cap the passages that answer the query. ``deep`` asks for
each page's whole text up to WebIQ's ceiling. A key WebIQ grants passages only
is refused ``text`` with a 403, which fails that search rather than returning
less. Unset, passages run to WebIQ's default of 10,000 characters.
"""

WEBIQ_QUERY_LIMIT = 1000
"""The longest query WebIQ accepts, operators included; a longer one is refused before it is sent."""

# WebIQ filters by domain through site: and -site: operators in the query, its
# only domain filter. Several included sites are joined with OR, so a result may
# come from any of them.
WEBIQ_SEARCH_PARAMETERS = frozenset(
    {"max_results", "search_depth", "include_domains", "exclude_domains", "country", "locale"}
)
"""What WebIQ search maps. ``recency`` and ``effort`` have no WebIQ field."""

MSG_WEBIQ_QUERY_TOO_LONG = (
    "WebIQ takes a query of at most {limit} characters, and this one would be {length}; include_domains and "
    "exclude_domains are sent inside the query and count toward it. Shorten the query, drop a filter, or use "
    "another provider."
)
MSG_WEBIQ_CODE = (
    "WebIQ takes a country as a two-letter region code such as 'US', and a locale as a two-letter ISO 639-1 "
    "language such as 'en' (a regional locale such as 'en-GB' sends its language)."
)
