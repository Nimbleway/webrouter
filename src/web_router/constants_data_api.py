"""Data API discovery's limits and messages: what ``search`` lists beside the web results, and when.

Kept apart from :mod:`web_router.constants_vendors`, which is about calling a
vendor; this is about finding one.
"""

from __future__ import annotations

from typing import Final

DATA_API_SEARCH_LIMIT: Final = 3
"""Data APIs :meth:`~web_router.WebRouter.search` lists beside the web results when ``max_results`` is not set."""

DATA_API_EXTRACT_LIMIT: Final = 3
"""Data APIs :meth:`~web_router.WebRouter.extract` lists for each page whose site a catalog entry serves."""

DATA_API_NAME_MIN_LENGTH: Final = 4
"""The shortest word of a data API's display name that names it on its own: "fed" or "who" are everyday words."""

DATA_API_COMMON_NAME_SHARE: Final = 4
"""A name word this many data APIs share ("google", "bank", "open") does not name any one of them on its own."""

DATA_API_GENERIC_NAME_WORDS: Final = frozenset({"api", "data", "open", "search", "the", "web"})
"""Name words that never name a data API on their own, however few share them."""

DATA_APIS_ONLY_PROVIDER: Final = "data-apis"
"""``SearchResponse.provider`` for a ``search(data_apis_only=True)``: the local catalog answered, no web backend."""

MSG_DATA_APIS_ONLY_CONFLICT: Final = (
    "search(data_apis_only=True) calls no web backend, so it does not take {names}; drop them, or search the web."
)
MSG_DATA_APIS_ONLY_OFF: Final = "search(data_apis_only=True) needs data_apis=True: it lists data APIs and nothing else."
MSG_DATA_API_CATALOG_SKIPPED: Final = "data API matching skipped: the vendor catalog did not load: %s"
