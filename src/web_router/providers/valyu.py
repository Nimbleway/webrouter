"""Valyu search provider, over the Search API.

Talks to the REST endpoint directly. Install with the ``search`` extra. Reads
``VALYU_API_KEY`` unless a key is passed explicitly.

Search maps ``max_results``, both domain lists (Valyu takes them as fields,
not query operators), ``country`` and ``recency``. Every search is a web
search: Valyu's default would also search paid datasets, see
:data:`~web_router.constants_valyu.VALYU_SEARCH_TYPE`. Everything else,
``response_length`` and ``fast_mode`` included, is reachable through
``provider_options``.
"""

from __future__ import annotations

from typing import Any, ClassVar

from web_router.constants import ENV_VALYU_API_KEY, PROVIDER_VALYU
from web_router.constants_valyu import (
    MSG_VALYU_COUNTRY,
    MSG_VALYU_UNSUCCESSFUL,
    VALYU_API_KEY_HEADER,
    VALYU_BASE_URL,
    VALYU_COUNTRIES,
    VALYU_MAX_RESULTS,
    VALYU_SEARCH_PARAMETERS,
    VALYU_SEARCH_PATH,
    VALYU_SEARCH_TYPE,
)
from web_router.exceptions import ProviderError
from web_router.providers._clock import recency_start
from web_router.providers._http import HttpSearchProvider
from web_router.providers._search_text import as_snippet
from web_router.providers._utils import best_text, build_payload
from web_router.types import SearchRequest, SearchResponse, SearchResult

_MAPPED_RESULT_FIELDS = frozenset({"url", "title", "content", "description", "publication_date", "relevance_score"})


class ValyuProvider(HttpSearchProvider):
    """Routes searches to the Valyu Search API.

    ``score`` is Valyu's ``relevance_score``, from 0 to 1. A result's content is
    its page body in markdown, cut at Valyu's ``response_length`` (25,000
    characters unless set); ``source``, ``price`` and the rest stay in ``extra``.
    """

    name: ClassVar[str] = PROVIDER_VALYU
    supported_parameters: ClassVar[frozenset[str]] = VALYU_SEARCH_PARAMETERS
    refusal_reasons: ClassVar[dict[str, str]] = {"country": MSG_VALYU_COUNTRY}

    base_url: ClassVar[str] = VALYU_BASE_URL
    search_path: ClassVar[str] = VALYU_SEARCH_PATH
    env_api_key: ClassVar[str] = ENV_VALYU_API_KEY
    api_key_header: ClassVar[str] = VALYU_API_KEY_HEADER

    @classmethod
    def refused_options(cls, request: SearchRequest) -> dict[str, Any]:
        if request.country and request.country.upper() not in VALYU_COUNTRIES:
            return {"country": request.country}
        return {}

    def _build_payload(self, request: SearchRequest) -> dict[str, Any]:
        since = recency_start(request.recency)
        return build_payload(
            request,
            query=request.query,
            max_num_results=min(request.max_results, VALYU_MAX_RESULTS) if request.max_results else None,
            included_sources=list(request.include_domains) if request.include_domains else None,
            excluded_sources=list(request.exclude_domains) if request.exclude_domains else None,
            country_code=request.country.upper() if request.country else None,
            start_date=since.isoformat() if since else None,
            **VALYU_SEARCH_TYPE,
        )

    def _to_response(self, request: SearchRequest, payload: dict[str, Any]) -> SearchResponse:
        if payload.get("success") is False:
            raise ProviderError(self.name, MSG_VALYU_UNSUCCESSFUL.format(error=payload.get("error")))
        return SearchResponse(
            provider=self.name,
            query=request.query,
            results=[self._to_result(hit) for hit in payload.get("results") or []],
            request_id=payload.get("tx_id"),
            raw=payload,
        )

    @staticmethod
    def _to_result(hit: dict[str, Any]) -> SearchResult:
        content = hit.get("content")
        body = content if isinstance(content, str) else ""
        # With no description of its own, the opening of the body is the snippet.
        description = hit.get("description") or as_snippet(body)
        return SearchResult(
            url=hit.get("url", ""),
            title=hit.get("title") or "",
            description=description,
            content=best_text(body, description),
            score=hit.get("relevance_score"),
            # Valyu sends "" for a page with no known date.
            published_date=hit.get("publication_date") or None,
            extra={k: v for k, v in hit.items() if k not in _MAPPED_RESULT_FIELDS},
        )
