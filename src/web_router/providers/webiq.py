"""WebIQ provider, over Microsoft AI's Web Search (v3) and Browse APIs.

Search and extract. Talks to the REST endpoints directly: Microsoft's ``webiq``
SDK needs Python 3.11, and this package supports 3.10. Install with
the ``search`` extra or ``extract``. Reads
``WEBIQ_API_KEY`` unless a key is passed explicitly.

Slim by design: search maps only ``max_results`` and extract only ``format``.
Everything else -- ``region``, ``language``, ``safeSearch``, Browse's
``liveCrawl`` and link lists -- is reachable through ``provider_options``;
mapping one properly is a welcome PR -- see CONTRIBUTING.md.
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
from web_router.providers._http import HttpExtractProvider, HttpSearchProvider
from web_router.providers._utils import as_snippet, build_payload
from web_router.types import (
    EXTRACT_FORMAT_ONLY,
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
    """

    name: ClassVar[str] = PROVIDER_WEBIQ
    supported_parameters: ClassVar[frozenset[str]] = frozenset(WEBIQ_PARAM_NAMES)

    base_url: ClassVar[str] = WEBIQ_BASE_URL
    search_path: ClassVar[str] = WEBIQ_SEARCH_PATH
    env_api_key: ClassVar[str] = ENV_WEBIQ_API_KEY
    api_key_header: ClassVar[str] = WEBIQ_API_KEY_HEADER

    def _build_payload(self, request: SearchRequest) -> dict[str, Any]:
        return build_payload(request, WEBIQ_PARAM_NAMES, query=request.query, **WEBIQ_CONTENT_FORMAT)

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


class WebIQExtractProvider(HttpExtractProvider):
    """Fetches page contents through WebIQ's Browse API, from WebIQ's own index.

    A page WebIQ has not crawled is not fetched live: the API answers 404 and
    the URL comes back as a failure, so ``auto`` moves on to the next member.
    """

    name: ClassVar[str] = PROVIDER_WEBIQ
    supported_parameters: ClassVar[frozenset[str]] = EXTRACT_FORMAT_ONLY
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
