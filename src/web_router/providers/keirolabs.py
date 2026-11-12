"""Keirolabs search provider, over the lite Search API.

Talks to the REST endpoint directly: Keirolabs publishes no Python SDK. Install
with the ``search`` extra. Reads ``KEIROLABS_API_KEY`` unless a key is passed
explicitly.

Search maps ``max_results`` alone. A result carries a title, a link and a
snippet; the snippet is both its description and its content, since the lite
endpoint returns no page body. ``domain`` and ``position`` stay in ``extra``.
"""

from __future__ import annotations

import math
from typing import Any, ClassVar

from web_router.constants import ENV_KEIROLABS_API_KEY, PROVIDER_KEIROLABS
from web_router.constants_keirolabs import (
    KEIROLABS_API_KEY_PREFIX,
    KEIROLABS_BASE_URL,
    KEIROLABS_SEARCH_NAMES,
    KEIROLABS_SEARCH_PARAMETERS,
    KEIROLABS_SEARCH_PATH,
)
from web_router.providers._http import HttpSearchProvider
from web_router.providers._utils import build_payload
from web_router.types import SearchRequest, SearchResponse, SearchResult

_MAPPED_RESULT_FIELDS = frozenset({"url", "title", "snippet", "score"})


def _finite_score(score: Any) -> float | None:
    """A real number, or ``None``: a boolean, NaN or infinity is not a score to rank by."""
    if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
        return None
    return float(score)


class KeirolabsProvider(HttpSearchProvider):
    """Routes searches to the Keirolabs lite Search API.

    Keirolabs answers ``score: null`` on this endpoint, so ``score`` is set only
    when it sends a number, and no date is returned, so ``published_date`` stays
    unset.
    """

    name: ClassVar[str] = PROVIDER_KEIROLABS
    supported_parameters: ClassVar[frozenset[str]] = KEIROLABS_SEARCH_PARAMETERS

    base_url: ClassVar[str] = KEIROLABS_BASE_URL
    search_path: ClassVar[str] = KEIROLABS_SEARCH_PATH
    env_api_key: ClassVar[str] = ENV_KEIROLABS_API_KEY
    api_key_prefix: ClassVar[str] = KEIROLABS_API_KEY_PREFIX

    def _build_payload(self, request: SearchRequest) -> dict[str, Any]:
        return build_payload(request, KEIROLABS_SEARCH_NAMES, query=request.query)

    def _to_response(self, request: SearchRequest, payload: dict[str, Any]) -> SearchResponse:
        return SearchResponse(
            provider=self.name,
            query=request.query,
            results=[self._to_result(hit) for hit in payload.get("results") or [] if hit.get("url")],
            raw=payload,
        )

    @staticmethod
    def _to_result(hit: dict[str, Any]) -> SearchResult:
        snippet = hit.get("snippet") or ""
        return SearchResult(
            url=hit["url"],
            title=hit.get("title") or "",
            description=snippet,
            content=snippet,
            score=_finite_score(hit.get("score")),
            extra={k: v for k, v in hit.items() if k not in _MAPPED_RESULT_FIELDS},
        )
