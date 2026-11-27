"""Context Search with query-relevant Highlights; no SDK dependency.

Maps only ``max_results``. Highlights are discovery passages, not proof of a
fresh page read; failed or empty Highlights use the search description. The
provider's partial flag and all unmapped fields remain in ``raw``/``extra``.
"""

from __future__ import annotations

from typing import Any, ClassVar

from web_router.constants import ENV_CONTEXT_DEV_API_KEY, PROVIDER_CONTEXT
from web_router.constants_context import (
    CONTEXT_API_KEY_PREFIX,
    CONTEXT_BASE_URL,
    CONTEXT_HIGHLIGHTS_CODES,
    CONTEXT_HIGHLIGHTS_NOT_REQUESTED,
    CONTEXT_HIGHLIGHTS_SUCCESS,
    CONTEXT_MAX_RESULTS,
    CONTEXT_SEARCH_PATH,
    MSG_CONTEXT_FAILED,
)
from web_router.exceptions import ProviderError
from web_router.providers._http import HttpSearchProvider
from web_router.providers._utils import build_payload
from web_router.snippet import as_snippet
from web_router.types import SearchRequest, SearchResponse, SearchResult
from web_router.types_errors import ERROR_KIND_TIMEOUT, classify


class ContextProvider(HttpSearchProvider):
    """Routes searches to Context.dev, reading ``CONTEXT_DEV_API_KEY``."""

    name: ClassVar[str] = PROVIDER_CONTEXT
    supported_parameters: ClassVar[frozenset[str]] = frozenset({"max_results"})
    base_url: ClassVar[str] = CONTEXT_BASE_URL
    search_path: ClassVar[str] = CONTEXT_SEARCH_PATH
    env_api_key: ClassVar[str] = ENV_CONTEXT_DEV_API_KEY
    api_key_prefix: ClassVar[str] = CONTEXT_API_KEY_PREFIX

    def _build_payload(self, request: SearchRequest) -> dict[str, Any]:
        return build_payload(
            request,
            query=request.query,
            numResults=min(request.max_results, CONTEXT_MAX_RESULTS) if request.max_results else None,
            highlightsOptions={"enabled": True},
        )

    def _to_response(self, request: SearchRequest, payload: dict[str, Any]) -> SearchResponse:
        hits = payload.get("results")
        if not isinstance(hits, list):
            raise ProviderError(self.name, MSG_CONTEXT_FAILED)
        return SearchResponse(
            provider=self.name,
            query=request.query,
            results=[self._result(hit) for hit in hits],
            request_id=payload.get("request_id"),
            raw=payload,
        )

    @staticmethod
    def _result(hit: dict[str, Any]) -> SearchResult:
        if not isinstance(hit, dict):
            raise ValueError(MSG_CONTEXT_FAILED)
        url = hit.get("url")
        if not isinstance(url, str):
            raise ValueError(MSG_CONTEXT_FAILED)
        highlights = hit.get("highlights")
        if highlights is None:
            highlights = {}
        if not isinstance(highlights, dict):
            raise ValueError(MSG_CONTEXT_FAILED)
        code = highlights.get("code", CONTEXT_HIGHLIGHTS_NOT_REQUESTED)
        if code not in CONTEXT_HIGHLIGHTS_CODES:
            raise ValueError(MSG_CONTEXT_FAILED)
        passages = highlights.get("highlights")
        if passages is None:
            passages = []
        if not isinstance(passages, list) or not all(isinstance(p, str) for p in passages):
            raise ValueError(MSG_CONTEXT_FAILED)
        description = hit.get("description") or ""
        content = "\n".join(passages).strip() if code == CONTEXT_HIGHLIGHTS_SUCCESS else ""
        return SearchResult(
            url=url,
            title=hit.get("title") or "",
            description=as_snippet(description),
            content=content or description,
            extra={k: v for k, v in hit.items() if k not in {"url", "title", "description"}},
        )

    def _search(self, request: SearchRequest) -> SearchResponse:
        try:
            return super()._search(request)
        except Exception as exc:
            error = self._safe_error(exc)
        # Raise outside the handler: even traceback/context serialization must not retain credential echoes.
        raise error

    async def _asearch(self, request: SearchRequest) -> SearchResponse:
        try:
            return await super()._asearch(request)
        except Exception as exc:
            error = self._safe_error(exc)
        raise error

    def _safe_error(self, exc: Exception) -> ProviderError:
        facts = classify(exc)
        original = TimeoutError(MSG_CONTEXT_FAILED) if facts.kind == ERROR_KIND_TIMEOUT else None
        error = ProviderError(self.name, MSG_CONTEXT_FAILED, original=original, kind=facts.kind)
        error.status_code = facts.status_code
        error.retry_after = facts.retry_after
        return error
