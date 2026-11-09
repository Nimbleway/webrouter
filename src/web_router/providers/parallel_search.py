"""Parallel search, over the Search API's ``/v1/search``.

Talks to the REST endpoint directly rather than through ``parallel-web``. Reads
``PARALLEL_API_KEY`` unless a key is passed explicitly. Calls ``/v1/search``
with ``mode="fast"`` unless ``effort`` picks another mode, and maps
``max_results``, ``search_depth``, the domain filters, ``country`` and
``recency`` under ``advanced_settings``. Everything else is reachable through
``provider_options``, merged into ``advanced_settings`` rather than replacing it.
"""

from __future__ import annotations

from typing import Any, ClassVar

from web_router.constants import (
    ENV_PARALLEL_API_KEY,
    PARALLEL_API_KEY_HEADER,
    PARALLEL_BASE_URL,
    PROVIDER_PARALLEL,
)
from web_router.constants_parallel import (
    MSG_PARALLEL_COUNTRY,
    MSG_PARALLEL_DOMAINS,
    MSG_PARALLEL_TURBO_PATHS,
    PARALLEL_ADVANCED_NAMES,
    PARALLEL_ADVANCED_SETTINGS,
    PARALLEL_AFTER_DATE,
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
from web_router.providers._http import HttpSearchProvider
from web_router.providers._search_text import as_snippet, lite_body, refused_domains
from web_router.providers._utils import build_payload, compact, mapped
from web_router.types import (
    SearchDepth,
    SearchRequest,
    SearchResponse,
    SearchResult,
)

_MAPPED_RESULT_FIELDS = frozenset({"url", "title", "excerpts", "publish_date"})


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
