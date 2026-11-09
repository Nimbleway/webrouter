"""You.com providers, over the Search API and the Research API.

You.com publishes no Python SDK, so this talks to the REST endpoints directly.
:class:`YouProvider` searches; :class:`YouResearchProvider` runs research as a
background task; :class:`YouAnswerProvider` answers one question in one call.
Install with the ``search``, ``research`` or ``answer`` extra.
Reads ``YOU_API_KEY`` unless a key is passed explicitly.

Search maps every shared option but ``effort``, which You.com has no tier for.
``country`` and ``locale`` reach fixed lists of 36 countries and 51 languages,
and a value off them is refused before the call. Everything else its API offers
is reachable through ``provider_options``.
"""

from __future__ import annotations

from collections.abc import Mapping
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
    MSG_YOU_ANSWER_DOMAINS_EXCLUSIVE,
    MSG_YOU_SEARCH_DOMAINS_EXCLUSIVE,
    MSG_YOU_SEARCH_VALUES,
    YOU_ANSWER_MARKER,
    YOU_ANSWER_PARAM_NAMES,
    YOU_ANSWER_PATH,
    YOU_COUNTRIES,
    YOU_FRESHNESS_FIELD,
    YOU_RECENCY,
    YOU_RESEARCH_BACKGROUND,
    YOU_RESEARCH_BASE_URL,
    YOU_RESEARCH_EFFORTS,
    YOU_RESEARCH_PATH,
    YOU_RESEARCH_STATES,
    YOU_RESEARCH_STATUS_PATH,
    YOU_SEARCH_PARAM_NAMES,
    YOU_SEARCH_PARAMETERS,
)
from web_router.providers._http import HttpAnswerProvider, HttpPolledResearchProvider, HttpSearchProvider
from web_router.providers._language import language_tag, off_the_lists
from web_router.providers._search_text import lite_body, refused_domains
from web_router.providers._utils import best_text, build_payload, mapped
from web_router.providers.answer import refuse_domain_pair
from web_router.types import (
    Citation,
    ResearchRequest,
    ResearchResponse,
    ResearchState,
    SearchDepth,
    SearchRequest,
    SearchResponse,
    SearchResult,
)
from web_router.types_answer import AnswerRequest, AnswerResponse

_MAPPED_RESULT_FIELDS = frozenset({"url", "title", "description", "contents", "page_age"})
_MAPPED_SOURCE_FIELDS = frozenset({"url", "title", "snippets"})


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


class YouResearchProvider(HttpPolledResearchProvider):
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

    research_path: ClassVar[str] = YOU_RESEARCH_PATH
    status_path: ClassVar[str] = YOU_RESEARCH_STATUS_PATH
    research_states: ClassVar[Mapping[str, ResearchState]] = YOU_RESEARCH_STATES
    # The start call names the task `task_id`; the status call names it `id`.
    job_id_fields: ClassVar[tuple[str, ...]] = ("task_id", "id")

    def _build_payload(self, request: ResearchRequest) -> dict[str, Any]:
        effort = mapped(YOU_RESEARCH_EFFORTS, request.effort)
        return build_payload(request, input=request.query, research_effort=effort, background=YOU_RESEARCH_BACKGROUND)

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


class YouAnswerProvider(HttpAnswerProvider):
    """Answers a question through the You.com Answer API, ``POST /v1/answer`` on ``api.you.com``.

    The answer cites as ``[[1, 2]]``, numbering ``citations`` -- only the
    sources it cites -- from 1; each citation carries the first marker naming it
    in ``field``. A citation is ``{source, excerpts}``, so its title is joined
    from ``results.web``, the pages considered, by URL, and its snippet is the
    excerpts. There is no request id and no early stop: ``request_id`` is
    ``None`` and ``outcome`` always ``complete``.

    ``include_domains``, ``exclude_domains`` and ``country`` map to You.com's own
    fields, and ``recency`` to ``freshness``. The two domain lists cannot be sent
    together, so asking for both raises before the call. ``locale`` is refused:
    ``language`` showed no effect live.
    """

    name: ClassVar[str] = PROVIDER_YOU
    supported_parameters: ClassVar[frozenset[str]] = frozenset({*YOU_ANSWER_PARAM_NAMES, "recency"})

    base_url: ClassVar[str] = YOU_RESEARCH_BASE_URL
    answer_path: ClassVar[str] = YOU_ANSWER_PATH
    env_api_key: ClassVar[str] = ENV_YOU_API_KEY
    api_key_header: ClassVar[str] = YOU_API_KEY_HEADER

    def _build_payload(self, request: AnswerRequest) -> dict[str, Any]:
        refuse_domain_pair(self.name, request, MSG_YOU_ANSWER_DOMAINS_EXCLUSIVE)
        return build_payload(
            request,
            YOU_ANSWER_PARAM_NAMES,
            query=request.query,
            **{YOU_FRESHNESS_FIELD: mapped(YOU_RECENCY, request.recency)},
        )

    def _to_response(self, request: AnswerRequest, payload: dict[str, Any]) -> AnswerResponse:
        answer = payload.get("answer") or ""
        pages = {page.get("url"): page for page in (payload.get("results") or {}).get("web") or []}
        markers: dict[int, str] = {}
        for match in YOU_ANSWER_MARKER.finditer(answer):
            for number in match.group(1).split(","):
                markers.setdefault(int(number), match.group(0))
        return AnswerResponse(
            provider=self.name,
            query=request.query,
            answer=answer,
            citations=[
                self._to_citation(cited, pages.get(cited.get("source")) or {}, markers.get(position))
                for position, cited in enumerate(payload.get("citations") or [], start=1)
            ],
            raw=[payload],
        )

    @staticmethod
    def _to_citation(cited: dict[str, Any], page: dict[str, Any], marker: str | None) -> Citation:
        return Citation(
            url=cited.get("source") or "",
            title=page.get("title") or "",
            snippet="\n\n".join(cited.get("excerpts") or []),
            field=marker,
            extra={key: value for key, value in page.items() if key not in {"url", "title"}},
        )
