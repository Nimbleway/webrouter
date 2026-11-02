"""Exa providers, over the Search, Contents and Answer APIs.

Talks to the REST endpoints directly rather than through ``exa-py``: web-router
uses a handful of fields of these APIs, which an SDK dependency would not
simplify. Install with the ``search``, ``extract`` or ``answer`` extra. Reads ``EXA_API_KEY`` unless a key is
passed explicitly.

Search is slim by design: only ``max_results`` is mapped. Everything else is
reachable through ``provider_options``; mapping one properly is a welcome PR --
see CONTRIBUTING.md.

Exa's Research API was retired upstream -- every path answers 410
``RESEARCH_RETIRED`` -- so there is no Exa research provider.
"""

from __future__ import annotations

from typing import Any, ClassVar

from web_router.constants import (
    ENV_EXA_API_KEY,
    EXA_API_KEY_HEADER,
    EXA_BASE_URL,
    EXA_CONTENTS,
    EXA_CONTENTS_PATH,
    EXA_EXTRACT_DEFAULT_FORMAT,
    EXA_EXTRACT_FORMATS,
    EXA_PARAM_NAMES,
    EXA_SEARCH_PATH,
    PROVIDER_EXA,
)
from web_router.constants_exa import EXA_ANSWER_CITATION_TEXT_FIELD, EXA_ANSWER_PATH
from web_router.providers._http import HttpAnswerProvider, HttpExtractProvider, HttpSearchProvider
from web_router.providers._utils import as_snippet, best_text, build_payload, cited_marker
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
    SearchRequest,
    SearchResponse,
    SearchResult,
)

_MAPPED_RESULT_FIELDS = frozenset({"url", "title", "text", "summary", "highlights", "score", "publishedDate"})
_MAPPED_DOCUMENT_FIELDS = frozenset({"url", "title", "text", "publishedDate"})
_MAPPED_SOURCE_FIELDS = frozenset({"url", "title", "snippet"})
_MAPPED_ANSWER_FIELDS = frozenset({"url", "title", "text"})


class ExaProvider(HttpSearchProvider):
    """Routes searches to the Exa Search API."""

    name: ClassVar[str] = PROVIDER_EXA
    supported_parameters: ClassVar[frozenset[str]] = frozenset(EXA_PARAM_NAMES)

    base_url: ClassVar[str] = EXA_BASE_URL
    search_path: ClassVar[str] = EXA_SEARCH_PATH
    env_api_key: ClassVar[str | None] = ENV_EXA_API_KEY
    api_key_header: ClassVar[str] = EXA_API_KEY_HEADER

    def _build_payload(self, request: SearchRequest) -> dict[str, Any]:
        return build_payload(request, EXA_PARAM_NAMES, query=request.query, contents=EXA_CONTENTS)

    def _to_response(self, request: SearchRequest, payload: dict[str, Any]) -> SearchResponse:
        return SearchResponse(
            provider=self.name,
            query=request.query,
            results=[self._to_result(hit) for hit in payload.get("results") or []],
            request_id=payload.get("requestId"),
            raw=payload,
        )

    @staticmethod
    def _to_result(hit: dict[str, Any]) -> SearchResult:
        # Exa returns no snippet field; its summary, else the head of the text,
        # is the closest equivalent to what other providers call a description.
        text = hit.get("text") or ""
        summary = hit.get("summary") or " ".join(hit.get("highlights") or [])
        return SearchResult(
            url=hit.get("url", ""),
            title=hit.get("title") or "",
            description=summary or as_snippet(text),
            content=best_text(text, summary),
            score=hit.get("score"),
            published_date=hit.get("publishedDate"),
            extra={k: v for k, v in hit.items() if k not in _MAPPED_RESULT_FIELDS},
        )


