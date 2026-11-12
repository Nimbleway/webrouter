"""Valyu search: its endpoint, limits and the option mappings.

Kept apart from :mod:`web_router.constants`, as the other newer backends' are,
so that file stops growing. The env var and provider name stay there, beside
every other backend's.
"""

from __future__ import annotations

# POST /v1/search (docs.valyu.ai/api-reference/endpoint/search), authenticated
# with the X-API-Key header. Mapped from Valyu's OpenAPI spec (version 2.3.0);
# not yet checked against the live API.

VALYU_BASE_URL = "https://api.valyu.ai"
VALYU_SEARCH_PATH = "/v1/search"
VALYU_API_KEY_HEADER = "X-API-Key"

VALYU_SEARCH_PARAMETERS = frozenset({"max_results", "include_domains", "exclude_domains", "country", "recency"})
"""What Valyu search maps.

``max_results`` goes to ``max_num_results``, the domain lists to
``included_sources`` and ``excluded_sources``, ``country`` to ``country_code``
and ``recency`` to ``start_date``, the first day of the window.

``locale`` stays unmapped and raises: Valyu documents no language filter.
``search_depth`` and ``effort`` too: every result carries its page body, up to
``response_length`` characters, and ``fast_mode`` trades relevance for speed
rather than choosing how much of a page comes back. Both are reachable through
``provider_options``.
"""

VALYU_SEARCH_NAMES = {"max_results": "max_num_results"}

VALYU_MAX_RESULTS = 20
"""The most results one search returns on a standard plan; a larger ``max_num_results`` is lowered to it.

Sent as this rather than left to Valyu, so the request carries no warning about a cap.
"""

VALYU_SEARCH_TYPE = {"search_type": "web"}
"""Sent on every search, so web search stays web search.

Valyu's own default, ``all``, lets a model pick datasets beside the web
(papers, filings, patents), priced per result at up to 300 times a web result.
``provider_options={"search_type": "all"}`` restores it.
"""

VALYU_COUNTRIES = frozenset(
    {
        "AR", "AU", "AT", "BE", "BR", "CA", "CL", "DK", "FI", "FR", "DE", "HK", "IN", "ID", "IT", "JP", "KR", "MY",
        "MX", "NL", "NZ", "CN", "PL", "PT", "PH", "RU", "SA", "ZA", "ES", "SE", "CH", "TW", "TR", "GB", "US",
    }
)  # fmt: skip
"""The ``country_code`` values Valyu lists; another is a 400, so it is refused before the call."""

MSG_VALYU_COUNTRY = (
    "Valyu geo-targets only the countries it lists: "
    + ", ".join(sorted(VALYU_COUNTRIES))
    + ". Ask for one of those, leave country unset, or name another provider."
)

MSG_VALYU_UNSUCCESSFUL = "Valyu answered without success: {error}"
