"""You.com provider, over the Search API.

You.com publishes no Python SDK, so this talks to the REST endpoint directly.
Install with the ``search`` extra. Reads ``YOU_API_KEY`` unless a
key is passed explicitly.

web-router sends You.com the minimum a search needs: the query, ``max_results``
and ``search_depth``. Everything else its API offers is reachable through
``provider_options``, and mapping one properly is a welcome PR -- see
CONTRIBUTING.md.
"""

from __future__ import annotations

from typing import Any, ClassVar

from web_router.constants import (
    ENV_YOU_API_KEY,
    PROVIDER_YOU,
    YOU_API_KEY_HEADER,
    YOU_BASE_URL,
    YOU_EXTRACTION_BY_DEPTH,
    YOU_KNOWLEDGE_ANSWER_TYPE,
    YOU_PARAM_NAMES,
    YOU_RESULT_SECTIONS,
    YOU_SEARCH_PATH,
)
from web_router.providers._http import HttpSearchProvider
from web_router.providers._utils import best_text, build_payload
from web_router.types import SearchRequest, SearchResponse, SearchResult

_MAPPED_RESULT_FIELDS = frozenset({"url", "title", "description", "contents", "page_age"})


class YouProvider(HttpSearchProvider):
    """Routes searches to the You.com Search API."""

    name: ClassVar[str] = PROVIDER_YOU
    supported_parameters: ClassVar[frozenset[str]] = frozenset(YOU_PARAM_NAMES) | {"search_depth"}

    base_url: ClassVar[str] = YOU_BASE_URL
    search_path: ClassVar[str] = YOU_SEARCH_PATH
    # Widened from the base's default so YouMcpProvider, which subclasses this
    # for its normalization, can declare itself keyless.
    env_api_key: ClassVar[str | None] = ENV_YOU_API_KEY
    api_key_header: ClassVar[str] = YOU_API_KEY_HEADER

    def _build_payload(self, request: SearchRequest) -> dict[str, Any]:
        # A depth of None, or "lite" which needs no extraction, both map to None
        # here and are dropped rather than sent.
        extraction = YOU_EXTRACTION_BY_DEPTH.get(request.search_depth) if request.search_depth else None
        return build_payload(request, YOU_PARAM_NAMES, query=request.query, extraction=extraction)

    def _to_response(self, request: SearchRequest, payload: dict[str, Any]) -> SearchResponse:
        sections = payload.get("results") or {}
        metadata = payload.get("metadata") or {}

        results = [
            self._to_result(hit, section) for section in YOU_RESULT_SECTIONS for hit in sections.get(section) or []
        ]

        return SearchResponse(
            provider=self.name,
            query=request.query,
            results=results,
            answer=self._to_answer(sections.get("knowledge") or []),
            request_id=metadata.get("search_uuid"),
            raw=payload,
        )

    @staticmethod
    def _to_result(hit: dict[str, Any], section: str) -> SearchResult:
        contents = hit.get("contents") or {}
        description = hit.get("description") or ""
        # Page body if extraction ran, else the snippets the API always returns.
        body = contents.get("markdown") or "\n\n".join(contents.get("highlights") or hit.get("snippets") or [])
        return SearchResult(
            url=hit.get("url", ""),
            title=hit.get("title") or "",
            description=description,
            content=best_text(body, description),
            published_date=hit.get("page_age"),
            extra={"section": section, **{k: v for k, v in hit.items() if k not in _MAPPED_RESULT_FIELDS}},
        )

    @staticmethod
    def _to_answer(knowledge: list[dict[str, Any]]) -> str | None:
        """Surface a knowledge-panel answer when the API returned one."""
        for item in knowledge:
            if item.get("type") == YOU_KNOWLEDGE_ANSWER_TYPE and item.get("description"):
                description: str = item["description"]
                return description
        return None
