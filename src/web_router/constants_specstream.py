"""SpecStream search and extract: endpoints and option mapping."""

from __future__ import annotations

# GET https://api.specstream.ai/v1/web-search
# POST /v1/extract

SPECSTREAM_BASE_URL = "https://api.specstream.ai"
SPECSTREAM_SEARCH_PATH = "/v1/web-search"
SPECSTREAM_EXTRACT_PATH = "/v1/extract"
SPECSTREAM_API_KEY_PREFIX = "Bearer "

SPECSTREAM_SEARCH_PARAMETERS = frozenset({"max_results", "search_depth"})
SPECSTREAM_SEARCH_NAMES = {"max_results": "max_results", "search_depth": "search_depth"}

MSG_SPECSTREAM_NO_RESULTS = "SpecStream answered without a results list (fields: {fields})"
"""A 200 body with no ``results`` list is a reply this mapping cannot read, not a search that found nothing."""
