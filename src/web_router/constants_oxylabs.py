"""Oxylabs extract and search: the realtime endpoint, extract's formats and search's ``google_search`` source.

Kept apart from :mod:`web_router.constants`, as the other newer backends' are,
so that file stops growing. The env vars and the provider name stay there,
beside every other backend's. Search posts to the realtime ``/v1/queries``
extract uses, with the same username and password.
"""

from __future__ import annotations

from typing import Final

from web_router.types import ExtractFormat

# --- Extract ------------------------------------------------------------------
# Verified live on 2026-11-03. See tests/providers/test_oxylabs.py.

OXYLABS_BASE_URL = "https://realtime.oxylabs.io"
OXYLABS_EXTRACT_PATH = "/v1/queries"
"""The realtime endpoint, the only one that returns content in the response.

The push-pull endpoint at ``data.oxylabs.io`` submits a job and hands back an id
to poll, which is a different shape of integration than ``extract()`` promises.
"""

OXYLABS_UNIVERSAL_SOURCE = "universal"
"""The ``source`` that means "any website", as opposed to a per-site parser."""

OXYLABS_MARKDOWN_PARAM = "markdown"

OXYLABS_EXTRACT_FORMATS: dict[ExtractFormat, str] = {
    # Normalized format -> the `type` Oxylabs tags the matching result with. A
    # request can ask for several representations at once and each comes back as
    # its own entry in `results`, in no guaranteed order, so the reply is picked
    # by `type` rather than by position.
    "html": "raw",
    "markdown": "markdown",
}

OXYLABS_EXTRACT_DEFAULT_FORMAT: ExtractFormat = "html"
"""Without the ``markdown`` flag Oxylabs returns the raw HTML it fetched."""

OXYLABS_SEARCH_SOURCE: Final = "google_search"
"""Sent on every search, with ``parse: true``: one page of Google, parsed by Oxylabs."""

OXYLABS_SEARCH_PARAMETERS = frozenset({"max_results"})
"""What Oxylabs search maps: ``max_results``, as ``limit`` and applied to the results.

``limit`` is the source's results per page, 10 when unset; Google answers
about ten organic results a page whatever is asked, so a smaller
``max_results`` is cut here too. ``geo_location``, ``locale``, ``pages`` and
``context`` (``tbs``, ``filter``) are reachable through ``provider_options``
but not mapped, so the shared filters raise rather than run unfiltered. One page
of Google has no depth to choose: ``search_depth`` and ``effort`` raise too.
"""

OXYLABS_SEARCH_NAMES: Final = {"max_results": "limit"}

OXYLABS_ORGANIC: Final = "organic"

MSG_OXYLABS_SEARCH_NOT_PARSED: Final = (
    "Oxylabs did not return a parsed Google page: status {status}, parse_status_code {code} (fields: {fields})"
)
"""No result, a fetch that failed, or a page the parser could not read: an error, not a search that found nothing."""
