"""SpecStream search and extract: endpoints and option mapping.

Kept apart from :mod:`web_router.constants`, as the other newer backends' are,
so that file stops growing. The env var and provider name stay there.
"""

from __future__ import annotations

from web_router.types import ExtractFormat

# GET /v1/web-search and POST /v1/extract (specstream.ai/agents.md), with the
# key as a Bearer token. Both answer {"data": {...}}.

SPECSTREAM_BASE_URL = "https://api.specstream.ai"
SPECSTREAM_SEARCH_PATH = "/v1/web-search"
SPECSTREAM_EXTRACT_PATH = "/v1/extract"
SPECSTREAM_API_KEY_PREFIX = "Bearer "

SPECSTREAM_SEARCH_PARAMETERS = frozenset({"max_results"})
"""What SpecStream search maps: ``max_results`` goes to ``num``, at most :data:`SPECSTREAM_MAX_RESULTS`.

``search_depth`` and the rest stay unmapped and raise. ``passages``,
``speed`` and the other query fields are reachable through ``provider_options``.
"""

SPECSTREAM_MAX_RESULTS = 100
"""The most ``sources`` one call returns; a larger ``num`` is a 400, so it is lowered to this."""

SPECSTREAM_EXTRACT_FORMATS: dict[ExtractFormat, str] = {"markdown": "markdown", "text": "text"}
"""Normalized format -> the ``format`` field's value. The page body is in ``text`` either way."""

SPECSTREAM_EXTRACT_DEFAULT_FORMAT: ExtractFormat = "markdown"

SPECSTREAM_PAGE_OK = "ok"
"""The one ``status`` of a page that was read; ``blocked``, ``not_found``, ``empty`` and the rest are failures."""

MSG_SPECSTREAM_NO_LIST = "SpecStream answered without a data.{field} list (fields: {fields})"
"""A 200 body with no list is a reply this mapping cannot read, not a call that found nothing."""

MSG_SPECSTREAM_PAGE_FAILED = "SpecStream could not read the page: {status}"
