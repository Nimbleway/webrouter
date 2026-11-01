"""Perplexity providers, over the Search API and the Agent (Responses) API.

:class:`PerplexityProvider` is the ``/search`` endpoint, which returns ranked
web results. :class:`PerplexityResearchProvider` runs research as a background
job on ``/v1/responses``, the Agent API that replaced deep research on the chat
endpoint, and :class:`PerplexityAnswerProvider` asks the same endpoint for one
answer in one call. Install with the ``search``, ``research`` or ``answer`` extra. Reads ``PERPLEXITY_API_KEY`` unless a
key is passed explicitly.

Search is slim by design: only ``max_results`` is mapped. Everything else is
reachable through ``provider_options``; mapping one properly is a welcome PR --
see CONTRIBUTING.md.
"""

from __future__ import annotations

from typing import Any, ClassVar
from urllib.parse import quote

from web_router.constants import (
    ENV_PERPLEXITY_API_KEY,
    PERPLEXITY_API_KEY_HEADER,
    PERPLEXITY_API_KEY_PREFIX,
    PERPLEXITY_BASE_URL,
    PERPLEXITY_INCOMPLETE_STATUS,
    PERPLEXITY_OUTPUT_MESSAGE,
    PERPLEXITY_OUTPUT_SEARCH_RESULTS,
    PERPLEXITY_OUTPUT_TEXT,
    PERPLEXITY_PARAM_NAMES,
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
    PERPLEXITY_ANSWER_COUNTRY_FIELD,
    PERPLEXITY_ANSWER_DOMAIN_FIELD,
    PERPLEXITY_ANSWER_EFFORT,
    PERPLEXITY_ANSWER_EFFORT_FIELD,
    PERPLEXITY_ANSWER_EXCLUDE_PREFIX,
    PERPLEXITY_ANSWER_LOCATION_FIELD,
    PERPLEXITY_ANSWER_MARKER,
    PERPLEXITY_ANSWER_RECENCY,
    PERPLEXITY_ANSWER_RECENCY_FIELD,
    PERPLEXITY_ANSWER_TOOL,
)
from web_router.providers._http import HttpAnswerProvider, HttpResearchProvider, HttpSearchProvider
from web_router.providers._utils import build_payload, compact, error_detail
from web_router.types import (
    AnswerRequest,
    AnswerResponse,
    Citation,
    ResearchProgress,
    ResearchRequest,
    ResearchResponse,
    SearchRequest,
    SearchResponse,
    SearchResult,
)

_MAPPED_RESULT_FIELDS = frozenset({"url", "title", "snippet", "date"})
_MAPPED_SOURCE_FIELDS = frozenset({"url", "title", "snippet"})


class PerplexityProvider(HttpSearchProvider):
    """Routes searches to the Perplexity Search API."""

    name: ClassVar[str] = PROVIDER_PERPLEXITY
    supported_parameters: ClassVar[frozenset[str]] = frozenset(PERPLEXITY_PARAM_NAMES)

    base_url: ClassVar[str] = PERPLEXITY_BASE_URL
    search_path: ClassVar[str] = PERPLEXITY_SEARCH_PATH
    env_api_key: ClassVar[str] = ENV_PERPLEXITY_API_KEY
    api_key_header: ClassVar[str] = PERPLEXITY_API_KEY_HEADER
    api_key_prefix: ClassVar[str] = PERPLEXITY_API_KEY_PREFIX

    def _build_payload(self, request: SearchRequest) -> dict[str, Any]:
        return build_payload(request, PERPLEXITY_PARAM_NAMES, query=request.query)

    def _to_response(self, request: SearchRequest, payload: dict[str, Any]) -> SearchResponse:
        return SearchResponse(
            provider=self.name,
            query=request.query,
            results=[self._to_result(hit) for hit in payload.get("results") or []],
            request_id=payload.get("id"),
            raw=payload,
        )

    @staticmethod
    def _to_result(hit: dict[str, Any]) -> SearchResult:
        snippet = hit.get("snippet") or ""
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
            answer=_text(output) or None,
            citations=_citations(output),
            outcome="partial" if payload.get("status") == PERPLEXITY_INCOMPLETE_STATUS else "complete",
            request_id=payload.get("id"),
            raw=[payload],
        )


