"""WebIQ provider, over Microsoft AI's Web Search (v3) and Browse APIs.

Search and extract. Talks to the REST endpoints directly: Microsoft's ``webiq``
SDK needs Python 3.11, and this package supports 3.10. Install with
the ``search`` extra or ``extract``. Reads
``WEBIQ_API_KEY`` unless a key is passed explicitly.

Search maps ``max_results``, ``search_depth`` (as ``maxLength``, and whole-page
text for ``deep``), the domain filters (as ``site:`` operators in the query),
``country`` (as ``region``) and ``locale`` (as ``language``); extract maps only
``format``. Everything else -- ``safeSearch``, ``location``, Browse's
``liveCrawl`` and link lists -- is reachable through ``provider_options``.
"""

from __future__ import annotations

from typing import Any, ClassVar

from web_router.constants import (
    ENV_WEBIQ_API_KEY,
    MSG_EXTRACT_UNSUCCESSFUL,
    PROVIDER_WEBIQ,
    WEBIQ_API_KEY_HEADER,
    WEBIQ_BASE_URL,
    WEBIQ_BROWSE_MAX_LENGTH,
    WEBIQ_BROWSE_PATH,
    WEBIQ_CONTENT_FORMAT,
    WEBIQ_EXTRACT_DEFAULT_FORMAT,
    WEBIQ_EXTRACT_FORMATS,
    WEBIQ_PARAM_NAMES,
    WEBIQ_SEARCH_PATH,
)
from web_router.constants_webiq import (
    MSG_WEBIQ_CODE,
    MSG_WEBIQ_QUERY_TOO_LONG,
    WEBIQ_PARAMS_BY_DEPTH,
    WEBIQ_QUERY_LIMIT,
    WEBIQ_SEARCH_PARAMETERS,
)
from web_router.exceptions import ProviderError
from web_router.providers._http import HttpExtractProvider, HttpSearchProvider
from web_router.providers._language import language_code
from web_router.providers._utils import as_snippet, build_payload, site_operators
from web_router.types import (
    EXTRACT_PARAMETERS,
    ExtractedDocument,
    ExtractFailure,
    ExtractFormat,
    ExtractRequest,
    ExtractResponse,
    SearchRequest,
    SearchResponse,
    SearchResult,
)

_MAPPED_FIELDS = frozenset({"url", "title", "content"})
_MAPPED_DOCUMENT_FIELDS = _MAPPED_FIELDS | {"traceId"}


class WebIQProvider(HttpSearchProvider):
    """Routes searches to WebIQ, Microsoft AI's Web Search API.

    Each result carries the passages of its page that answer the query, in
    ``content``; WebIQ returns no snippet and no relevance score, so
    ``description`` is the head of those passages and ``score`` stays unset.
    ``deep`` asks for each page's whole text instead.
    """

    name: ClassVar[str] = PROVIDER_WEBIQ
    supported_parameters: ClassVar[frozenset[str]] = WEBIQ_SEARCH_PARAMETERS
    refusal_reasons: ClassVar[dict[str, str]] = {"country": MSG_WEBIQ_CODE, "locale": MSG_WEBIQ_CODE}

    base_url: ClassVar[str] = WEBIQ_BASE_URL
    search_path: ClassVar[str] = WEBIQ_SEARCH_PATH
    env_api_key: ClassVar[str] = ENV_WEBIQ_API_KEY
    api_key_header: ClassVar[str] = WEBIQ_API_KEY_HEADER

    def _build_payload(self, request: SearchRequest) -> dict[str, Any]:
        query = self._query(request)
        if len(query) > WEBIQ_QUERY_LIMIT:
            raise ProviderError(self.name, MSG_WEBIQ_QUERY_TOO_LONG.format(limit=WEBIQ_QUERY_LIMIT, length=len(query)))
        depth = WEBIQ_PARAMS_BY_DEPTH[request.search_depth] if request.search_depth else {}
        return build_payload(
            request,
            WEBIQ_PARAM_NAMES,
            query=query,
            region=request.country.upper() if request.country else None,
            language=language_code(request.locale) if request.locale else None,
            **{**WEBIQ_CONTENT_FORMAT, **depth},
        )

    @staticmethod
    def _query(request: SearchRequest) -> str:
        return " ".join([request.query, *site_operators(request.include_domains, request.exclude_domains)])

    @classmethod
    def refused_options(cls, request: SearchRequest) -> dict[str, Any]:
        refused: dict[str, Any] = {}
        if request.country is not None and not _two_letters(request.country):
            refused["country"] = request.country
        if request.locale is not None and not _two_letters(language_code(request.locale)):
            refused["locale"] = request.locale
        return refused

    def _to_response(self, request: SearchRequest, payload: dict[str, Any]) -> SearchResponse:
        return SearchResponse(
            provider=self.name,
            query=request.query,
            results=[self._to_result(hit) for hit in payload.get("webResults") or []],
            request_id=payload.get("traceId"),
            raw=payload,
        )

    @staticmethod
    def _to_result(hit: dict[str, Any]) -> SearchResult:
        content = hit.get("content") or ""
        # lastUpdatedAt is when WebIQ saw the page change, not when it was
        # published, so it stays in extra rather than posing as published_date.
        return SearchResult(
            url=hit.get("url", ""),
            title=hit.get("title") or "",
            description=as_snippet(content),
            content=content,
            extra={k: v for k, v in hit.items() if k not in _MAPPED_FIELDS},
        )


