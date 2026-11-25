"""Firecrawl extract and search: the endpoints, limits and option mappings.

Kept apart from :mod:`web_router.constants`, as the other newer backends' are,
so that file stops growing. The env var and the provider name stay there,
beside every other backend's.
"""

from __future__ import annotations

from typing import Any

from web_router.types import ExtractFormat
from web_router.types_options import SearchDepth

# --- Extract ------------------------------------------------------------------
# Verified live keyless on 2026-10-01: the request body and the
# `success` / `data.<format>` response for markdown and html. The keyed path
# differs only by its Bearer header.

FIRECRAWL_BASE_URL = "https://api.firecrawl.dev"
FIRECRAWL_EXTRACT_PATH = "/v2/scrape"
FIRECRAWL_API_KEY_PREFIX = "Bearer "

FIRECRAWL_EXTRACT_FORMATS: dict[ExtractFormat, str] = {
    # Normalized format -> the `formats` entry to ask for, which is also the key
    # under `data` the body lands in.
    "markdown": "markdown",
    "html": "html",
}

FIRECRAWL_EXTRACT_DEFAULT_FORMAT: ExtractFormat = "markdown"

# --- Search -------------------------------------------------------------------
# POST /v2/search (docs.firecrawl.dev/api-reference/endpoint/search), the host
# and Bearer key extract uses. Checked against the live API on 2026-11-24: v2
# answers {"query", "limit"} with the same hits as /v1/search, which now answers
# with a deprecation warning.

FIRECRAWL_SEARCH_PATH = "/v2/search"

FIRECRAWL_SEARCH_PARAMETERS = frozenset({"max_results", "search_depth"})
"""What Firecrawl search maps: ``max_results`` as ``limit``, and ``search_depth``.

``lite`` cuts each result's description to a snippet; ``deep`` adds
``scrapeOptions``, so Firecrawl reads every result's page and returns its
markdown. Firecrawl has no size between the two, so ``fast`` raises. Domain,
date, country and language filters are reachable through ``provider_options``
(``tbs``, ``location``, ``country``) but not mapped, so they raise too.
"""

FIRECRAWL_MAX_LIMIT = 100
"""The most results one search returns; a larger ``max_results`` is sent as this."""

FIRECRAWL_DEPTH: dict[SearchDepth, dict[str, Any]] = {
    "lite": {},
    "deep": {"scrapeOptions": {"formats": ["markdown"]}},
}
"""The body each ``search_depth`` level adds. ``lite`` adds nothing: the cut is made here, on the description."""

FIRECRAWL_SEARCH_BODY_FIELD = "markdown"
"""Where a scraped result carries its page, when ``deep`` asked for one."""

FIRECRAWL_WEB_RESULTS = "web"
"""The ``data`` key holding the web results; ``news`` and ``images`` answer only when ``sources`` asks for them."""

MSG_FIRECRAWL_SEARCH_UNSUCCESSFUL = "Firecrawl search did not succeed: {error}"
"""A 200 body with ``success: false``: Firecrawl reports a refused search there, not in the status."""

MSG_FIRECRAWL_NO_RESULTS = "Firecrawl answered without a data object (fields: {fields})"
"""A 200 body with no ``data`` object is a reply this mapping cannot read, not a search that found nothing."""