class PerplexityAnswerProvider(HttpAnswerProvider):
    """Answers a question in one call on Perplexity's Agent API, at its ``fast`` effort.

    The same ``POST /v1/responses`` research uses, without ``background``, so
    the call returns the finished response. The answer cites its sources inline
    as ``[2]``, the ``id`` of a ``search_results`` entry; a citation whose
    marker appears in the answer carries it in ``field``, and one the answer
    never cites has ``None``. A response Perplexity reports ``incomplete`` is
    ``partial``. The response states its own cost under ``usage.cost``, kept
    in ``raw``.

    The filters go to the ``web_search`` tool, configured only when one is set:
    the domain lists share ``search_domain_filter`` (an excluded domain
    prefixed ``-``), ``recency`` is ``search_recency_filter`` and ``country``
    is ``user_location.country``. ``locale`` has no field and is refused.
    Another effort level goes through ``provider_options``.
    """

    name: ClassVar[str] = PROVIDER_PERPLEXITY
    supported_parameters: ClassVar[frozenset[str]] = frozenset(
        {"include_domains", "exclude_domains", "recency", "country"}
    )

    base_url: ClassVar[str] = PERPLEXITY_BASE_URL
    answer_path: ClassVar[str] = PERPLEXITY_RESEARCH_PATH
    env_api_key: ClassVar[str] = ENV_PERPLEXITY_API_KEY
    api_key_header: ClassVar[str] = PERPLEXITY_API_KEY_HEADER
    api_key_prefix: ClassVar[str] = PERPLEXITY_API_KEY_PREFIX

    def _build_payload(self, request: AnswerRequest) -> dict[str, Any]:
        tool = self._web_search(request)
        return build_payload(
            request,
            input=request.query,
            tools=[tool] if tool else None,
            **{PERPLEXITY_ANSWER_EFFORT_FIELD: PERPLEXITY_ANSWER_EFFORT},
        )

    @staticmethod
    def _web_search(request: AnswerRequest) -> dict[str, Any] | None:
        """The ``web_search`` tool carrying the request's filters, or ``None`` when it sets none."""
        domains = [
            *(request.include_domains or []),
            *(PERPLEXITY_ANSWER_EXCLUDE_PREFIX + domain for domain in request.exclude_domains or []),
        ]
        filters = compact(
            {
                PERPLEXITY_ANSWER_DOMAIN_FIELD: domains or None,
                PERPLEXITY_ANSWER_RECENCY_FIELD: PERPLEXITY_ANSWER_RECENCY[request.recency]
                if request.recency
                else None,
            }
        )
        location = {PERPLEXITY_ANSWER_COUNTRY_FIELD: request.country} if request.country else None
        tool = compact({"filters": filters or None, PERPLEXITY_ANSWER_LOCATION_FIELD: location})
        return {"type": PERPLEXITY_ANSWER_TOOL, **tool} if tool else None

    def _to_response(self, request: AnswerRequest, payload: dict[str, Any]) -> AnswerResponse:
        output = payload.get("output") or []
        answer = _text(output)
        return AnswerResponse(
            provider=self.name,
            query=request.query,
            answer=answer,
            citations=_citations(output, cited_in=answer),
            outcome="partial" if payload.get("status") == PERPLEXITY_INCOMPLETE_STATUS else "complete",
            request_id=payload.get("id"),
            raw=[payload],
        )


def _text(output: list[dict[str, Any]]) -> str:
    """The written answer: every ``output_text`` part of the ``message`` output, joined."""
    return "".join(
        part.get("text") or ""
        for item in output
        if item.get("type") == PERPLEXITY_OUTPUT_MESSAGE
        for part in item.get("content") or []
        if part.get("type") == PERPLEXITY_OUTPUT_TEXT
    )


def _citations(output: list[dict[str, Any]], *, cited_in: str | None = None) -> list[Citation]:
    """The sources behind the answer, from the ``search_results`` output.

    With ``cited_in``, a source whose ``[id]`` marker appears in that text
    carries the marker in ``field``.
    """
    citations = []
    for item in output:
        if item.get("type") != PERPLEXITY_OUTPUT_SEARCH_RESULTS:
            continue
        for result in item.get("results") or []:
            marker = PERPLEXITY_ANSWER_MARKER.format(id=result.get("id"))
            citations.append(
                Citation(
                    url=result.get("url", ""),
                    title=result.get("title") or "",
                    snippet=result.get("snippet") or "",
                    field=marker if cited_in and result.get("id") is not None and marker in cited_in else None,
                    extra={key: value for key, value in result.items() if key not in _MAPPED_SOURCE_FIELDS},
                )
            )
    return citations
