"""SpecStream search and extract providers.

Talk to the REST endpoints directly. Install with the ``search`` or ``extract``
extra. Read ``SPECSTREAM_API_KEY`` unless a key is passed explicitly.

Search maps ``max_results`` onto ``num``; ``passages``, ``speed`` and the other
query fields are reachable through ``provider_options``. Both endpoints answer
``{"data": {...}}``: search with ranked hits in ``data.sources``, extract with
one item per URL in ``data.results``.
"""

from __future__ import annotations

from typing import Any, ClassVar, Literal

from web_router.constants import ENV_SPECSTREAM_API_KEY, PROVIDER_SPECSTREAM
from web_router.constants_specstream import (
    MSG_SPECSTREAM_NO_LIST,
    MSG_SPECSTREAM_PAGE_FAILED,
    SPECSTREAM_API_KEY_PREFIX,
    SPECSTREAM_BASE_URL,
    SPECSTREAM_EXTRACT_DEFAULT_FORMAT,
    SPECSTREAM_EXTRACT_FORMATS,
    SPECSTREAM_EXTRACT_PATH,
    SPECSTREAM_MAX_RESULTS,
    SPECSTREAM_PAGE_OK,
    SPECSTREAM_SEARCH_PARAMETERS,
    SPECSTREAM_SEARCH_PATH,
)
from web_router.exceptions import ProviderError
from web_router.providers._http import HttpExtractProvider, HttpSearchProvider
from web_router.providers._utils import best_text, build_payload
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

_MAPPED_RESULT_FIELDS = frozenset({"url", "title", "snippet", "excerpt", "published"})
_MAPPED_DOCUMENT_FIELDS = frozenset({"url", "title", "text", "status_code", "published"})


def _data_list(name: str, payload: dict[str, Any], field: str) -> list[dict[str, Any]]:
    """``payload["data"][field]``, or a ``ProviderError``: a 200 with no list is a reply this mapping cannot read."""
    data = payload.get("data")
    items = data.get(field) if isinstance(data, dict) else None
    if not isinstance(items, list):
        fields = ", ".join(sorted(data if isinstance(data, dict) else payload)) or "none"
        raise ProviderError(name, MSG_SPECSTREAM_NO_LIST.format(field=field, fields=fields))
    return [item for item in items if isinstance(item, dict)]


class SpecStreamProvider(HttpSearchProvider):
    """Routes searches to the SpecStream Search API.

    A result's ``description`` is its ``snippet``. Its ``content`` is the
    ``excerpt`` of its page when ``passages=true`` is sent through
    ``provider_options``, else the snippet. ``answer``, ``rows`` and the rest of
    the reply stay in ``raw``; ``site``, ``cited`` and the rest of a hit in ``extra``.
    """

    name: ClassVar[str] = PROVIDER_SPECSTREAM
    supported_parameters: ClassVar[frozenset[str]] = SPECSTREAM_SEARCH_PARAMETERS

    base_url: ClassVar[str] = SPECSTREAM_BASE_URL
    search_path: ClassVar[str] = SPECSTREAM_SEARCH_PATH
    env_api_key: ClassVar[str] = ENV_SPECSTREAM_API_KEY
    api_key_prefix: ClassVar[str] = SPECSTREAM_API_KEY_PREFIX
    http_method: ClassVar[Literal["GET", "POST"]] = "GET"

    def _build_payload(self, request: SearchRequest) -> dict[str, Any]:
        num = min(request.max_results, SPECSTREAM_MAX_RESULTS) if request.max_results else None
        return build_payload(request, q=request.query, num=num)

    def _to_response(self, request: SearchRequest, payload: dict[str, Any]) -> SearchResponse:
        hits = _data_list(self.name, payload, "sources")
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
            content=best_text(hit.get("excerpt"), snippet),
            published_date=hit.get("published") or None,
            extra={k: v for k, v in hit.items() if k not in _MAPPED_RESULT_FIELDS},
        )


class SpecStreamExtractProvider(HttpExtractProvider):
    """Routes extracts to the SpecStream Extract API, one call per URL.

    A page's body is its ``text``, in the format asked for. ``read_from``,
    ``emails`` and the rest of an item stay in ``extra``.
    """

    name: ClassVar[str] = PROVIDER_SPECSTREAM
    supported_parameters: ClassVar[frozenset[str]] = EXTRACT_FORMAT_ONLY
    supported_formats: ClassVar[frozenset[ExtractFormat]] = frozenset(SPECSTREAM_EXTRACT_FORMATS)
    default_format: ClassVar[ExtractFormat] = SPECSTREAM_EXTRACT_DEFAULT_FORMAT

    base_url: ClassVar[str] = SPECSTREAM_BASE_URL
    extract_path: ClassVar[str] = SPECSTREAM_EXTRACT_PATH
    env_api_key: ClassVar[str] = ENV_SPECSTREAM_API_KEY
    api_key_prefix: ClassVar[str] = SPECSTREAM_API_KEY_PREFIX

    def _build_payload(self, request: ExtractRequest, urls: list[str]) -> dict[str, Any]:
        return build_payload(request, urls=urls, format=SPECSTREAM_EXTRACT_FORMATS[self.format_of(request)])

    def _to_response(self, request: ExtractRequest, urls: list[str], payload: dict[str, Any]) -> ExtractResponse:
        body_format = self.format_of(request)
        documents: list[ExtractedDocument] = []
        failures: list[ExtractFailure] = []
        for item in _data_list(self.name, payload, "results"):
            url = item.get("url") or ""
            status = item.get("status")
            # The API answers 200 for a page it could not read and says why in status, so that
            # page fails here and auto can try the next backend for it.
            if status != SPECSTREAM_PAGE_OK:
                error = MSG_SPECSTREAM_PAGE_FAILED.format(status=status)
                failures.append(ExtractFailure(url=url, error=error, status_code=item.get("status_code")))
                continue
            documents.append(
                ExtractedDocument(
                    url=url,
                    title=item.get("title") or "",
                    content=item.get("text") or "",
                    format=body_format,
                    status_code=item.get("status_code"),
                    published_date=item.get("published") or None,
                    extra={k: v for k, v in item.items() if k not in _MAPPED_DOCUMENT_FIELDS},
                )
            )
        return ExtractResponse(provider=self.name, documents=documents, failures=failures, raw=[payload])
