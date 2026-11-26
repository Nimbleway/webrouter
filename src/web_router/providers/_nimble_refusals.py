"""What Nimble search refuses on the class, before anything is sent."""

from __future__ import annotations

from typing import Any

from web_router.constants_nimble import (
    MSG_NIMBLE_DOMAINS,
    MSG_NIMBLE_LITE_RECENCY,
    NIMBLE_DOMAIN_LIMITS,
    NIMBLE_LITE_DOMAIN_LIMIT,
)
from web_router.providers._search_text import DOMAIN_LISTS
from web_router.types import SearchRequest

NIMBLE_REFUSAL_REASONS: dict[str, str] = {
    "recency": MSG_NIMBLE_LITE_RECENCY,
    **dict.fromkeys(DOMAIN_LISTS, MSG_NIMBLE_DOMAINS),
}


def refused_search_options(request: SearchRequest) -> dict[str, Any]:
    """``recency`` at lite, and a domain list past Nimble's limit for the depth asked."""
    lite = request.search_depth == "lite"
    refused: dict[str, Any] = {"recency": request.recency} if request.recency and lite else {}
    for name in DOMAIN_LISTS:
        domains = getattr(request, name) or []
        # The raw cap holds at every depth; lite's is checked after duplicates are dropped. The refusal names the
        # count rather than the list, which can run to thousands of entries in the error message.
        if len(domains) > NIMBLE_DOMAIN_LIMITS[name]:
            refused[name] = len(domains)
        elif lite and len(set(domains)) > NIMBLE_LITE_DOMAIN_LIMIT:
            refused[name] = len(set(domains))
    return refused
