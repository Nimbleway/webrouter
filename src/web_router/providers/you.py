"""You.com providers, over the Search API and the Research API.

You.com publishes no Python SDK, so this talks to the REST endpoints directly.
:class:`YouProvider` searches; :class:`YouResearchProvider` runs research as a
background task. Install with the ``search`` extra or the ``research`` extra.
Reads ``YOU_API_KEY`` unless a key is passed explicitly.

web-router sends You.com the minimum a search needs: the query, ``max_results``
and ``search_depth``. Everything else its API offers is reachable through
``provider_options``, and mapping one properly is a welcome PR -- see
CONTRIBUTING.md.
"""

from __future__ import annotations

from typing import Any, ClassVar
from urllib.parse import quote

from web_router.constants import (
    ENV_YOU_API_KEY,
    PROVIDER_YOU,
    YOU_API_KEY_HEADER,
    YOU_BASE_URL,
    YOU_EXTRACTION_BY_DEPTH,
    YOU_KNOWLEDGE_ANSWER_TYPE,
    YOU_PARAM_NAMES,
    YOU_RESEARCH_BACKGROUND,
    YOU_RESEARCH_BASE_URL,
    YOU_RESEARCH_EFFORTS,
    YOU_RESEARCH_PATH,
    YOU_RESEARCH_STATES,
    YOU_RESEARCH_STATUS_PATH,
    YOU_RESULT_SECTIONS,
    YOU_SEARCH_PATH,
)
from web_router.providers._http import HttpResearchProvider, HttpSearchProvider
from web_router.providers._utils import best_text, build_payload, error_detail
from web_router.types import (
    Citation,
    ResearchProgress,
    ResearchRequest,
    ResearchResponse,
    SearchRequest,
    SearchResponse,
    SearchResult,
)

_MAPPED_RESULT_FIELDS = frozenset({"url", "title", "description", "contents", "page_age"})
_MAPPED_SOURCE_FIELDS = frozenset({"url", "title", "snippets"})


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


class YouResearchProvider(HttpResearchProvider):
    """Runs agentic research as a background task on the You.com Research API.

    ``POST /v1/research`` with ``background`` returns the task ``queued`` under
    ``task_id``, and ``GET /v1/research/{task_id}`` reports it, under ``id``,
    through ``running`` to ``completed`` -- verified live on 2026-10-16. The
    answer is ``result.output.content``, markdown with numbered citation
    markers, and its sources are ``result.output.sources``.

    ``effort`` maps onto ``research_effort``. You.com offers no way to stop a
    task, so :meth:`cancel` reports ``False``. Its API reports no early stop, so
    every finished run is ``complete``. Structured output is not mapped until a
    live run has checked it, so ``output_schema`` raises rather than coming back
    as prose, and a schema sent through ``provider_options`` leaves its result in
    ``raw``.
    """

    name: ClassVar[str] = PROVIDER_YOU
    supported_parameters: ClassVar[frozenset[str]] = frozenset({"effort"})

    base_url: ClassVar[str] = YOU_RESEARCH_BASE_URL
    env_api_key: ClassVar[str] = ENV_YOU_API_KEY
    api_key_header: ClassVar[str] = YOU_API_KEY_HEADER

    def _start(self, request: ResearchRequest) -> ResearchProgress:
        return self._progress(request, self._post(YOU_RESEARCH_PATH, self._build_payload(request)))

    async def _astart(self, request: ResearchRequest) -> ResearchProgress:
        return self._progress(request, await self._apost(YOU_RESEARCH_PATH, self._build_payload(request)))

    def _fetch(self, request: ResearchRequest, job_id: str) -> ResearchProgress:
        return self._progress(request, self._get(self._path(job_id)))

    async def _afetch(self, request: ResearchRequest, job_id: str) -> ResearchProgress:
        return self._progress(request, await self._aget(self._path(job_id)))

    @staticmethod
    def _path(job_id: str) -> str:
        return YOU_RESEARCH_STATUS_PATH.format(task_id=quote(job_id, safe=""))

    def _build_payload(self, request: ResearchRequest) -> dict[str, Any]:
        effort = YOU_RESEARCH_EFFORTS[request.effort] if request.effort else None
        return build_payload(request, input=request.query, research_effort=effort, background=YOU_RESEARCH_BACKGROUND)

    def _progress(self, request: ResearchRequest, payload: dict[str, Any]) -> ResearchProgress:
        state = self.state_of(str(payload.get("status")), YOU_RESEARCH_STATES)
        return ResearchProgress(
            # The start call names the task `task_id`; the status call names it `id`.
            job_id=str(payload.get("task_id") or payload.get("id") or ""),
            state=state,
            # The status call carries the finished run, so it needs no second fetch.
            response=self._to_response(request, payload) if state == "completed" else None,
            detail=error_detail(payload.get("error")),
        )

    def _to_response(self, request: ResearchRequest, payload: dict[str, Any]) -> ResearchResponse:
        output = (payload.get("result") or {}).get("output") or {}
        content = output.get("content")
        return ResearchResponse(
            provider=self.name,
            query=request.query,
            # Only prose is read: a schema sent through `provider_options` comes
            # back in a shape no live run has checked, so it stays in `raw`.
            answer=content if isinstance(content, str) and content else None,
            citations=[self._to_citation(source) for source in output.get("sources") or []],
            request_id=payload.get("id") or payload.get("task_id"),
            raw=[payload],
        )

    @staticmethod
    def _to_citation(source: dict[str, Any]) -> Citation:
        return Citation(
            url=source.get("url", ""),
            title=source.get("title") or "",
            snippet="\n\n".join(snippet for snippet in source.get("snippets") or [] if snippet),
            extra={key: value for key, value in source.items() if key not in _MAPPED_SOURCE_FIELDS},
        )
