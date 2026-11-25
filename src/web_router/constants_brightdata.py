"""Bright Data extract and search: the Web Unlocker's formats and the SERP API request.

Kept apart from :mod:`web_router.constants`, as the other newer backends' are,
so that file stops growing. The env vars and the provider name stay there,
beside every other backend's.
"""

from __future__ import annotations

from typing import Any, Final

from web_router.types import ExtractFormat

# --- Extract: the Web Unlocker ---------------------------------------------------
# Verified live on 2026-11-03. See tests/providers/test_brightdata.py.

BRIGHTDATA_BASE_URL = "https://api.brightdata.com"
BRIGHTDATA_EXTRACT_PATH = "/request"
BRIGHTDATA_API_KEY_PREFIX = "Bearer "

BRIGHTDATA_RESPONSE_FORMAT = "json"
"""Extract always asks for the JSON envelope, never ``format="raw"``.

``raw`` returns the fetched page as the HTTP body itself -- no status code, no
headers, and not JSON, so decoding it would fail. The envelope carries the same
body under ``body`` plus the target's status and headers, which is strictly more
to normalize from. Bright Data's own reference shows the envelope beside a
``raw`` request, which reads as though ``raw`` returned it too; it does not.
"""

BRIGHTDATA_EXTRACT_FORMATS: dict[ExtractFormat, dict[str, Any]] = {
    # `data_format` post-processes what the unlocker fetched; absent, the
    # envelope's `body` holds the page's own HTML.
    "html": {},
    "markdown": {"data_format": "markdown"},
}

BRIGHTDATA_EXTRACT_DEFAULT_FORMAT: ExtractFormat = "html"

BRIGHTDATA_ERROR_HEADER = "x-brd-error"
"""The envelope header in which the unlocker says why it could not fetch the page.

Seen live on 2026-10-16: an unresolvable host answers 200 with an envelope whose
``status_code`` is 502, ``body`` is empty, and this header holds the reason. A
target that answers an error page of its own carries no such header -- that page
was fetched, and stays a document with the target's status.
"""

BRIGHTDATA_ERROR_CODE_HEADER = "x-brd-error-code"
"""The short code beside :data:`BRIGHTDATA_ERROR_HEADER` (``proxy_error`` live).

Read on its own too, so a failure that names only a code is still a failure.
"""

# --- Search: the SERP API ---------------------------------------------------------
# POST https://api.brightdata.com/request with a Google search URL (docs.brightdata.com,
# SERP API), the endpoint and Bearer token extract uses. brd_json=1 asks Bright
# Data to parse the page; format "raw" returns that parse as the body itself.

BRIGHTDATA_SERP_URL: Final = "https://www.google.com/search"
BRIGHTDATA_SERP_FORMAT: Final = "raw"
BRIGHTDATA_SERP_PARSED: Final = {"brd_json": "1"}
"""Bright Data's own URL parameter: the parsed page as JSON rather than Google's HTML."""

BRIGHTDATA_SEARCH_PARAMETERS = frozenset({"max_results"})
"""What Bright Data search maps: ``max_results``, sent as Google's ``num`` and applied to the results.

Google stopped honouring ``num`` on 2025-09-11 and answers about ten organic
results a page, so a smaller ``max_results`` is cut here and a larger one
gets what one page holds. Google's other parameters (``gl``, ``hl``, ``tbs``)
belong in the search URL, which ``provider_options["url"]`` replaces whole,
so the shared filters raise rather than run unfiltered. One page of Google has
no depth to choose: ``search_depth`` and ``effort`` raise too.
"""

BRIGHTDATA_ORGANIC: Final = "organic"
BRIGHTDATA_GENERAL: Final = "general"
"""The parse's summary of the search; a body with neither it nor ``organic`` is not a parsed page."""

MSG_BRIGHTDATA_NO_RESULTS: Final = "Bright Data answered without a parsed search page (fields: {fields})"
"""A body with no ``organic`` list and no ``general`` block: an error envelope or an unparsed page, not no hits."""
