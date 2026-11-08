"""Keenable search: the shared options it maps and the values each becomes.

Kept apart from :mod:`web_router.constants`, so that file stops growing. The
host, paths and headers stay there, beside the fetch settings they share.
Checked against docs.keenable.ai/api-reference/search on 2026-11-09.
"""

from __future__ import annotations

from web_router.types_options import Recency, ResearchEffort, SearchDepth

KEENABLE_PARAM_NAMES = {"max_results": "max_results"}
"""Normalized parameter -> Keenable field, sent as the caller set it. The rest translate, below."""

KEENABLE_SNIPPET_CHARS: dict[SearchDepth, int] = {"lite": 500, "fast": 3000}
"""``search_depth`` -> ``snippet_max_length``, which Keenable takes from 180 to 10,000 characters.

``deep`` is left out and refused: 10,000 characters is short of a whole page.
Unset, a snippet often runs to several thousand characters.
"""

KEENABLE_PUBLISHED_AFTER: dict[Recency, str] = {"day": "1d", "week": "7d", "month": "1mo", "year": "1y"}
"""``recency`` -> ``published_after``, as Keenable's relative deltas (``mo`` is 30 days, ``y`` 365).

Keenable then leaves out pages with no known publish date.
"""

KEENABLE_MODE_BY_EFFORT: dict[ResearchEffort, str | None] = {
    "low": "realtime",
    "medium": None,
    "high": "pro",
    "max": "pro",
}
"""``effort`` -> ``mode``. ``pro``, Keenable's default and deeper tier, serves every level from ``medium`` up.

``medium`` sends nothing, so it gets that default; ``high`` and ``max`` name
``pro``, Keenable's most thorough, and ``low`` its fastest, ``realtime``.
"""

KEENABLE_SEARCH_PARAMETERS: frozenset[str] = frozenset(KEENABLE_PARAM_NAMES) | {
    "search_depth",
    "include_domains",
    "recency",
    "effort",
}
"""What Keenable search maps. ``exclude_domains``, ``country`` and ``locale`` have no Keenable field."""

MSG_KEENABLE_ONE_SITE = (
    "Keenable filters to one site per search (its `site` field, which takes a domain or a domain/path); "
    "list one, or name a provider that takes several."
)
