"""Octen search provider, over the Web Search API.

Talks to the REST endpoint directly. Install with the ``search`` extra. Reads
``OCTEN_API_KEY`` unless a key is passed explicitly.

Search maps ``max_results``, both domain lists, ``locale``, ``recency`` and
``search_depth`` (``fast`` and ``deep``, as Octen's ``full_content``).
``include_text``, ``start_time``, ``safesearch`` and the rest are reachable
through ``provider_options``.
"""

from __future__ import annotations

from typing import Any, ClassVar

from web_router.constants import ENV_OCTEN_API_KEY, PROVIDER_OCTEN
from web_router.constants_octen import (
    MSG_OCTEN_LOCALE,
    MSG_OCTEN_UNSUCCESSFUL,
    OCTEN_API_KEY_PREFIX,
    OCTEN_BASE_URL,
    OCTEN_DEPTH,
    OCTEN_LANGUAGES,
    OCTEN_MAX_COUNT,
    OCTEN_RECENCY,
    OCTEN_SEARCH_NAMES,
    OCTEN_SEARCH_PARAMETERS,
    OCTEN_SEARCH_PATH,
)
from web_router.exceptions import ProviderError
from web_router.providers._http import HttpSearchProvider
from web_router.providers._language import language_code
from web_router.providers._search_text import as_snippet
from web_router.providers._utils import best_text, build_payload, mapped
from web_router.types import SearchDepth, SearchRequest, SearchResponse, SearchResult

_MAPPED_RESULT_FIELDS = frozenset({"url", "title", "highlight", "full_content", "time_published"})


class OctenProvider(HttpSearchProvider):
    """Routes searches to the Octen Web Search API.

    A result's description is the opening of Octen's query-relevant
    ``highlight``; its content is the page body when ``search_depth`` asked for
    one, and the highlight otherwise. Octen returns no score.
    """

    name: ClassVar[str] = PROVIDER_OCTEN
    supported_parameters: ClassVar[frozenset[str]] = OCTEN_SEARCH_PARAMETERS
    depth_levels: ClassVar[frozenset[SearchDepth]] = frozenset(OCTEN_DEPTH)
    refusal_reasons: ClassVar[dict[str, str]] = {"locale": MSG_OCTEN_LOCALE}

    base_url: ClassVar[str] = OCTEN_BASE_URL
    search_path: ClassVar[str] = OCTEN_SEARCH_PATH
    env_api_key: ClassVar[str] = ENV_OCTEN_API_KEY
    api_key_prefix: ClassVar[str] = OCTEN_API_KEY_PREFIX

    @classmethod
    def refused_options(cls, request: SearchRequest) -> dict[str, Any]:
        if request.locale and language_code(request.locale) not in OCTEN_LANGUAGES:
            return {"locale": request.locale}
        return {}

    def _build_payload(self, request: SearchRequest) -> dict[str, Any]:
        payload = build_payload(
            request,
            OCTEN_SEARCH_NAMES,
            query=request.query,
            language=[language_code(request.locale)] if request.locale else None,
            time_range=mapped(OCTEN_RECENCY, request.recency),
            **(mapped(OCTEN_DEPTH, request.search_depth) or {}),
        )
        if request.max_results:
            payload["count"] = min(payload["count"], OCTEN_MAX_COUNT)
        return payload

    def _to_response(self, request: SearchRequest, payload: dict[str, Any]) -> SearchResponse:
        code = payload.get("code")
        if code not in (None, 0):
            raise ProviderError(self.name, MSG_OCTEN_UNSUCCESSFUL.format(code=code, msg=payload.get("msg")))
        data = payload.get("data") or {}
        return SearchResponse(
            provider=self.name,
            query=request.query,
            results=[self._to_result(hit) for hit in data.get("results") or []],
            request_id=payload.get("request_id"),
            raw=payload,
        )

    @staticmethod
    def _to_result(hit: dict[str, Any]) -> SearchResult:
        highlight = str(hit.get("highlight") or "")
        return SearchResult(
            url=hit.get("url") or "",
            title=hit.get("title") or "",
            description=as_snippet(highlight),
            content=best_text(hit.get("full_content"), highlight),
            published_date=hit.get("time_published"),
            extra={k: v for k, v in hit.items() if k not in _MAPPED_RESULT_FIELDS},
        )
