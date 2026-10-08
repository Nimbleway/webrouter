"""Brave Search provider, over the Web Search API.

Brave publishes no Python SDK, so this talks to the REST endpoint directly.
Install with the ``search`` extra. Reads ``BRAVE_SEARCH_API_KEY``
unless a key is passed explicitly.

Slim by design: only ``max_results`` is mapped. ``search_depth`` is absent
because the API has no content-depth control -- it returns a description and a
few extra snippets, and that is all. Everything else is reachable through
``provider_options``; mapping one properly is a welcome PR -- see CONTRIBUTING.md.
"""

from __future__ import annotations

from typing import Any, ClassVar, Literal

from web_router.constants import (
    BRAVE_API_KEY_HEADER,
    BRAVE_BASE_URL,
    BRAVE_HIGHLIGHT_TAGS,
    BRAVE_PARAM_NAMES,
    BRAVE_SEARCH_PATH,
    ENV_BRAVE_API_KEY,
    PROVIDER_BRAVE,
)
from web_router.providers._http import HttpSearchProvider
from web_router.providers._utils import build_payload
from web_router.types import SearchRequest, SearchResponse, SearchResult

_MAPPED_RESULT_FIELDS = frozenset({"url", "title", "description", "extra_snippets", "page_age"})


def _unmark(text: str) -> str:
    """Strip the ``<strong>`` markers Brave wraps query terms in.

    They are Brave's presentation markup, not part of the page, and every other
    provider returns plain text here.
    """
    for tag in BRAVE_HIGHLIGHT_TAGS:
        text = text.replace(tag, "")
    return text


class BraveProvider(HttpSearchProvider):
    """Routes searches to the Brave Web Search API."""

    name: ClassVar[str] = PROVIDER_BRAVE
    supported_parameters: ClassVar[frozenset[str]] = frozenset(BRAVE_PARAM_NAMES)

    base_url: ClassVar[str] = BRAVE_BASE_URL
    search_path: ClassVar[str] = BRAVE_SEARCH_PATH
    env_api_key: ClassVar[str] = ENV_BRAVE_API_KEY
    api_key_header: ClassVar[str] = BRAVE_API_KEY_HEADER
    http_method: ClassVar[Literal["GET", "POST"]] = "GET"

    def _build_payload(self, request: SearchRequest) -> dict[str, Any]:
        return build_payload(request, BRAVE_PARAM_NAMES, q=request.query)

    def _to_response(self, request: SearchRequest, payload: dict[str, Any]) -> SearchResponse:
        hits = (payload.get("web") or {}).get("results") or []
        return SearchResponse(
            provider=self.name,
            query=request.query,
            results=[self._to_result(hit) for hit in hits],
            raw=payload,
        )

    @staticmethod
    def _to_result(hit: dict[str, Any]) -> SearchResult:
        description = _unmark(hit.get("description") or "")
        # Brave has no full-text mode; the extra snippets are the most body it offers.
        snippets = [_unmark(snippet) for snippet in hit.get("extra_snippets") or []]
        return SearchResult(
            url=hit.get("url", ""),
            title=_unmark(hit.get("title") or ""),
            description=description,
            content="\n\n".join([description, *snippets]).strip(),
            published_date=hit.get("page_age"),
            extra={k: v for k, v in hit.items() if k not in _MAPPED_RESULT_FIELDS},
        )
