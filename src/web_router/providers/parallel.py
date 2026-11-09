"""Parallel providers, over the Search API, the Task API and the Chat API.

Talks to the REST endpoints directly rather than through ``parallel-web``:
web-router uses a handful of fields of these APIs, which an SDK dependency would
not simplify. Install with the ``search``, ``research`` or ``answer`` extra. Reads ``PARALLEL_API_KEY`` unless a key
is passed explicitly.

Search calls ``/v1/search`` with ``mode="fast"`` unless ``effort`` picks another
mode, and maps ``max_results``, ``search_depth``, the domain filters, ``country``
and ``recency`` under ``advanced_settings``. Everything else is reachable through
``provider_options``, merged into ``advanced_settings`` rather than replacing it.
"""

from __future__ import annotations

from typing import Any, ClassVar

from web_router.constants import (
    ENV_PARALLEL_API_KEY,
    PARALLEL_API_KEY_HEADER,
    PARALLEL_BASE_URL,
    PARALLEL_RESEARCH_DEFAULT_EFFORT,
    PARALLEL_RESEARCH_JSON_OUTPUT,
    PARALLEL_RESEARCH_PROCESSORS,
    PARALLEL_RESEARCH_STATES,
    PARALLEL_RESEARCH_TEXT_OUTPUT,
    PARALLEL_TASK_RESULT_PATH,
    PARALLEL_TASK_RUN_PATH,
    PARALLEL_TASK_STATUS_PATH,
    PROVIDER_PARALLEL,
)
from web_router.constants_parallel import (
    MSG_PARALLEL_COUNTRY,
    MSG_PARALLEL_DOMAINS,
    MSG_PARALLEL_TURBO_PATHS,
    PARALLEL_ADVANCED_NAMES,
    PARALLEL_ADVANCED_SETTINGS,
    PARALLEL_AFTER_DATE,
    PARALLEL_ANSWER_API_KEY_HEADER,
    PARALLEL_ANSWER_API_KEY_PREFIX,
    PARALLEL_ANSWER_BASIS_EXTRA,
    PARALLEL_ANSWER_MODEL,
    PARALLEL_ANSWER_PATH,
    PARALLEL_ANSWER_ROLE,
    PARALLEL_ANSWER_TRUNCATED,
    PARALLEL_ANSWER_WHOLE_FIELD,
    PARALLEL_DOMAIN_LIMIT,
    PARALLEL_EXCERPT_CHARS,
    PARALLEL_EXCERPT_MAX,
    PARALLEL_EXCERPT_SETTINGS,
    PARALLEL_LOCATION,
    PARALLEL_LOCATIONS,
    PARALLEL_MODE_BY_EFFORT,
    PARALLEL_SEARCH_MODE,
    PARALLEL_SEARCH_PARAMETERS,
    PARALLEL_SEARCH_PATH,
    PARALLEL_SOURCE_FIELDS,
    PARALLEL_SOURCE_POLICY,
)
from web_router.providers._clock import recency_start
from web_router.providers._http import HttpAnswerProvider, HttpResearchProvider, HttpSearchProvider
from web_router.providers._utils import as_snippet, build_payload, compact, lite_body, mapped, refused_domains
from web_router.types import (
    Citation,
    ResearchProgress,
    ResearchRequest,
    ResearchResponse,
    ResearchState,
    SearchDepth,
    SearchRequest,
    SearchResponse,
    SearchResult,
)
from web_router.types_answer import AnswerRequest, AnswerResponse

_MAPPED_RESULT_FIELDS = frozenset({"url", "title", "excerpts", "publish_date"})
_MAPPED_BASIS_FIELDS = frozenset({"field", "citations"})


def _citations_from_basis(
    basis: list[dict[str, Any]], *, whole_field: str | None = None, basis_extra: frozenset[str] | None = None
) -> list[Citation]:
    """Flatten Parallel's per-field basis into one citation per cited source.

    Each basis entry says which output field it supports and why; what it
    carries besides belongs to the whole entry rather than to any one source,
    so every citation drawn from it carries it in ``extra`` -- every unmapped
    key, or only ``basis_extra``. An entry for ``whole_field`` backs the whole
    output, so its citations have ``field=None``. Empty excerpts are dropped.
    """
    return [
        Citation(
            url=cited.get("url", ""),
            title=cited.get("title") or "",
            snippet="\n\n".join(excerpt for excerpt in cited.get("excerpts") or [] if excerpt),
            field=None if entry.get("field") == whole_field else entry.get("field"),
            extra={
                **{
                    key: value
                    for key, value in entry.items()
                    if key not in _MAPPED_BASIS_FIELDS
                    and (basis_extra is None or (key in basis_extra and value is not None))
                },
                **{key: value for key, value in cited.items() if key not in _MAPPED_CITATION_FIELDS},
            },
        )
        for entry in basis
        for cited in entry.get("citations") or []
    ]


