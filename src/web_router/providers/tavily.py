"""Tavily providers, backed by the official ``tavily-python`` SDK.

Search and extract are separate classes over one SDK client. Install with
the ``search`` extra or ``[extract]``. Both read
``TAVILY_API_KEY`` unless a key is passed explicitly, and both can run keyless
against Tavily's anonymous tier -- see ``allow_keyless``.

Search maps every shared option but ``effort``: Tavily's latency tiers share
the ``search_depth`` field our depth uses. ``country`` reaches the country names
Tavily lists, ``locale`` a strict language filter and ``recency`` a time range
that drops undated pages. An extract sends the URLs and ``format``. Everything
else its API offers is reachable through ``provider_options``.
"""

from __future__ import annotations

from typing import Any, ClassVar

from tavily import AsyncTavilyClient, TavilyClient

from web_router.constants import (
    ENV_TAVILY_API_KEY,
    MSG_TAVILY_TOO_LITTLE_TIME,
    PROVIDER_TAVILY,
    TAVILY_EXTRACT_DEFAULT_FORMAT,
    TAVILY_EXTRACT_FORMATS,
    TAVILY_EXTRACT_MIN_TIMEOUT,
    TAVILY_EXTRACT_RESULT_FIELD,
    TAVILY_PARAMS_BY_DEPTH,
    TAVILY_SDK_EXTRACT_TIMEOUT,
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
from web_router.exceptions import ConfigurationError, ProviderError
from web_router.providers._deadline import call_budget, current_deadline
from web_router.providers._language import language_code
from web_router.providers._search_text import lite_body
from web_router.providers._utils import best_text, build_payload, compact, mapped, resolve_api_key
from web_router.providers.base import ExtractProvider, SearchProvider
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

_MAPPED_RESULT_FIELDS = frozenset({"url", "title", "content", "raw_content", "score", "published_date"})
_MAPPED_DOCUMENT_FIELDS = frozenset({"url", "title", TAVILY_EXTRACT_RESULT_FIELD})


def _resolve_key(api_key: str | None, *, allow_keyless: bool, provider: str) -> str | None:
    """Tavily's key, or ``None`` to use its anonymous tier when that was asked for."""
    if not allow_keyless:
        return resolve_api_key(api_key, env_var=ENV_TAVILY_API_KEY, provider=provider)
    try:
        return resolve_api_key(api_key, env_var=ENV_TAVILY_API_KEY, provider=provider)
    except ConfigurationError:
        return None


def _clamped(payload: dict[str, Any], sdk_default: float, minimum: float = 0.0) -> dict[str, Any]:
    """``payload`` with its ``timeout`` clamped to the time a deadline in force leaves.

    Tavily takes its timeout per call, so this is where a deadline reaches it.
    Applied after ``provider_options`` is merged in, so a longer timeout there
    cannot outlast the deadline; unset, the SDK's own default is clamped. With
    no deadline the payload is returned as built. Less time left than
    ``minimum``, the shortest timeout the endpoint accepts, is Tavily's own
    limit, so this member fails before sending -- not the deadline, which
    has not passed: an ``auto`` chain goes on to its next member.
    """
    if current_deadline() is not None:
        configured = payload.get("timeout")
        payload["timeout"] = call_budget(float(configured) if configured is not None else sdk_default)
        if payload["timeout"] < minimum:
            raise ProviderError(
                PROVIDER_TAVILY, MSG_TAVILY_TOO_LITTLE_TIME.format(left=payload["timeout"], minimum=minimum)
            )
    return payload


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
            **kwargs: Forwarded to both Tavily clients.
        """
        key = _resolve_key(api_key, allow_keyless=allow_keyless, provider=self.name)
        super().__init__(api_key=key, api_base_url=api_base_url, **kwargs)

        self._timeout = timeout
        client_kwargs = compact({"api_base_url": api_base_url, **kwargs})
        # api_key=None is what puts the SDK in keyless mode; it is not the same
        # as omitting the argument, so pass it through either way.
        self._client = TavilyClient(api_key=key, **client_kwargs)
        self._async_client = AsyncTavilyClient(api_key=key, **client_kwargs)

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
        depth = mapped(TAVILY_PARAMS_BY_DEPTH, request.search_depth)
        payload = build_payload(
            request,
            TAVILY_PARAM_NAMES,
            query=request.query,
            timeout=self._timeout,
            country=TAVILY_COUNTRIES[request.country.upper()] if request.country else None,
            # The language alone, as the ISO 639-1 code Tavily documents.
            language=language_code(request.locale) if request.locale else None,
            time_range=mapped(TAVILY_TIME_RANGE, request.recency),
            **(depth or {}),
            **(TAVILY_LANGUAGE_FILTER if request.locale else {}),
            **(TAVILY_RECENCY_FILTER if request.recency else {}),
        )
        return _clamped(payload, TAVILY_SDK_SEARCH_TIMEOUT)

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


class TavilyExtractProvider(ExtractProvider):
    """Fetches page contents through the Tavily Extract API."""

    name: ClassVar[str] = PROVIDER_TAVILY
    supported_parameters: ClassVar[frozenset[str]] = EXTRACT_PARAMETERS
    supported_formats: ClassVar[frozenset[ExtractFormat]] = frozenset(TAVILY_EXTRACT_FORMATS)
    default_format: ClassVar[ExtractFormat] = TAVILY_EXTRACT_DEFAULT_FORMAT
    batches_urls: ClassVar[bool] = True

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
                Verified live: Tavily's extract endpoint answers anonymously,
                rate-limited. Off by default, for the same reason as on search.
            api_base_url: Override the API host.
            timeout: Per-request timeout in seconds. Tavily takes this per call
                rather than on the client, so it is applied to every extract.
            **kwargs: Forwarded to both Tavily clients.
        """
        key = _resolve_key(api_key, allow_keyless=allow_keyless, provider=self.name)
        super().__init__(api_key=key, api_base_url=api_base_url, **kwargs)

        self._timeout = timeout
        client_kwargs = compact({"api_base_url": api_base_url, **kwargs})
        # api_key=None is what puts the SDK in keyless mode; it is not the same
        # as omitting the argument, so pass it through either way.
        self._client = TavilyClient(api_key=key, **client_kwargs)
        self._async_client = AsyncTavilyClient(api_key=key, **client_kwargs)

    def _extract(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
        payload = self._client.extract(**self._build_payload(request, urls))
        return self._to_response(request, urls, payload)

    async def _aextract(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
        payload = await self._async_client.extract(**self._build_payload(request, urls))
        return self._to_response(request, urls, payload)

    def close(self) -> None:
        self._client.close()

    async def aclose(self) -> None:
        await self._async_client.close()

    def _build_payload(self, request: ExtractRequest, urls: list[str]) -> dict[str, Any]:
        """Translate a normalized request into Tavily ``extract()`` keyword arguments."""
        payload = build_payload(
            request,
            urls=urls,
            format=TAVILY_EXTRACT_FORMATS[self.format_of(request)],
            timeout=self._timeout,
        )
        return _clamped(payload, TAVILY_SDK_EXTRACT_TIMEOUT, TAVILY_EXTRACT_MIN_TIMEOUT)

    def _to_response(self, request: ExtractRequest, urls: list[str], payload: dict[str, Any]) -> ExtractResponse:
        body_format = self.format_of(request)
        documents = [
            ExtractedDocument(
                url=item.get("url", ""),
                title=item.get("title") or "",
                content=item.get(TAVILY_EXTRACT_RESULT_FIELD) or "",
                format=body_format,
                extra={k: v for k, v in item.items() if k not in _MAPPED_DOCUMENT_FIELDS},
            )
            for item in payload.get("results") or []
        ]

        return ExtractResponse(
            provider=self.name,
            documents=documents,
            # Tavily reports the URLs it could not fetch rather than failing the
            # call, which is the behaviour the base class gives every backend.
            failures=[
                ExtractFailure(url=item.get("url", ""), error=item.get("error") or "")
                for item in payload.get("failed_results") or []
            ],
            request_id=payload.get("request_id"),
            raw=[payload],
        )
