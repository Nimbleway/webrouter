"""Octen search: its endpoint, limits and the option mappings.

Kept apart from :mod:`web_router.constants`, as the other newer backends' are,
so that file stops growing. The env var and provider name stay there, beside
every other backend's.
"""

from __future__ import annotations

from typing import Any

from web_router.types_options import Recency, SearchDepth

# POST /search (docs.octen.ai/api-reference/search), authenticated with a bearer
# key. Mapped from Octen's OpenAPI 3.1 spec; not yet checked against the live API.

OCTEN_BASE_URL = "https://api.octen.ai"
OCTEN_SEARCH_PATH = "/search"
OCTEN_API_KEY_PREFIX = "Bearer "

OCTEN_SEARCH_PARAMETERS = frozenset(
    {"max_results", "include_domains", "exclude_domains", "locale", "recency", "search_depth"}
)
"""What Octen search maps.

``max_results`` goes to ``count``, the domain lists keep their names,
``locale`` goes to ``language`` as a bare ISO 639-1 code, ``recency`` to
``time_range`` and ``search_depth`` to ``full_content`` (see
``OCTEN_DEPTH``).

``country`` stays unmapped and raises: Octen documents no country or region
filter. ``effort`` too: Web Search has one mode.
"""

OCTEN_SEARCH_NAMES = {
    "max_results": "count",
    "include_domains": "include_domains",
    "exclude_domains": "exclude_domains",
}

OCTEN_MAX_COUNT = 100
"""The most results one search returns; a larger ``count`` is out of range, so it is sent as this."""

OCTEN_RECENCY: dict[Recency, str] = {"day": "day", "week": "week", "month": "month", "year": "year"}

OCTEN_LANGUAGES = frozenset(
    {"ar", "de", "en", "es", "fr", "hi", "id", "it", "ja", "ko", "nl", "pl", "pt", "ru", "th", "tr", "vi", "zh"}
)
"""The ``language`` values Octen lists; another is out of range, so it is refused before the call."""

OCTEN_DEPTH: dict[SearchDepth, dict[str, Any]] = {
    "fast": {"full_content": {"enable": True}},
    "deep": {"full_content": {"enable": True, "max_tokens": 100_000}},
}
"""``search_depth`` as Octen's ``full_content``, which is off unless asked for.

``fast`` turns it on at Octen's default of 2,048 tokens a page, a truncated
body; ``deep`` raises that to 100,000, the most Octen allows, the whole page in
practice. ``lite`` is refused: the default highlight runs to 512 tokens, about
2,000 characters, four times a snippet. Full content is billed per result on
top of the search ($0.50 per 1,000 results).
"""

MSG_OCTEN_LOCALE = (
    "Octen filters by the languages it lists: "
    + ", ".join(sorted(OCTEN_LANGUAGES))
    + ". Ask for one of those, leave locale unset, or name another provider."
)

MSG_OCTEN_UNSUCCESSFUL = "Octen answered with code {code}: {msg}"
