"""You.com providers, over the Search API and the Research API.

You.com publishes no Python SDK, so this talks to the REST endpoints directly.
:class:`YouProvider` searches; :class:`YouResearchProvider` runs research as a
background task; :class:`YouAnswerProvider` answers one question in one call.
Install with the ``search``, ``research`` or ``answer`` extra.
Reads ``YOU_API_KEY`` unless a key is passed explicitly. The research and
answer classes live in :mod:`web_router.providers.you_research` and
:mod:`web_router.providers.you_answer` and are re-exported here.

Search maps every shared option but ``effort``, which You.com has no tier for.
``country`` and ``locale`` reach fixed lists of 36 countries and 51 languages,
and a value off them is refused before the call. Everything else its API offers
is reachable through ``provider_options``.
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
    YOU_RESULT_SECTIONS,
    YOU_SEARCH_PATH,
)
from web_router.constants_you import (
    MSG_YOU_SEARCH_DOMAINS_EXCLUSIVE,
    MSG_YOU_SEARCH_VALUES,
    YOU_COUNTRIES,
    YOU_FRESHNESS_FIELD,
    YOU_RECENCY,
    YOU_SEARCH_PARAM_NAMES,
    YOU_SEARCH_PARAMETERS,
)
from web_router.providers._http import HttpSearchProvider
from web_router.providers._language import language_tag, off_the_lists
from web_router.providers._search_text import lite_body, refused_domains
from web_router.providers._utils import best_text, build_payload, mapped
from web_router.providers.you_answer import YouAnswerProvider as YouAnswerProvider
from web_router.providers.you_research import YouResearchProvider as YouResearchProvider
from web_router.types import (
    SearchDepth,
    SearchRequest,
    SearchResponse,
    SearchResult,
)

_MAPPED_RESULT_FIELDS = frozenset({"url", "title", "description", "contents", "page_age"})


class YouProvider(HttpSearchProvider):
    """Routes searches to the You.com Search API."""

    name: ClassVar[str] = PROVIDER_YOU
    supported_parameters: ClassVar[frozenset[str]] = YOU_SEARCH_PARAMETERS
    refusal_reasons: ClassVar[dict[str, str]] = {
        **dict.fromkeys(("country", "locale"), MSG_YOU_SEARCH_VALUES),
        **dict.fromkeys(("include_domains", "exclude_domains"), MSG_YOU_SEARCH_DOMAINS_EXCLUSIVE),
    }

    base_url: ClassVar[str] = YOU_BASE_URL
    search_path: ClassVar[str] = YOU_SEARCH_PATH
    # Widened from the base's default so YouMcpProvider, which subclasses this
    # for its normalization, can declare itself keyless.
    env_api_key: ClassVar[str | None] = ENV_YOU_API_KEY
    api_key_header: ClassVar[str] = YOU_API_KEY_HEADER

    @classmethod
    def refused_options(cls, request: SearchRequest) -> dict[str, Any]:
        """Both domain lists at once, or a ``country`` or ``locale`` off its lists; the MCP tool is held to them too."""
        return {**refused_domains(request), **off_the_lists(request, YOU_COUNTRIES)}

    def _build_payload(self, request: SearchRequest) -> dict[str, Any]:
        # A depth of None, or "lite" which needs no extraction, both map to None
        # here and are dropped rather than sent.
        extraction = mapped(YOU_EXTRACTION_BY_DEPTH, request.search_depth)
        return build_payload(
            request,
            YOU_SEARCH_PARAM_NAMES,
            query=request.query,
            extraction=extraction,
            country=request.country.upper() if request.country else None,
            language=language_tag(request.locale) if request.locale else None,
            **{YOU_FRESHNESS_FIELD: mapped(YOU_RECENCY, request.recency)},
        )

    def _to_response(self, request: SearchRequest, payload: dict[str, Any]) -> SearchResponse:
        sections = payload.get("results") or {}
        metadata = payload.get("metadata") or {}

        results = [
            self._to_result(hit, section, request.search_depth)
            for section in YOU_RESULT_SECTIONS
            for hit in sections.get(section) or []
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
    def _to_result(hit: dict[str, Any], section: str, depth: SearchDepth | None) -> SearchResult:
        contents = hit.get("contents") or {}
        description = hit.get("description") or ""
        # Page body if extraction ran, else the snippets the API always returns.
        body = contents.get("markdown") or "\n\n".join(contents.get("highlights") or hit.get("snippets") or [])
        return SearchResult(
            url=hit.get("url", ""),
            title=hit.get("title") or "",
            description=description,
            content=lite_body(best_text(body, description), depth),
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
