"""Keenable providers, over the Search and Fetch APIs.

Search and extract. Talks to the REST endpoints directly: Keenable publishes no
Python SDK. Install with the ``search`` extra or ``extract``. Both read
``KEENABLE_API_KEY`` unless a key is passed explicitly, and both can run keyless
against Keenable's ``/public`` endpoints -- see ``allow_keyless``.

Search maps ``max_results``, ``search_depth`` (as ``snippet_max_length``), a
single ``include_domains`` entry (as ``site``), ``recency`` (as
``published_after``) and ``effort`` (as ``mode``); extract maps only ``format``.
Everything else -- search's other date filters, Fetch's ``live`` and ``prompt``
-- is reachable through ``provider_options``.
"""

from __future__ import annotations

from typing import Any, ClassVar, Literal

from web_router.constants import (
    ENV_KEENABLE_API_KEY,
    EXTRACT_MAX_CHARS,
    KEENABLE_API_KEY_HEADER,
    KEENABLE_BASE_URL,
    KEENABLE_FETCH_PATH,
    KEENABLE_FETCH_PUBLIC_PATH,
    KEENABLE_SEARCH_PATH,
    KEENABLE_SEARCH_PUBLIC_PATH,
    KEENABLE_TITLE,
    KEENABLE_TITLE_HEADER,
    MSG_EXTRACT_UNSUCCESSFUL,
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
from web_router.exceptions import ConfigurationError
from web_router.providers._http import HttpExtractProvider, HttpSearchProvider, _HttpProvider
from web_router.providers._utils import as_snippet, best_text, build_payload
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

_MAPPED_RESULT_FIELDS = frozenset({"url", "title", "description", "snippet", "published_at"})
_MAPPED_DOCUMENT_FIELDS = frozenset({"url", "title", "content"})


class _KeenableKeyless(_HttpProvider):
    """The keyless switch both Keenable classes share.

    Without a key a call goes to the ``/public`` twin of its endpoint, which
    takes the same parameters and answers the same shape, and names the
    calling app in a header in place of the key.
    """

    def __init__(self, *, allow_keyless: bool = False, **kwargs: Any) -> None:
        """
        Args:
            allow_keyless: Run without a credential when none is available, on
                Keenable's keyless endpoints, which are rate-limited per IP on a
                pool shared with everyone else on it. Off by default: a missing
                key on a provider the caller named should say so, not quietly
                downgrade the account it searches under.
            **kwargs: Forwarded to the HTTP base class.
        """
        # Set before the base class resolves the key, which reads both.
        self._allow_keyless = allow_keyless
        self._keyless = False
        super().__init__(**kwargs)
        if self._keyless:
            # The clients are built on first use, so this reaches both. A caller's own header wins.
            self._client_kwargs["headers"].setdefault(KEENABLE_TITLE_HEADER, KEENABLE_TITLE)

    def _resolve_key(self, api_key: str | None) -> str | None:
        try:
            return super()._resolve_key(api_key)
        except ConfigurationError:
            if not self._allow_keyless:
                raise
            self._keyless = True
            return None


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
            snippet_max_length=KEENABLE_SNIPPET_CHARS.get(request.search_depth) if request.search_depth else None,
            site=request.include_domains[0] if request.include_domains else None,
            published_after=KEENABLE_PUBLISHED_AFTER[request.recency] if request.recency else None,
            mode=KEENABLE_MODE_BY_EFFORT[request.effort] if request.effort else None,
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


class KeenableExtractProvider(_KeenableKeyless, HttpExtractProvider):
    """Fetches page contents through the Keenable Fetch API, as markdown.

    ``published_at`` is left in ``extra`` untranslated: Keenable documents it
    as an ISO 8601 string and answers with a Unix timestamp.
    """

    name: ClassVar[str] = PROVIDER_KEENABLE
    supported_parameters: ClassVar[frozenset[str]] = EXTRACT_PARAMETERS
    supported_formats: ClassVar[frozenset[ExtractFormat]] = frozenset({"markdown"})
    # Fetch has no format field: it returns markdown alone.
    default_format: ClassVar[ExtractFormat] = "markdown"

    base_url: ClassVar[str] = KEENABLE_BASE_URL
    extract_path: ClassVar[str] = KEENABLE_FETCH_PATH
    public_extract_path: ClassVar[str] = KEENABLE_FETCH_PUBLIC_PATH
    env_api_key: ClassVar[str] = ENV_KEENABLE_API_KEY
    api_key_header: ClassVar[str] = KEENABLE_API_KEY_HEADER
    http_method: ClassVar[Literal["GET", "POST"]] = "GET"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if self._keyless:
            self.extract_path = self.public_extract_path  # type: ignore[misc]

    def _build_payload(self, request: ExtractRequest, urls: list[str]) -> dict[str, Any]:
        return build_payload(
            request,
            url=urls[0],
            # Unset, Fetch cuts a page at 50,000 characters and appends a notice.
            max_chars=EXTRACT_MAX_CHARS,
        )

    def _to_response(self, request: ExtractRequest, urls: list[str], payload: dict[str, Any]) -> ExtractResponse:
        if "content" not in payload:
            failure = ExtractFailure(url=urls[0], error=str(payload.get("message") or MSG_EXTRACT_UNSUCCESSFUL))
            return ExtractResponse(provider=self.name, failures=[failure], raw=[payload])

        document = ExtractedDocument(
            url=payload.get("url") or urls[0],
            title=payload.get("title") or "",
            content=payload.get("content") or "",
            format=self.format_of(request),
            extra={k: v for k, v in payload.items() if k not in _MAPPED_DOCUMENT_FIELDS},
        )
        return ExtractResponse(provider=self.name, documents=[document], raw=[payload])
