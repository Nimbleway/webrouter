"""Linkup providers, over the Search, Fetch and Research APIs.

Talks to the REST endpoints directly: Linkup's SDK would add a dependency for the
handful of fields used here. Install with the ``search`` extra,
``[extract]``, ``[research]`` or ``[answer]``. Reads ``LINKUP_API_KEY`` unless a
key is passed explicitly.

Slim by design. A search maps ``max_results`` and the domain filters; an extract
maps ``format``; a research run maps ``effort``; an answer maps the domain
filters and ``recency``. A search refuses
``search_depth``, because Linkup's ``depth`` is retrieval effort rather than page
content (see ``LINKUP_DEFAULT_DEPTH``). Everything else -- its depths, dates,
images, a research ``mode`` -- is reachable through ``provider_options``, and mapping one properly is a welcome PR;
see CONTRIBUTING.md.
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any, ClassVar
from urllib.parse import quote

from web_router.constants import (
    ENV_LINKUP_API_KEY,
    LINKUP_API_KEY_HEADER,
    LINKUP_API_KEY_PREFIX,
    LINKUP_BASE_URL,
    LINKUP_DEFAULT_DEPTH,
    LINKUP_EXTRACT_DEFAULT_FORMAT,
    LINKUP_EXTRACT_FORMATS,
    LINKUP_EXTRACT_HTML_CONTENT_TYPE,
    LINKUP_EXTRACT_RESULT_FIELDS,
    LINKUP_FETCH_PATH,
    LINKUP_PARAM_NAMES,
    LINKUP_RESEARCH_DEPTHS,
    LINKUP_RESEARCH_OUTPUT_TYPE,
    LINKUP_RESEARCH_PATH,
    LINKUP_RESEARCH_SCHEMA_FIELD,
    LINKUP_RESEARCH_STATES,
    LINKUP_RESEARCH_STATUS_PATH,
    LINKUP_RESEARCH_STRUCTURED_OUTPUT_TYPE,
    LINKUP_SEARCH_OUTPUT_TYPE,
    LINKUP_SEARCH_PATH,
    MSG_LINKUP_NO_HTML,
    PROVIDER_LINKUP,
)
from web_router.constants_linkup import (
    LINKUP_ANSWER_DATE_FORMAT,
    LINKUP_ANSWER_FROM_DATE_FIELD,
    LINKUP_ANSWER_OUTPUT_TYPE,
    LINKUP_ANSWER_PARAM_NAMES,
    LINKUP_ANSWER_RECENCY_DAYS,
)
from web_router.providers._http import (
    HttpAnswerProvider,
    HttpExtractProvider,
    HttpResearchProvider,
    HttpSearchProvider,
)
from web_router.providers._utils import as_snippet, build_payload, error_detail, today
from web_router.types import (
    EXTRACT_PARAMETERS,
    AnswerRequest,
    AnswerResponse,
    Citation,
    ExtractedDocument,
    ExtractFailure,
    ExtractFormat,
    ExtractRequest,
    ExtractResponse,
    ResearchProgress,
    ResearchRequest,
    ResearchResponse,
    SearchRequest,
    SearchResponse,
    SearchResult,
)

_MAPPED_RESULT_FIELDS = frozenset({"url", "name", "content"})
_MAPPED_SOURCE_FIELDS = frozenset({"url", "name", "snippet"})


class LinkupProvider(HttpSearchProvider):
    """Routes searches to the Linkup Search API.

    Linkup returns a snippet per result and no date or relevance score, so
    ``published_date`` and ``score`` stay ``None``. An image result, which
    Linkup returns only when ``includeImages`` is sent through
    ``provider_options``, carries a title and a URL and no text; it is kept
    rather than dropped, with ``type`` in ``extra`` saying what it is.
    """

    name: ClassVar[str] = PROVIDER_LINKUP
    supported_parameters: ClassVar[frozenset[str]] = frozenset(LINKUP_PARAM_NAMES)

    base_url: ClassVar[str] = LINKUP_BASE_URL
    search_path: ClassVar[str] = LINKUP_SEARCH_PATH
    env_api_key: ClassVar[str] = ENV_LINKUP_API_KEY
    api_key_header: ClassVar[str] = LINKUP_API_KEY_HEADER
    api_key_prefix: ClassVar[str] = LINKUP_API_KEY_PREFIX

    def _build_payload(self, request: SearchRequest) -> dict[str, Any]:
        return build_payload(
            request,
            LINKUP_PARAM_NAMES,
            q=request.query,
            depth=LINKUP_DEFAULT_DEPTH,
            outputType=LINKUP_SEARCH_OUTPUT_TYPE,
        )

    def _to_response(self, request: SearchRequest, payload: dict[str, Any]) -> SearchResponse:
        return SearchResponse(
            provider=self.name,
            query=request.query,
            results=[self._to_result(hit) for hit in payload.get("results") or []],
            raw=payload,
        )

    @staticmethod
    def _to_result(hit: dict[str, Any]) -> SearchResult:
        content = hit.get("content") or ""
        return SearchResult(
            url=hit.get("url", ""),
            title=hit.get("name") or "",
            description=as_snippet(content),
            content=content,
            extra={k: v for k, v in hit.items() if k not in _MAPPED_RESULT_FIELDS},
        )


class LinkupExtractProvider(HttpExtractProvider):
    """Fetches page contents through the Linkup Fetch API.

    Linkup returns no title, so ``title`` stays empty. It always returns
    markdown and adds the page's raw content on request, which is the only
    other representation it has: asking for plain text raises, and asking for
    HTML on a page whose raw content is not HTML (a PDF has none) is reported as
    a failure rather than answered with the markdown.
    """

    name: ClassVar[str] = PROVIDER_LINKUP
    supported_parameters: ClassVar[frozenset[str]] = EXTRACT_PARAMETERS
    supported_formats: ClassVar[frozenset[ExtractFormat]] = frozenset(LINKUP_EXTRACT_FORMATS)
    default_format: ClassVar[ExtractFormat] = LINKUP_EXTRACT_DEFAULT_FORMAT

    base_url: ClassVar[str] = LINKUP_BASE_URL
    extract_path: ClassVar[str] = LINKUP_FETCH_PATH
    env_api_key: ClassVar[str] = ENV_LINKUP_API_KEY
    api_key_header: ClassVar[str] = LINKUP_API_KEY_HEADER
    api_key_prefix: ClassVar[str] = LINKUP_API_KEY_PREFIX

    def _build_payload(self, request: ExtractRequest, urls: list[str]) -> dict[str, Any]:
        return build_payload(request, url=urls[0], **LINKUP_EXTRACT_FORMATS[self.format_of(request)])

    def _to_response(self, request: ExtractRequest, urls: list[str], payload: dict[str, Any]) -> ExtractResponse:
        body_format = self.format_of(request)
        content = payload.get(LINKUP_EXTRACT_RESULT_FIELDS[body_format])

        # Linkup answers 200 with the markdown and leaves raw content out for a
        # page it has none for, so a missing body is a refusal to report.
        if body_format == "html" and (not content or payload.get("contentType") != LINKUP_EXTRACT_HTML_CONTENT_TYPE):
            error = MSG_LINKUP_NO_HTML.format(content_type=payload.get("contentType"))
            return ExtractResponse(
                provider=self.name,
                failures=[ExtractFailure(url=urls[0], error=error)],
                raw=[payload],
            )

        document = ExtractedDocument(
            url=urls[0],
            content=content or "",
            format=body_format,
            extra={k: v for k, v in payload.items() if k not in LINKUP_EXTRACT_RESULT_FIELDS.values()},
        )
        return ExtractResponse(provider=self.name, documents=[document], raw=[payload])


class LinkupResearchProvider(HttpResearchProvider):
    """Runs agentic research through the Linkup Research API.

    A run is asynchronous: ``POST /v1/research`` returns an id and a ``pending``
    status, and ``GET /v1/research/{id}`` reports the run until it is
    ``completed`` or ``failed``, carrying the answer with it. Linkup has no call
    to stop a run, so :meth:`cancel` reports ``False``, and no field saying a
    run stopped early, so ``outcome`` is always ``complete``.

    ``effort`` maps onto ``reasoningDepth``, and ``output_schema`` onto
    Linkup's structured output, whose result is the schema's object alone. The
    domain filters and Linkup's research ``mode`` are reachable through
    ``provider_options``.

    A failed run's ``error`` is read for its ``message``, because Linkup's error
    bodies are ``{"code", "message"}`` objects.
    """

    name: ClassVar[str] = PROVIDER_LINKUP
    supported_parameters: ClassVar[frozenset[str]] = frozenset({"effort", "output_schema"})

    base_url: ClassVar[str] = LINKUP_BASE_URL
    research_path: ClassVar[str] = LINKUP_RESEARCH_PATH
    status_path: ClassVar[str] = LINKUP_RESEARCH_STATUS_PATH
    env_api_key: ClassVar[str] = ENV_LINKUP_API_KEY
    api_key_header: ClassVar[str] = LINKUP_API_KEY_HEADER
    api_key_prefix: ClassVar[str] = LINKUP_API_KEY_PREFIX

    def _start(self, request: ResearchRequest) -> ResearchProgress:
        return self._progress(request, self._post(self.research_path, self._build_payload(request)))

    async def _astart(self, request: ResearchRequest) -> ResearchProgress:
        return self._progress(request, await self._apost(self.research_path, self._build_payload(request)))

    def _fetch(self, request: ResearchRequest, job_id: str) -> ResearchProgress:
        return self._progress(request, self._get(self._status_path(job_id)))

    async def _afetch(self, request: ResearchRequest, job_id: str) -> ResearchProgress:
        return self._progress(request, await self._aget(self._status_path(job_id)))

    def _status_path(self, job_id: str) -> str:
        return self.status_path.format(research_id=quote(job_id, safe=""))

    def _build_payload(self, request: ResearchRequest) -> dict[str, Any]:
        depth = LINKUP_RESEARCH_DEPTHS[request.effort] if request.effort else None
        schema = request.output_schema
        return build_payload(
            request,
            q=request.query,
            outputType=LINKUP_RESEARCH_STRUCTURED_OUTPUT_TYPE if schema else LINKUP_RESEARCH_OUTPUT_TYPE,
            reasoningDepth=depth,
            **({LINKUP_RESEARCH_SCHEMA_FIELD: json.dumps(schema)} if schema else {}),
        )

    def _progress(self, request: ResearchRequest, payload: dict[str, Any]) -> ResearchProgress:
        state = self.state_of(str(payload.get("status")), LINKUP_RESEARCH_STATES)
        error = payload.get("error")
        return ResearchProgress(
            job_id=str(payload.get("id") or ""),
            state=state,
            # The status call carries the answer, so a finished run needs no
            # second fetch and keeps the one payload it was read from.
            response=self._to_response(request, payload) if state == "completed" else None,
            detail=error_detail(error),
        )

    def _to_response(self, request: ResearchRequest, payload: dict[str, Any]) -> ResearchResponse:
        output = payload.get("output") or {}
        if request.output_schema:
            return ResearchResponse(
                provider=self.name,
                query=request.query,
                structured=output,
                request_id=str(payload.get("id") or "") or None,
                raw=[payload],
            )
        return ResearchResponse(
            provider=self.name,
            query=request.query,
            answer=output.get("answer"),
            citations=[
                Citation(
                    url=source.get("url", ""),
                    title=source.get("name") or "",
                    snippet=source.get("snippet") or "",
                    extra={k: v for k, v in source.items() if k not in _MAPPED_SOURCE_FIELDS},
                )
                for source in output.get("sources") or []
            ],
            request_id=str(payload.get("id") or "") or None,
            raw=[payload],
        )


class LinkupAnswerProvider(HttpAnswerProvider):
    """Answers a question through Linkup's Search API, with ``outputType: sourcedAnswer``.

    Linkup writes the answer and lists every page it read under ``sources``; the
    answer carries no markers, so each citation backs the answer as a whole and
    ``field`` stays ``None``. There is no request id and no field saying the
    answer stopped early, so ``request_id`` is ``None`` and ``outcome`` is
    always ``complete``.

    ``include_domains`` and ``exclude_domains`` map as on search; ``recency``
    sets ``fromDate`` that far back from today. ``depth`` is required and sent
    as on search (see ``LINKUP_DEFAULT_DEPTH``); a deeper one goes through
    ``provider_options``.
    """

    name: ClassVar[str] = PROVIDER_LINKUP
    supported_parameters: ClassVar[frozenset[str]] = frozenset({*LINKUP_ANSWER_PARAM_NAMES, "recency"})

    base_url: ClassVar[str] = LINKUP_BASE_URL
    answer_path: ClassVar[str] = LINKUP_SEARCH_PATH
    env_api_key: ClassVar[str] = ENV_LINKUP_API_KEY
    api_key_header: ClassVar[str] = LINKUP_API_KEY_HEADER
    api_key_prefix: ClassVar[str] = LINKUP_API_KEY_PREFIX

    def _build_payload(self, request: AnswerRequest) -> dict[str, Any]:
        since = today() - timedelta(days=LINKUP_ANSWER_RECENCY_DAYS[request.recency]) if request.recency else None
        return build_payload(
            request,
            LINKUP_ANSWER_PARAM_NAMES,
            q=request.query,
            depth=LINKUP_DEFAULT_DEPTH,
            outputType=LINKUP_ANSWER_OUTPUT_TYPE,
            **{LINKUP_ANSWER_FROM_DATE_FIELD: since.strftime(LINKUP_ANSWER_DATE_FORMAT) if since else None},
        )

    def _to_response(self, request: AnswerRequest, payload: dict[str, Any]) -> AnswerResponse:
        return AnswerResponse(
            provider=self.name,
            query=request.query,
            answer=payload.get("answer") or "",
            citations=[
                Citation(
                    url=source.get("url", ""),
                    title=source.get("name") or "",
                    snippet=source.get("snippet") or "",
                    extra={k: v for k, v in source.items() if k not in _MAPPED_SOURCE_FIELDS},
                )
                for source in payload.get("sources") or []
            ],
            raw=[payload],
        )
