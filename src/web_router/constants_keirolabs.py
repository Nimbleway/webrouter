"""Keirolabs search: its endpoint and the option mapping.

Kept apart from :mod:`web_router.constants`, as the other newer backends' are,
so that file stops growing. The env var and provider name stay there, beside
every other backend's.
"""

from __future__ import annotations

# POST /api/v2/search/lite (keirolabs.cloud/docs), authenticated with a Bearer
# token in the Authorization header. Checked against the live API on 2026-11-12.

KEIROLABS_BASE_URL = "https://api.keirolabs.cloud"
KEIROLABS_SEARCH_PATH = "/api/v2/search/lite"
KEIROLABS_API_KEY_PREFIX = "Bearer "

KEIROLABS_SEARCH_PARAMETERS = frozenset({"max_results"})
"""What Keirolabs lite search maps: ``max_results``, sent as ``maxResults``.

The lite endpoint documents no domain, date, country or language filter, so
those raise rather than run unfiltered. It returns titles, links and snippets,
never a page body, so ``search_depth`` and ``effort`` raise too.
"""

KEIROLABS_SEARCH_NAMES = {"max_results": "maxResults"}
