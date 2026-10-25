"""Parallel providers, over the Search API and the Task API.

Talks to the REST endpoints directly rather than through ``parallel-web``:
web-router uses a handful of fields of these APIs, which an SDK dependency would
not simplify. Install with the ``search`` extra or
the ``research`` extra. Reads ``PARALLEL_API_KEY`` unless a key
is passed explicitly.

Search is slim by design: only ``max_results`` is mapped. Everything else is
reachable through ``provider_options``; mapping one properly is a welcome PR --
see CONTRIBUTING.md.
"""

from __future__ import annotations

from typing import Any, ClassVar

from web_router.constants import (
    ENV_PARALLEL_API_KEY,
    PARALLEL_API_KEY_HEADER,
    PARALLEL_BASE_URL,
    PARALLEL_PARAM_NAMES,
    PARALLEL_RESEARCH_DEFAULT_EFFORT,
    PARALLEL_RESEARCH_JSON_OUTPUT,
    PARALLEL_RESEARCH_PROCESSORS,
    PARALLEL_RESEARCH_STATES,
    PARALLEL_RESEARCH_TEXT_OUTPUT,
    PARALLEL_SEARCH_PATH,
    PARALLEL_TASK_RESULT_PATH,
    PARALLEL_TASK_RUN_PATH,
    PARALLEL_TASK_STATUS_PATH,
    PROVIDER_PARALLEL,
)
from web_router.providers._http import HttpResearchProvider, HttpSearchProvider
from web_router.providers._utils import as_snippet, build_payload
from web_router.types import (
    Citation,
    ResearchProgress,
    ResearchRequest,
    ResearchResponse,
    ResearchState,
    SearchRequest,
    SearchResponse,
    SearchResult,
)

_MAPPED_RESULT_FIELDS = frozenset({"url", "title", "excerpts", "publish_date"})
_MAPPED_BASIS_FIELDS = frozenset({"field", "citations"})
_MAPPED_CITATION_FIELDS = frozenset({"url", "title", "excerpts"})


class ParallelProvider(HttpSearchProvider):
    """Routes searches to the Parallel Search API."""

    name: ClassVar[str] = PROVIDER_PARALLEL
    supported_parameters: ClassVar[frozenset[str]] = frozenset(PARALLEL_PARAM_NAMES)

    base_url: ClassVar[str] = PARALLEL_BASE_URL
    search_path: ClassVar[str] = PARALLEL_SEARCH_PATH
    env_api_key: ClassVar[str | None] = ENV_PARALLEL_API_KEY
    api_key_header: ClassVar[str] = PARALLEL_API_KEY_HEADER

    def _build_payload(self, request: SearchRequest) -> dict[str, Any]:
        # Parallel is built around an objective plus the queries to run for it.
        # With a single normalized query, both are that query.
        return build_payload(
            request,
            PARALLEL_PARAM_NAMES,
            objective=request.query,
            search_queries=[request.query],
        )

    def _to_response(self, request: SearchRequest, payload: dict[str, Any]) -> SearchResponse:
        return SearchResponse(
            provider=self.name,
            query=request.query,
            results=[self._to_result(hit) for hit in payload.get("results") or []],
            request_id=payload.get("search_id"),
            raw=payload,
        )

    @staticmethod
    def _to_result(hit: dict[str, Any]) -> SearchResult:
        # Parallel returns ranked page excerpts rather than a snippet, and they
        # run to thousands of characters -- so the body is all of them, and the
        # description is the head of the first.
        excerpts = [excerpt for excerpt in hit.get("excerpts") or [] if excerpt]
        return SearchResult(
            url=hit.get("url", ""),
            title=hit.get("title") or "",
            description=as_snippet(excerpts[0]) if excerpts else "",
            content="\n\n".join(excerpts),
            published_date=hit.get("publish_date"),
            extra={k: v for k, v in hit.items() if k not in _MAPPED_RESULT_FIELDS},
        )


