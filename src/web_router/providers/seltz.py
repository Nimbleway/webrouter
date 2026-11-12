"""Seltz search provider, over the Search API.

Talks to the REST endpoint directly. Install with the ``search`` extra. Reads
``SELTZ_API_KEY`` unless a key is passed explicitly.

Search maps ``max_results``, both domain lists and ``recency``. Seltz serves
results from its own index, in ``scope`` ``web`` unless another is named, and
returns no title or score: a result's ``title`` stays empty rather than being
made up. ``scope``, ``tier``, ``to_date`` and ``fields`` are reachable through
``provider_options``.
"""

from __future__ import annotations

from typing import Any, ClassVar

from web_router.constants import ENV_SELTZ_API_KEY, PROVIDER_SELTZ
from web_router.constants_seltz import (
    SELTZ_API_KEY_HEADER,
    SELTZ_BASE_URL,
    SELTZ_RECENCY,
    SELTZ_SEARCH_NAMES,
    SELTZ_SEARCH_PARAMETERS,
    SELTZ_SEARCH_PATH,
)
from web_router.providers._http import HttpSearchProvider
from web_router.providers._search_text import as_snippet
from web_router.providers._utils import build_payload, mapped
from web_router.types import SearchRequest, SearchResponse, SearchResult

_MAPPED_RESULT_FIELDS = frozenset({"url", "content", "published_date"})


class SeltzProvider(HttpSearchProvider):
    """Routes searches to the Seltz Search API.

    A result's content is the document body Seltz returns by default. Its
    description is the first snippet when ``fields`` asks for snippets, and
    otherwise the opening of the body; snippets and ``properties`` stay in
    ``extra``.
    """

    name: ClassVar[str] = PROVIDER_SELTZ
    supported_parameters: ClassVar[frozenset[str]] = SELTZ_SEARCH_PARAMETERS

    base_url: ClassVar[str] = SELTZ_BASE_URL
    search_path: ClassVar[str] = SELTZ_SEARCH_PATH
    env_api_key: ClassVar[str] = ENV_SELTZ_API_KEY
    api_key_header: ClassVar[str] = SELTZ_API_KEY_HEADER

    def _build_payload(self, request: SearchRequest) -> dict[str, Any]:
        return build_payload(
            request,
            SELTZ_SEARCH_NAMES,
            query=request.query,
            from_date=mapped(SELTZ_RECENCY, request.recency),
        )

    def _to_response(self, request: SearchRequest, payload: dict[str, Any]) -> SearchResponse:
        return SearchResponse(
            provider=self.name,
            query=request.query,
            results=[self._to_result(document) for document in payload.get("documents") or []],
            request_id=payload.get("request_id"),
            raw=payload,
        )

    @staticmethod
    def _to_result(document: dict[str, Any]) -> SearchResult:
        content = str(document.get("content") or "")
        snippets = [str(s["text"]) for s in document.get("snippets") or [] if isinstance(s, dict) and s.get("text")]
        return SearchResult(
            url=document.get("url") or "",
            description=snippets[0] if snippets else as_snippet(content),
            content=content or "\n\n".join(snippets),
            published_date=document.get("published_date"),
            extra={k: v for k, v in document.items() if k not in _MAPPED_RESULT_FIELDS},
        )
