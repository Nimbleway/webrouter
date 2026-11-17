"""SpecStream search and extract provider.

Talks to the REST endpoint directly: SpecStream publishes no Python SDK. Reads
``SPECSTREAM_API_KEY`` unless a key is passed explicitly.
"""

from __future__ import annotations

from typing import Any, ClassVar

from web_router.constants import ENV_SPECSTREAM_API_KEY, PROVIDER_SPECSTREAM
from web_router.constants_specstream import (
    MSG_SPECSTREAM_NO_RESULTS,
    SPECSTREAM_API_KEY_PREFIX,
    SPECSTREAM_BASE_URL,
    SPECSTREAM_EXTRACT_PATH,
    SPECSTREAM_SEARCH_NAMES,
    SPECSTREAM_SEARCH_PARAMETERS,
    SPECSTREAM_SEARCH_PATH,
)
from web_router.exceptions import ProviderError
from web_router.providers._http import HttpExtractProvider, HttpSearchProvider
from web_router.providers._utils import build_payload
from web_router.types import (
    ExtractRequest,
    ExtractResponse,
    ExtractedDocument,
    SearchRequest,
    SearchResponse,
    SearchResult,
)

_MAPPED_RESULT_FIELDS = frozenset({"url", "title", "snippet", "score"})
_MAPPED_DOCUMENT_FIELDS = frozenset({"url", "content"})


class SpecStreamProvider(HttpSearchProvider):
    """Routes searches to the SpecStream Search API."""

    name: ClassVar[str] = PROVIDER_SPECSTREAM
    supported_parameters: ClassVar[frozenset[str]] = SPECSTREAM_SEARCH_PARAMETERS

    base_url: ClassVar[str] = SPECSTREAM_BASE_URL
    search_path: ClassVar[str] = SPECSTREAM_SEARCH_PATH
    env_api_key: ClassVar[str] = ENV_SPECSTREAM_API_KEY
    api_key_prefix: ClassVar[str] = SPECSTREAM_API_KEY_PREFIX
    http_method: ClassVar[str] = "GET"

    def _build_payload(self, request: SearchRequest) -> dict[str, Any]:
        return build_payload(request, SPECSTREAM_SEARCH_NAMES, query=request.query)

    def _to_response(self, request: SearchRequest, payload: dict[str, Any]) -> SearchResponse:
        hits = payload.get("results")
        if not isinstance(hits, list):
            raise ProviderError(
                self.name,
                MSG_SPECSTREAM_NO_RESULTS.format(fields=", ".join(sorted(payload)) or "none"),
            )
        return SearchResponse(
            provider=self.name,
            query=request.query,
            results=[self._to_result(hit) for hit in hits if hit.get("url")],
            raw=payload,
        )

    @staticmethod
    def _to_result(hit: dict[str, Any]) -> SearchResult:
        snippet = hit.get("snippet") or ""
        return SearchResult(
            url=hit["url"],
            title=hit.get("title") or "",
            description=snippet,
            content=snippet,
            score=hit.get("score"),
            extra={k: v for k, v in hit.items() if k not in _MAPPED_RESULT_FIELDS},
        )


class SpecStreamExtractProvider(HttpExtractProvider):
    """Routes extracts to the SpecStream Extract API."""

    name: ClassVar[str] = PROVIDER_SPECSTREAM
    base_url: ClassVar[str] = SPECSTREAM_BASE_URL
    extract_path: ClassVar[str] = SPECSTREAM_EXTRACT_PATH
    env_api_key: ClassVar[str] = ENV_SPECSTREAM_API_KEY
    api_key_prefix: ClassVar[str] = SPECSTREAM_API_KEY_PREFIX
    http_method: ClassVar[str] = "POST"

    def _build_payload(self, request: ExtractRequest, urls: list[str]) -> dict[str, Any]:
        return {"urls": urls}

    def _to_response(self, request: ExtractRequest, urls: list[str], payload: dict[str, Any]) -> ExtractResponse:
        results = payload.get("results", [])
        
        documents = [
            ExtractedDocument(
                url=item.get("url", ""),
                content=item.get("content", ""),
                format=request.format or "markdown",
                extra={k: v for k, v in item.items() if k not in _MAPPED_DOCUMENT_FIELDS},
            )
            for item in results
        ]
        
        return ExtractResponse(provider=self.name, documents=documents, raw=[payload])
