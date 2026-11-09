"""Keenable providers, over the Search and Fetch APIs.

Search and extract. Talks to the REST endpoints directly: Keenable publishes no
Python SDK. Install with the ``search`` extra or ``extract``. Both read
``KEENABLE_API_KEY`` unless a key is passed explicitly, and both can run keyless
against Keenable's ``/public`` endpoints -- see ``allow_keyless``.
:class:`KeenableExtractProvider` and that keyless switch live in
:mod:`web_router.providers.keenable_extract`; the class is re-exported here.

Search maps ``max_results``, ``search_depth`` (as ``snippet_max_length``), a
single ``include_domains`` entry (as ``site``), ``recency`` (as
``published_after``) and ``effort`` (as ``mode``); extract maps only ``format``.
Everything else -- search's other date filters, Fetch's ``live`` and ``prompt``
-- is reachable through ``provider_options``.
"""

from __future__ import annotations

from typing import Any, ClassVar

from web_router.constants import (
    ENV_KEENABLE_API_KEY,
    KEENABLE_API_KEY_HEADER,
    KEENABLE_BASE_URL,
    KEENABLE_SEARCH_PATH,
    KEENABLE_SEARCH_PUBLIC_PATH,
    PROVIDER_KEENABLE,
)
from web_router.constants_keenable import (
    KEENABLE_MODE_BY_EFFORT,
    KEENABLE_PARAM_NAMES,
    KEENABLE_PUBLISHED_AFTER,
    KEENABLE_SEARCH_PARAMETERS,
    KEENABLE_SNIPPET_CHARS,
    MSG_KEENABLE_ONE_SITE,
)
from web_router.providers._http import HttpSearchProvider
from web_router.providers._search_text import as_snippet
from web_router.providers._utils import best_text, build_payload, mapped
from web_router.providers.keenable_extract import KeenableExtractProvider as KeenableExtractProvider
from web_router.providers.keenable_extract import _KeenableKeyless
from web_router.types import (
    SearchRequest,
    SearchResponse,
    SearchResult,
)

_MAPPED_RESULT_FIELDS = frozenset({"url", "title", "description", "snippet", "published_at"})


class KeenableProvider(_KeenableKeyless, HttpSearchProvider):
    """Routes searches to the Keenable Search API.

    Each result carries a snippet of its page and, often, an empty
    ``description``; the snippet is the content, and ``description`` falls back
    to its head. Keenable states no relevance score, so ``score`` stays unset.
    Its longest snippet is 10,000 characters, so ``deep`` is refused, and it
    filters to one site, so a second ``include_domains`` entry is refused.
    """

    name: ClassVar[str] = PROVIDER_KEENABLE
    supported_parameters: ClassVar[frozenset[str]] = KEENABLE_SEARCH_PARAMETERS
    depth_levels = frozenset(KEENABLE_SNIPPET_CHARS)
    refusal_reasons: ClassVar[dict[str, str]] = {"include_domains": MSG_KEENABLE_ONE_SITE}

    base_url: ClassVar[str] = KEENABLE_BASE_URL
    search_path: ClassVar[str] = KEENABLE_SEARCH_PATH
    public_search_path: ClassVar[str] = KEENABLE_SEARCH_PUBLIC_PATH
    env_api_key: ClassVar[str] = ENV_KEENABLE_API_KEY
    api_key_header: ClassVar[str] = KEENABLE_API_KEY_HEADER

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if self._keyless:
            self.search_path = self.public_search_path  # type: ignore[misc]

    def _build_payload(self, request: SearchRequest) -> dict[str, Any]:
        return build_payload(
            request,
            KEENABLE_PARAM_NAMES,
            query=request.query,
            snippet_max_length=mapped(KEENABLE_SNIPPET_CHARS, request.search_depth),
            site=request.include_domains[0] if request.include_domains else None,
            published_after=mapped(KEENABLE_PUBLISHED_AFTER, request.recency),
            mode=mapped(KEENABLE_MODE_BY_EFFORT, request.effort),
        )

    @classmethod
    def refused_options(cls, request: SearchRequest) -> dict[str, Any]:
        domains = request.include_domains or []
        return {"include_domains": domains} if len(domains) > 1 else {}

    def _to_response(self, request: SearchRequest, payload: dict[str, Any]) -> SearchResponse:
        return SearchResponse(
            provider=self.name,
            query=request.query,
            results=[self._to_result(hit) for hit in payload.get("results") or []],
            raw=payload,
        )

    @staticmethod
    def _to_result(hit: dict[str, Any]) -> SearchResult:
        snippet = hit.get("snippet") or ""
        description = hit.get("description") or ""
        # Documented as ISO 8601, but Fetch already answers with a Unix integer;
        # one here would fail the whole search, so it stays in extra instead.
        published = hit.get("published_at")
        extra = {k: v for k, v in hit.items() if k not in _MAPPED_RESULT_FIELDS}
        if published is not None and not isinstance(published, str):
            extra["published_at"] = published
        # acquired_at is when Keenable indexed the page, not when it was
        # published, so it stays in extra rather than posing as published_date.
        return SearchResult(
            url=hit.get("url", ""),
            title=hit.get("title") or "",
            description=description or as_snippet(snippet),
            content=best_text(snippet, description),
            published_date=published if isinstance(published, str) else None,
            extra=extra,
        )
