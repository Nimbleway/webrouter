"""Tavily providers, backed by the official ``tavily-python`` SDK.

Search and extract are separate classes over one SDK client. Install with
the ``search`` extra or ``[extract]``. Both read
``TAVILY_API_KEY`` unless a key is passed explicitly, and both can run keyless
against Tavily's anonymous tier -- see ``allow_keyless``.
:class:`TavilyExtractProvider` and what both classes share live in
:mod:`web_router.providers.tavily_extract`; the class is re-exported here.

Search maps every shared option but ``effort``: Tavily's latency tiers share
the ``search_depth`` field our depth uses. ``country`` reaches the country names
Tavily lists, ``locale`` a strict language filter and ``recency`` a time range
that drops undated pages. An extract sends the URLs and ``format``. Everything
else its API offers is reachable through ``provider_options``.
"""

from __future__ import annotations

from typing import Any, ClassVar

from web_router.constants import (
    PROVIDER_TAVILY,
    TAVILY_PARAMS_BY_DEPTH,
    TAVILY_SDK_SEARCH_TIMEOUT,
)
from web_router.constants_tavily import (
    MSG_TAVILY_COUNTRY,
    TAVILY_COUNTRIES,
    TAVILY_GENERAL_TOPIC,
    TAVILY_LANGUAGE_FILTER,
    TAVILY_PARAM_NAMES,
    TAVILY_RECENCY_FILTER,
    TAVILY_SEARCH_PARAMETERS,
    TAVILY_TIME_RANGE,
)
from web_router.providers._language import language_code
from web_router.providers._search_text import lite_body
from web_router.providers._utils import best_text, build_payload, mapped
from web_router.providers.base import SearchProvider
from web_router.providers.tavily_extract import TavilyExtractProvider as TavilyExtractProvider
from web_router.providers.tavily_extract import clamped_timeout, sdk_clients, tavily_key
from web_router.types import (
    SearchRequest,
    SearchResponse,
    SearchResult,
)

_MAPPED_RESULT_FIELDS = frozenset({"url", "title", "content", "raw_content", "score", "published_date"})


def search_fields(request: SearchRequest, *, timeout: float | None = None) -> dict[str, Any]:
    """Tavily's search fields for ``request``: its SDK's ``search()`` keywords, which are its REST body's fields."""
    depth = mapped(TAVILY_PARAMS_BY_DEPTH, request.search_depth)
    return build_payload(
        request,
        TAVILY_PARAM_NAMES,
        query=request.query,
        timeout=timeout,
        country=TAVILY_COUNTRIES[request.country.upper()] if request.country else None,
        # The language alone, as the ISO 639-1 code Tavily documents.
        language=language_code(request.locale) if request.locale else None,
        time_range=mapped(TAVILY_TIME_RANGE, request.recency),
        **(depth or {}),
        **(TAVILY_LANGUAGE_FILTER if request.locale else {}),
        **(TAVILY_RECENCY_FILTER if request.recency else {}),
    )


class TavilyProvider(SearchProvider):
    """Routes searches to the Tavily Search API."""

    name: ClassVar[str] = PROVIDER_TAVILY
    supported_parameters: ClassVar[frozenset[str]] = TAVILY_SEARCH_PARAMETERS
    refusal_reasons: ClassVar[dict[str, str]] = {"country": MSG_TAVILY_COUNTRY}

    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_base_url: str | None = None,
        timeout: float | None = None,
        allow_keyless: bool = False,
        **kwargs: Any,
    ) -> None:
        """
        Args:
            api_key: Tavily key. Falls back to ``TAVILY_API_KEY``.
            allow_keyless: Run without a credential when none is available.
                Tavily's SDK supports this and the service rate-limits it. Off
                by default: a missing key on a provider the caller named should
                say so, not quietly downgrade the account it searches under.
            api_base_url: Override the API host.
            timeout: Per-request timeout in seconds. Tavily takes this per call
                rather than on the client, so it is applied to every search.
            **kwargs: Forwarded to both Tavily clients, but ``session`` (a
                ``requests.Session``) only to the sync one and ``client`` (an
                ``httpx.AsyncClient``) only to the async one.
        """
        key = tavily_key(api_key, allow_keyless=allow_keyless, provider=self.name)
        super().__init__(api_key=key, api_base_url=api_base_url, **kwargs)

        self._timeout = timeout
        self._client, self._async_client = sdk_clients(key, api_base_url, kwargs)

    def _search(self, request: SearchRequest) -> SearchResponse:
        payload = self._client.search(**self._build_payload(request))
        return self._to_response(request, payload)

    async def _asearch(self, request: SearchRequest) -> SearchResponse:
        payload = await self._async_client.search(**self._build_payload(request))
        return self._to_response(request, payload)

    def close(self) -> None:
        self._client.close()

    async def aclose(self) -> None:
        await self._async_client.close()

    @classmethod
    def refused_options(cls, request: SearchRequest) -> dict[str, Any]:
        """A ``country`` Tavily does not list, or one asked for on a topic other than general."""
        topic = request.provider_options.get("topic", TAVILY_GENERAL_TOPIC)
        if request.country and (request.country.upper() not in TAVILY_COUNTRIES or topic != TAVILY_GENERAL_TOPIC):
            return {"country": request.country}
        return {}

    def _build_payload(self, request: SearchRequest) -> dict[str, Any]:
        """Translate a normalized request into Tavily ``search()`` keyword arguments."""
        return clamped_timeout(search_fields(request, timeout=self._timeout), TAVILY_SDK_SEARCH_TIMEOUT)

    def _to_response(self, request: SearchRequest, payload: dict[str, Any]) -> SearchResponse:
        results = []
        for item in payload.get("results") or []:
            # Tavily's `content` is the matched snippet, up to three chunks at
            # `basic`; the full page, when asked for, arrives as `raw_content`.
            snippet = item.get("content") or ""
            results.append(
                SearchResult(
                    url=item.get("url", ""),
                    title=item.get("title") or "",
                    description=lite_body(snippet, request.search_depth),
                    content=lite_body(best_text(item.get("raw_content"), snippet), request.search_depth),
                    score=item.get("score"),
                    published_date=item.get("published_date"),
                    extra={k: v for k, v in item.items() if k not in _MAPPED_RESULT_FIELDS},
                )
            )

        return SearchResponse(
            provider=self.name,
            query=request.query,
            results=results,
            answer=payload.get("answer"),
            request_id=payload.get("request_id"),
            raw=payload,
        )