_MAPPED_CITATION_FIELDS = frozenset({"url", "title", "excerpts"})


def _merged(ours: dict[str, Any], theirs: dict[str, Any]) -> dict[str, Any]:
    """``theirs`` laid over ``ours`` at every depth: a nested object is merged, any other value replaced."""
    merged = dict(ours)
    for key, value in theirs.items():
        nested = merged.get(key)
        merged[key] = _merged(nested, value) if isinstance(nested, dict) and isinstance(value, dict) else value
    return merged


class ParallelProvider(HttpSearchProvider):
    """Routes searches to the Parallel Search API.

    ``search_depth`` caps the excerpts each result carries, and ``deep`` is
    refused: they are an upper bound chosen for the query, never the whole
    page. ``effort`` picks the mode. Parallel would ignore ``exclude_domains``
    beside ``include_domains`` and a country outside its list, so both are
    refused.
    """

    name: ClassVar[str] = PROVIDER_PARALLEL
    supported_parameters: ClassVar[frozenset[str]] = PARALLEL_SEARCH_PARAMETERS
    depth_levels = frozenset(PARALLEL_EXCERPT_CHARS)
    refusal_reasons: ClassVar[dict[str, str]] = {
        "include_domains": MSG_PARALLEL_DOMAINS,
        "exclude_domains": MSG_PARALLEL_DOMAINS,
        "country": MSG_PARALLEL_COUNTRY,
        "effort": MSG_PARALLEL_TURBO_PATHS,
    }

    base_url: ClassVar[str] = PARALLEL_BASE_URL
    search_path: ClassVar[str] = PARALLEL_SEARCH_PATH
    env_api_key: ClassVar[str | None] = ENV_PARALLEL_API_KEY
    api_key_header: ClassVar[str] = PARALLEL_API_KEY_HEADER

    def _build_payload(self, request: SearchRequest) -> dict[str, Any]:
        # Parallel is built around an objective plus the queries to run for it.
        # With a single normalized query, both are that query.
        settings = self._advanced_settings(request)
        payload = build_payload(
            request,
            objective=request.query,
            search_queries=[request.query],
            mode=PARALLEL_MODE_BY_EFFORT[request.effort] if request.effort else PARALLEL_SEARCH_MODE,
            advanced_settings=settings or None,
        )
        # build_payload lets provider_options replace a field whole; here that
        # would drop the mapped settings beside the one a caller added, so the
        # caller's object is merged in instead, its fields still winning.
        theirs = request.provider_options.get(PARALLEL_ADVANCED_SETTINGS)
        if isinstance(theirs, dict):
            payload[PARALLEL_ADVANCED_SETTINGS] = _merged(settings, theirs)
        return payload

    @staticmethod
    def _advanced_settings(request: SearchRequest) -> dict[str, Any]:
        """The options /v1/search takes under ``advanced_settings``, unset ones left out."""
        since = recency_start(request.recency)
        source_policy = compact(
            {
                **{theirs: getattr(request, ours) for ours, theirs in PARALLEL_SOURCE_FIELDS.items()},
                PARALLEL_AFTER_DATE: since.isoformat() if since else None,
            }
        )
        excerpts = mapped(PARALLEL_EXCERPT_CHARS, request.search_depth)
        return compact(
            {
                **{theirs: getattr(request, ours) for ours, theirs in PARALLEL_ADVANCED_NAMES.items()},
                PARALLEL_SOURCE_POLICY: source_policy or None,
                PARALLEL_EXCERPT_SETTINGS: {PARALLEL_EXCERPT_MAX: excerpts} if excerpts else None,
                PARALLEL_LOCATION: request.country.lower() if request.country else None,
            }
        )

    @classmethod
    def refused_options(cls, request: SearchRequest) -> dict[str, Any]:
        # Parallel ignores the excluded list beside an included one, so that is the one named.
        refused: dict[str, Any] = refused_domains(request, limit=PARALLEL_DOMAIN_LIMIT, both=("exclude_domains",))
        if request.country is not None and request.country.lower() not in PARALLEL_LOCATIONS:
            refused["country"] = request.country
        domains = [*(request.include_domains or []), *(request.exclude_domains or [])]
        if request.effort == "low" and any("/" in domain for domain in domains):
            refused["effort"] = request.effort
        return refused

    def _to_response(self, request: SearchRequest, payload: dict[str, Any]) -> SearchResponse:
        return SearchResponse(
            provider=self.name,
            query=request.query,
            results=[self._to_result(hit, request.search_depth) for hit in payload.get("results") or []],
            request_id=payload.get("search_id"),
            raw=payload,
        )

    @staticmethod
    def _to_result(hit: dict[str, Any], depth: SearchDepth | None) -> SearchResult:
        # Parallel returns ranked page excerpts rather than a snippet, and they
        # run to thousands of characters -- so the body is all of them, and the
        # description is the head of the first. The excerpt cap does not bind
        # at lite (1,377-1,488 characters live), so lite is cut here.
        excerpts = [excerpt for excerpt in hit.get("excerpts") or [] if excerpt]
        return SearchResult(
            url=hit.get("url", ""),
            title=hit.get("title") or "",
            description=as_snippet(excerpts[0]) if excerpts else "",
            content=lite_body("\n\n".join(excerpts), depth),
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
    research_path: ClassVar[str] = PARALLEL_TASK_RUN_PATH
    status_path: ClassVar[str] = PARALLEL_TASK_STATUS_PATH
    result_path: ClassVar[str] = PARALLEL_TASK_RESULT_PATH
    env_api_key: ClassVar[str | None] = ENV_PARALLEL_API_KEY
    api_key_header: ClassVar[str] = PARALLEL_API_KEY_HEADER

    def _start(self, request: ResearchRequest) -> ResearchProgress:
        return self._read(request, self._post(self.research_path, self._build_payload(request)))

    async def _astart(self, request: ResearchRequest) -> ResearchProgress:
        return await self._aread(request, await self._apost(self.research_path, self._build_payload(request)))

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
        return _citations_from_basis(output.get("basis") or [])


class ParallelAnswerProvider(HttpAnswerProvider):
    """Answers a question through Parallel's Chat API (beta), with the ``lite`` model.

    One ``POST /v1beta/chat/completions``: the answer is the message content,
    and its sources are the top-level ``basis`` -- per answer field, the
    citations behind it with their excerpts, a ``reasoning`` and a
    ``confidence``. A citation backing the answer as a whole has
    ``field=None``; the basis's ``confidence`` rides in its ``extra``, and the
    reasoning stays in ``raw``. A ``finish_reason`` of ``length`` is
    ``partial``.

    The Chat API takes no source filters, so every option is refused. Another
    model goes through ``provider_options``; ``speed`` returns no ``basis``.
    """

    name: ClassVar[str] = PROVIDER_PARALLEL
    supported_parameters: ClassVar[frozenset[str]] = frozenset()

    base_url: ClassVar[str] = PARALLEL_BASE_URL
    answer_path: ClassVar[str] = PARALLEL_ANSWER_PATH
    env_api_key: ClassVar[str] = ENV_PARALLEL_API_KEY
    api_key_header: ClassVar[str] = PARALLEL_ANSWER_API_KEY_HEADER
    api_key_prefix: ClassVar[str] = PARALLEL_ANSWER_API_KEY_PREFIX

    def _build_payload(self, request: AnswerRequest) -> dict[str, Any]:
        return build_payload(
            request,
            model=PARALLEL_ANSWER_MODEL,
            messages=[{"role": PARALLEL_ANSWER_ROLE, "content": request.query}],
        )

    def _to_response(self, request: AnswerRequest, payload: dict[str, Any]) -> AnswerResponse:
        choice = (payload.get("choices") or [{}])[0]
        return AnswerResponse(
            provider=self.name,
            query=request.query,
            answer=(choice.get("message") or {}).get("content") or "",
            citations=_citations_from_basis(
                payload.get("basis") or [],
                whole_field=PARALLEL_ANSWER_WHOLE_FIELD,
                basis_extra=PARALLEL_ANSWER_BASIS_EXTRA,
            ),
            outcome="partial" if choice.get("finish_reason") == PARALLEL_ANSWER_TRUNCATED else "complete",
            request_id=payload.get("id"),
            raw=[payload],
        )