class ExaExtractProvider(HttpExtractProvider):
    """Fetches page contents through the Exa Contents API.

    Exa returns one representation of a page, its ``text``, so this is the one
    extract backend here that cannot produce markdown: asking it for markdown
    raises rather than handing back text under that name.
    """

    name: ClassVar[str] = PROVIDER_EXA
    supported_parameters: ClassVar[frozenset[str]] = EXTRACT_PARAMETERS
    supported_formats: ClassVar[frozenset[ExtractFormat]] = frozenset(EXA_EXTRACT_FORMATS)
    default_format: ClassVar[ExtractFormat] = EXA_EXTRACT_DEFAULT_FORMAT
    batches_urls: ClassVar[bool] = True

    base_url: ClassVar[str] = EXA_BASE_URL
    extract_path: ClassVar[str] = EXA_CONTENTS_PATH
    env_api_key: ClassVar[str | None] = ENV_EXA_API_KEY
    api_key_header: ClassVar[str] = EXA_API_KEY_HEADER

    def _build_payload(self, request: ExtractRequest, urls: list[str]) -> dict[str, Any]:
        # `urls` and `ids` are both accepted and the schema requires one of
        # them; Exa documents `urls` as "backwards compatible with the ids
        # parameter", so `ids` is the older spelling rather than the current one.
        return build_payload(request, urls=urls, **EXA_EXTRACT_FORMATS[self.format_of(request)])

    def _to_response(self, request: ExtractRequest, urls: list[str], payload: dict[str, Any]) -> ExtractResponse:
        body_format = self.format_of(request)
        documents = [
            ExtractedDocument(
                url=hit.get("url", ""),
                title=hit.get("title") or "",
                content=hit.get("text") or "",
                format=body_format,
                published_date=hit.get("publishedDate"),
                extra={k: v for k, v in hit.items() if k not in _MAPPED_DOCUMENT_FIELDS},
            )
            for hit in payload.get("results") or []
        ]

        return ExtractResponse(
            provider=self.name,
            documents=documents,
            failures=self._failures(payload),
            request_id=payload.get("requestId"),
            raw=[payload],
        )

    @staticmethod
    def _failures(payload: dict[str, Any]) -> list[ExtractFailure]:
        """Exa reports each URL's outcome in `statuses`, keyed by the id it was asked for."""
        return [
            ExtractFailure(url=status.get("id", ""), error=str(status.get("error") or status.get("status")))
            for status in payload.get("statuses") or []
            if status.get("status") != "success"
        ]


class ExaAnswerProvider(HttpAnswerProvider):
    """Answers a question through Exa's Answer API, ``POST /answer``.

    The answer cites inline as ``[2]``, numbering ``citations`` from 1; a
    citation whose marker appears in the answer carries it in ``field``. A
    cited page's ``text``, when Exa returns it, is its snippet. ``requestId``
    is the request id, and ``costDollars`` -- the call's own stated cost --
    stays in ``raw``.

    No normalized option is mapped: Exa's Answer API documents no domain or
    date filter, and ``userLocation`` showed no clear effect live, so
    ``country`` is refused rather than sent to it.
    """

    name: ClassVar[str] = PROVIDER_EXA
    supported_parameters: ClassVar[frozenset[str]] = frozenset()

    base_url: ClassVar[str] = EXA_BASE_URL
    answer_path: ClassVar[str] = EXA_ANSWER_PATH
    env_api_key: ClassVar[str] = ENV_EXA_API_KEY
    api_key_header: ClassVar[str] = EXA_API_KEY_HEADER

    def _build_payload(self, request: AnswerRequest) -> dict[str, Any]:
        return build_payload(request, query=request.query)

    def _to_response(self, request: AnswerRequest, payload: dict[str, Any]) -> AnswerResponse:
        answer = payload.get("answer")
        text = answer if isinstance(answer, str) else ""
        return AnswerResponse(
            provider=self.name,
            query=request.query,
            answer=text,
            citations=[
                Citation(
                    url=cited.get("url", ""),
                    title=cited.get("title") or "",
                    snippet=as_snippet(cited.get(EXA_ANSWER_CITATION_TEXT_FIELD) or ""),
                    field=cited_marker(text, position),
                    extra={k: v for k, v in cited.items() if k not in _MAPPED_ANSWER_FIELDS},
                )
                for position, cited in enumerate(payload.get("citations") or [], start=1)
            ],
            request_id=payload.get("requestId"),
            raw=[payload],
        )
