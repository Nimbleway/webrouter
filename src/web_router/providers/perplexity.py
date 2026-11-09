"""Perplexity providers, over the Search API and the Agent (Responses) API.

:class:`PerplexityProvider` is the ``/search`` endpoint, which returns ranked
web results. :class:`PerplexityResearchProvider` runs research as a background
job on ``/v1/responses``, the Agent API that replaced deep research on the chat
endpoint; :class:`PerplexityAnswerProvider`, which asks the same endpoint for
one answer in one call, lives in :mod:`web_router.providers.perplexity_answer`.
Install with the ``search``, ``research`` or ``answer`` extra. Reads
``PERPLEXITY_API_KEY`` unless a key is passed explicitly.

Search maps every shared option: the domain filters onto one
``search_domain_filter``, ``country``, ``locale`` as ``search_language_filter``,
``recency``, ``search_depth`` as a per-page token budget (``lite`` and ``fast``;
``deep`` is refused) and ``effort`` as ``search_type``. Everything else is
reachable through ``provider_options``.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar
from urllib.parse import quote

from web_router.constants import (
    ENV_PERPLEXITY_API_KEY,
    PERPLEXITY_API_KEY_HEADER,
    PERPLEXITY_API_KEY_PREFIX,
    PERPLEXITY_BASE_URL,
    PERPLEXITY_INCOMPLETE_STATUS,
    PERPLEXITY_RESEARCH_BACKGROUND,
    PERPLEXITY_RESEARCH_CANCEL_PATH,
    PERPLEXITY_RESEARCH_DEFAULT_PRESET,
    PERPLEXITY_RESEARCH_PATH,
    PERPLEXITY_RESEARCH_PRESETS,
    PERPLEXITY_RESEARCH_STATES,
    PERPLEXITY_RESEARCH_STATUS_PATH,
    PERPLEXITY_SEARCH_PATH,
    PROVIDER_PERPLEXITY,
)
from web_router.constants_perplexity import (
    MSG_PERPLEXITY_LOCALE,
    MSG_PERPLEXITY_SEARCH_DOMAINS,
    PERPLEXITY_ANSWER_DOMAIN_FIELD,
    PERPLEXITY_ANSWER_RECENCY,
    PERPLEXITY_ANSWER_RECENCY_FIELD,
    PERPLEXITY_LANGUAGE_FIELD,
    PERPLEXITY_LANGUAGE_PATTERN,
    PERPLEXITY_MAX_DOMAINS,
    PERPLEXITY_PAGE_BUDGET_BY_DEPTH,
    PERPLEXITY_PAGE_BUDGET_FIELD,
    PERPLEXITY_SEARCH_NAMES,
    PERPLEXITY_SEARCH_TYPE_BY_EFFORT,
    PERPLEXITY_SEARCH_TYPE_FIELD,
)
from web_router.providers._http import HttpResearchProvider, HttpSearchProvider
from web_router.providers._search_text import lite_body, refused_domains
from web_router.providers._utils import build_payload, error_detail, mapped
from web_router.providers.perplexity_answer import (
    PerplexityAnswerProvider as PerplexityAnswerProvider,
)
from web_router.providers.perplexity_answer import (
    domain_filter,
    output_citations,
    output_text,
)
from web_router.types import (
    ResearchProgress,
    ResearchRequest,
    ResearchResponse,
    SearchDepth,
    SearchRequest,
    SearchResponse,
    SearchResult,
)

_MAPPED_RESULT_FIELDS = frozenset({"url", "title", "snippet", "date"})
_LANGUAGE = re.compile(PERPLEXITY_LANGUAGE_PATTERN)


class PerplexityProvider(HttpSearchProvider):
    """Routes searches to the Perplexity Search API."""

    name: ClassVar[str] = PROVIDER_PERPLEXITY
    supported_parameters: ClassVar[frozenset[str]] = frozenset(
        {*PERPLEXITY_SEARCH_NAMES, "include_domains", "exclude_domains", "locale", "recency", "search_depth", "effort"}
    )
    depth_levels: ClassVar[frozenset[SearchDepth]] = frozenset(PERPLEXITY_PAGE_BUDGET_BY_DEPTH)
    refusal_reasons: ClassVar[dict[str, str]] = {
        **dict.fromkeys(("include_domains", "exclude_domains"), MSG_PERPLEXITY_SEARCH_DOMAINS),
        "locale": MSG_PERPLEXITY_LOCALE,
    }

    base_url: ClassVar[str] = PERPLEXITY_BASE_URL
    search_path: ClassVar[str] = PERPLEXITY_SEARCH_PATH
    env_api_key: ClassVar[str] = ENV_PERPLEXITY_API_KEY
    api_key_header: ClassVar[str] = PERPLEXITY_API_KEY_HEADER
    api_key_prefix: ClassVar[str] = PERPLEXITY_API_KEY_PREFIX

    @classmethod
    def refused_options(cls, request: SearchRequest) -> dict[str, Any]:
        """Both domain lists, more than its cap of domains, or a ``locale`` that is not a bare language code."""
        refused: dict[str, Any] = refused_domains(request, limit=PERPLEXITY_MAX_DOMAINS)
        if request.locale is not None and not _LANGUAGE.fullmatch(request.locale):
            refused["locale"] = request.locale
        return refused

    def _build_payload(self, request: SearchRequest) -> dict[str, Any]:
        return build_payload(
            request,
            PERPLEXITY_SEARCH_NAMES,
            query=request.query,
            **{
                PERPLEXITY_ANSWER_DOMAIN_FIELD: domain_filter(request),
                PERPLEXITY_LANGUAGE_FIELD: [request.locale.lower()] if request.locale else None,
                PERPLEXITY_ANSWER_RECENCY_FIELD: mapped(PERPLEXITY_ANSWER_RECENCY, request.recency),
                PERPLEXITY_PAGE_BUDGET_FIELD: mapped(PERPLEXITY_PAGE_BUDGET_BY_DEPTH, request.search_depth),
                PERPLEXITY_SEARCH_TYPE_FIELD: mapped(PERPLEXITY_SEARCH_TYPE_BY_EFFORT, request.effort),
            },
        )

    def _to_response(self, request: SearchRequest, payload: dict[str, Any]) -> SearchResponse:
        return SearchResponse(
            provider=self.name,
            query=request.query,
            results=[self._to_result(hit, request.search_depth) for hit in payload.get("results") or []],
            request_id=payload.get("id"),
            raw=payload,
        )

    @staticmethod
    def _to_result(hit: dict[str, Any], depth: SearchDepth | None = None) -> SearchResult:
        snippet = lite_body(hit.get("snippet") or "", depth)
        return SearchResult(
            url=hit.get("url", ""),
            title=hit.get("title") or "",
            description=snippet,
            content=snippet,
            published_date=hit.get("date"),
            extra={k: v for k, v in hit.items() if k not in _MAPPED_RESULT_FIELDS},
        )


class PerplexityResearchProvider(HttpResearchProvider):
    """Runs agentic research as a background job on Perplexity's Agent API.

    ``POST /v1/responses`` with ``background`` returns the run ``queued``,
    ``GET /v1/responses/{id}`` reports it through ``in_progress`` to
    ``completed``, and ``POST /v1/responses/{id}/cancel`` stops it -- all
    verified live on 2026-09-30. The answer is the run's ``message`` output and
    its sources are the ``search_results`` output.

    ``effort`` maps onto Perplexity's presets. A run Perplexity reports
    ``incomplete`` is the one case this backend calls ``partial``, because the
    API itself says the run stopped early. Structured output is not mapped
    until a live run has checked it, so ``output_schema`` raises rather than
    coming back as prose.
    """

    name: ClassVar[str] = PROVIDER_PERPLEXITY
    supported_parameters: ClassVar[frozenset[str]] = frozenset({"effort"})

    base_url: ClassVar[str] = PERPLEXITY_BASE_URL
    research_path: ClassVar[str] = PERPLEXITY_RESEARCH_PATH
    status_path: ClassVar[str] = PERPLEXITY_RESEARCH_STATUS_PATH
    cancel_path: ClassVar[str] = PERPLEXITY_RESEARCH_CANCEL_PATH
    env_api_key: ClassVar[str] = ENV_PERPLEXITY_API_KEY
    api_key_header: ClassVar[str] = PERPLEXITY_API_KEY_HEADER
    api_key_prefix: ClassVar[str] = PERPLEXITY_API_KEY_PREFIX

    def _start(self, request: ResearchRequest) -> ResearchProgress:
        return self._progress(request, self._post(self.research_path, self._build_payload(request)))

    async def _astart(self, request: ResearchRequest) -> ResearchProgress:
        return self._progress(request, await self._apost(self.research_path, self._build_payload(request)))

    def _fetch(self, request: ResearchRequest, job_id: str) -> ResearchProgress:
        return self._progress(request, self._get(self._path(self.status_path, job_id)))

    async def _afetch(self, request: ResearchRequest, job_id: str) -> ResearchProgress:
        return self._progress(request, await self._aget(self._path(self.status_path, job_id)))

    def _cancel(self, job_id: str) -> bool:
        self._post(self._path(self.cancel_path, job_id), {})
        return True

    async def _acancel(self, job_id: str) -> bool:
        await self._apost(self._path(self.cancel_path, job_id), {})
        return True

    @staticmethod
    def _path(template: str, job_id: str) -> str:
        return template.format(response_id=quote(job_id, safe=""))

    def _build_payload(self, request: ResearchRequest) -> dict[str, Any]:
        preset = PERPLEXITY_RESEARCH_PRESETS[request.effort] if request.effort else PERPLEXITY_RESEARCH_DEFAULT_PRESET
        return build_payload(request, input=request.query, preset=preset, background=PERPLEXITY_RESEARCH_BACKGROUND)

    def _progress(self, request: ResearchRequest, payload: dict[str, Any]) -> ResearchProgress:
        state = self.state_of(str(payload.get("status")), PERPLEXITY_RESEARCH_STATES)
        error = payload.get("error")
        return ResearchProgress(
            job_id=str(payload.get("id") or ""),
            state=state,
            # The status call carries the finished run, so it needs no second fetch.
            response=self._to_response(request, payload) if state == "completed" else None,
            detail=error_detail(error),
        )

    def _to_response(self, request: ResearchRequest, payload: dict[str, Any]) -> ResearchResponse:
        output = payload.get("output") or []
        return ResearchResponse(
            provider=self.name,
            query=request.query,
            answer=output_text(output) or None,
            citations=output_citations(output),
            outcome="partial" if payload.get("status") == PERPLEXITY_INCOMPLETE_STATUS else "complete",
            request_id=payload.get("id"),
            raw=[payload],
        )