class ParallelResearchProvider(HttpResearchProvider):
    """Runs agentic research through the Parallel Task API.

    A run is started, polled and then read from a third endpoint: the status
    call reports where it is and the result call carries the answer, which is
    why a finished run keeps two payloads in
    :attr:`~web_router.ResearchResponse.raw`.

    Verified against the live API on 2026-09-18 -- the start, status and result
    calls, the prose and structured shapes of ``output.content``, and the
    per-field ``basis`` this maps onto citations.
    """

    name: ClassVar[str] = PROVIDER_PARALLEL
    supported_parameters: ClassVar[frozenset[str]] = frozenset({"effort", "output_schema"})

    base_url: ClassVar[str] = PARALLEL_BASE_URL
    run_path: ClassVar[str] = PARALLEL_TASK_RUN_PATH
    status_path: ClassVar[str] = PARALLEL_TASK_STATUS_PATH
    result_path: ClassVar[str] = PARALLEL_TASK_RESULT_PATH
    env_api_key: ClassVar[str | None] = ENV_PARALLEL_API_KEY
    api_key_header: ClassVar[str] = PARALLEL_API_KEY_HEADER

    def _start(self, request: ResearchRequest) -> ResearchProgress:
        return self._read(request, self._post(self.run_path, self._build_payload(request)))

    async def _astart(self, request: ResearchRequest) -> ResearchProgress:
        return await self._aread(request, await self._apost(self.run_path, self._build_payload(request)))

    def _fetch(self, request: ResearchRequest, job_id: str) -> ResearchProgress:
        return self._read(request, self._get(self.status_path.format(run_id=job_id)))

    async def _afetch(self, request: ResearchRequest, job_id: str) -> ResearchProgress:
        return await self._aread(request, await self._aget(self.status_path.format(run_id=job_id)))

    def _read(self, request: ResearchRequest, run: dict[str, Any]) -> ResearchProgress:
        """Fetch the answer of a run that has one, from whichever call reported it.

        A short run can come back from the start call already completed, and a
        progress saying so without the answer beside it reads as a run that
        failed for no stated reason.
        """
        if self._state(run) != "completed":
            return self._progress(run)
        result = self._get(self.result_path.format(run_id=run.get("run_id")))
        return self._progress(run, self._to_response(request, run, result))

    async def _aread(self, request: ResearchRequest, run: dict[str, Any]) -> ResearchProgress:
        if self._state(run) != "completed":
            return self._progress(run)
        result = await self._aget(self.result_path.format(run_id=run.get("run_id")))
        return self._progress(run, self._to_response(request, run, result))

    def _build_payload(self, request: ResearchRequest) -> dict[str, Any]:
        effort = request.effort or PARALLEL_RESEARCH_DEFAULT_EFFORT
        return build_payload(
            request,
            input=request.query,
            processor=PARALLEL_RESEARCH_PROCESSORS[effort],
            task_spec=self._task_spec(request),
        )

    @staticmethod
    def _task_spec(request: ResearchRequest) -> dict[str, Any]:
        """The shape to answer in, named either way.

        A run that sends no spec gets Parallel's ``auto`` schema, which answers a
        prose question with a one-key object rather than a string -- so prose is
        asked for as explicitly as structure is.
        """
        if request.output_schema is None:
            return {"output_schema": {"type": PARALLEL_RESEARCH_TEXT_OUTPUT}}
        return {
            "output_schema": {
                "type": PARALLEL_RESEARCH_JSON_OUTPUT,
                "json_schema": request.output_schema,
            }
        }

    def _state(self, run: dict[str, Any]) -> ResearchState:
        return self.state_of(str(run.get("status")), PARALLEL_RESEARCH_STATES)

    def _progress(self, run: dict[str, Any], response: ResearchResponse | None = None) -> ResearchProgress:
        # Parallel wraps a failure in an error object whose `message` is the
        # readable half -- the same envelope it returns a rejected request in.
        error = run.get("error")
        detail = error.get("message") if isinstance(error, dict) else error
        return ResearchProgress(
            job_id=str(run.get("run_id") or ""),
            state=self._state(run),
            response=response,
            detail=None if detail is None else str(detail),
        )

    def _to_response(
        self, request: ResearchRequest, status: dict[str, Any], result: dict[str, Any]
    ) -> ResearchResponse:
        output = result.get("output") or {}
        content = output.get("content")
        # One field or many: `content` is the prose for a text run and the object
        # matching the schema for a structured one, and the run says which.
        return ResearchResponse(
            provider=self.name,
            query=request.query,
            answer=content if isinstance(content, str) else None,
            structured=content if isinstance(content, dict | list) else None,
            citations=self._citations(output),
            request_id=str(status.get("run_id") or "") or None,
            raw=[status, result],
        )

    @staticmethod
    def _citations(output: dict[str, Any]) -> list[Citation]:
        """Flatten Parallel's per-field basis into one citation per cited source.

        Each basis entry says which output field it supports and why; the
        reasoning and confidence belong to the whole entry rather than to any
        one source, so every citation drawn from it carries them.
        """
        return [
            Citation(
                url=cited.get("url", ""),
                title=cited.get("title") or "",
                snippet="\n\n".join(excerpt for excerpt in cited.get("excerpts") or [] if excerpt),
                field=basis.get("field"),
                extra={
                    **{key: value for key, value in basis.items() if key not in _MAPPED_BASIS_FIELDS},
                    **{key: value for key, value in cited.items() if key not in _MAPPED_CITATION_FIELDS},
                },
            )
            for basis in output.get("basis") or []
            for cited in basis.get("citations") or []
        ]