def _two_letters(code: str) -> bool:
    return len(code) == 2 and code.isascii() and code.isalpha()


class WebIQExtractProvider(HttpExtractProvider):
    """Fetches page contents through WebIQ's Browse API, from WebIQ's own index.

    A page WebIQ has not crawled is not fetched live: the API answers 404 and
    the URL comes back as a failure, so ``auto`` moves on to the next member.
    """

    name: ClassVar[str] = PROVIDER_WEBIQ
    supported_parameters: ClassVar[frozenset[str]] = EXTRACT_PARAMETERS
    supported_formats: ClassVar[frozenset[ExtractFormat]] = frozenset(WEBIQ_EXTRACT_FORMATS)
    default_format: ClassVar[ExtractFormat] = WEBIQ_EXTRACT_DEFAULT_FORMAT

    base_url: ClassVar[str] = WEBIQ_BASE_URL
    extract_path: ClassVar[str] = WEBIQ_BROWSE_PATH
    env_api_key: ClassVar[str] = ENV_WEBIQ_API_KEY
    api_key_header: ClassVar[str] = WEBIQ_API_KEY_HEADER

    def _build_payload(self, request: ExtractRequest, urls: list[str]) -> dict[str, Any]:
        return build_payload(
            request,
            url=urls[0],
            contentFormat=WEBIQ_EXTRACT_FORMATS[self.format_of(request)],
            **WEBIQ_BROWSE_MAX_LENGTH,
        )

    def _to_response(self, request: ExtractRequest, urls: list[str], payload: dict[str, Any]) -> ExtractResponse:
        # A `liveCrawl` sent through provider_options answers 202 with only a
        # `retryAfter` and no content: the page was queued, not fetched.
        if "content" not in payload:
            reason = f"not fetched yet, retry after {payload['retryAfter']}" if payload.get("retryAfter") else None
            failure = ExtractFailure(url=urls[0], error=reason or MSG_EXTRACT_UNSUCCESSFUL)
            return ExtractResponse(
                provider=self.name, failures=[failure], request_id=payload.get("traceId"), raw=[payload]
            )

        document = ExtractedDocument(
            url=payload.get("url") or urls[0],
            title=payload.get("title") or "",
            content=payload.get("content") or "",
            format=self.format_of(request),
            extra={k: v for k, v in payload.items() if k not in _MAPPED_DOCUMENT_FIELDS},
        )
        return ExtractResponse(
            provider=self.name, documents=[document], request_id=payload.get("traceId"), raw=[payload]
        )
