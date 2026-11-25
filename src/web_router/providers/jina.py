"""Jina providers: Reader, the keyless route to a page's contents, and Search.

Reader is the one hosted extract backend that needs no credential: it answers
anonymously at 20 requests a minute, and ``JINA_API_KEY`` raises that rather
than unlocking anything. Search is not keyless: ``s.jina.ai`` refuses a call
without ``JINA_API_KEY``. Install with the ``extract`` or ``search`` extra --
each needs ``httpx`` and nothing else.

Two independent knobs decide what Reader returns, and conflating them is the easy
mistake: ``Accept`` chooses between a JSON envelope and the bare page, while
``X-Return-Format`` chooses what is inside it. Both classes always ask for the
envelope, because the bare page carries no title, status or publication date to
normalize from.
"""

from __future__ import annotations

from typing import Any, ClassVar, Literal

from web_router.constants import (
    ENV_JINA_API_KEY,
    PROVIDER_JINA,
)
from web_router.constants_jina import (
    JINA_ACCEPT,
    JINA_API_KEY_PREFIX,
    JINA_BASE_URL,
    JINA_BODY_FIELDS,
    JINA_DEFAULT_DEPTH,
    JINA_DEPTH_HEADERS,
    JINA_EXTRACT_DEFAULT_FORMAT,
    JINA_EXTRACT_FORMATS,
    JINA_EXTRACT_PATH,
    JINA_RETURN_FORMAT_HEADER,
    JINA_SEARCH_BASE_URL,
    JINA_SEARCH_NAMES,
    JINA_SEARCH_PARAMETERS,
    JINA_SEARCH_PATH,
    MSG_JINA_NO_RESULTS,
)
from web_router.exceptions import ConfigurationError, ProviderError
from web_router.providers._http import HttpExtractProvider, HttpSearchProvider
from web_router.providers._search_text import named_result
from web_router.providers._utils import build_payload, compact, field_names
from web_router.types import (
    EXTRACT_FORMAT_ONLY,
    ExtractedDocument,
    ExtractFormat,
    ExtractRequest,
    ExtractResponse,
    SearchDepth,
    SearchRequest,
    SearchResponse,
)

_MAPPED_DATA_FIELDS = frozenset({"url", "title", "publishedTime", "httpStatus", "warning", *JINA_BODY_FIELDS.values()})


class JinaProvider(HttpSearchProvider):
    """Routes searches to Jina Search (``s.jina.ai``).

    Unless ``search_depth`` is ``deep``, each search sends ``X-Respond-With:
    no-content``: Jina then reads no page, and a result is a title, a link, a
    description and sometimes a date. ``deep`` drops the header and each
    result's content is the page Jina read. Jina returns no score.
    """

    name: ClassVar[str] = PROVIDER_JINA
    supported_parameters: ClassVar[frozenset[str]] = JINA_SEARCH_PARAMETERS
    depth_levels: ClassVar[frozenset[SearchDepth]] = frozenset(JINA_DEPTH_HEADERS)

    base_url: ClassVar[str] = JINA_SEARCH_BASE_URL
    search_path: ClassVar[str] = JINA_SEARCH_PATH
    env_api_key: ClassVar[str] = ENV_JINA_API_KEY
    api_key_prefix: ClassVar[str] = JINA_API_KEY_PREFIX
    http_method: ClassVar[Literal["GET", "POST"]] = "GET"

    def _call_kwargs(self, request: SearchRequest) -> dict[str, Any]:
        """The query as parameters; the depth as a header, the only place Jina reads it."""
        headers = JINA_DEPTH_HEADERS[request.search_depth or JINA_DEFAULT_DEPTH]
        return compact({"params": self._build_payload(request), "headers": headers or None})

    def _build_payload(self, request: SearchRequest) -> dict[str, Any]:
        return build_payload(request, JINA_SEARCH_NAMES, q=request.query)

    def _to_response(self, request: SearchRequest, payload: dict[str, Any]) -> SearchResponse:
        hits = payload.get("data")
        if not isinstance(hits, list):
            raise ProviderError(self.name, MSG_JINA_NO_RESULTS.format(fields=field_names(payload)))
        return SearchResponse(
            provider=self.name,
            query=request.query,
            results=[
                named_result(hit, request.search_depth, body="content", date="date") for hit in hits if hit.get("url")
            ],
            raw=payload,
        )


class JinaExtractProvider(HttpExtractProvider):
    """Fetches page contents through Jina Reader."""

    name: ClassVar[str] = PROVIDER_JINA
    supported_parameters: ClassVar[frozenset[str]] = EXTRACT_FORMAT_ONLY
    supported_formats: ClassVar[frozenset[ExtractFormat]] = frozenset(JINA_EXTRACT_FORMATS)
    default_format: ClassVar[ExtractFormat] = JINA_EXTRACT_DEFAULT_FORMAT

    base_url: ClassVar[str] = JINA_BASE_URL
    extract_path: ClassVar[str] = JINA_EXTRACT_PATH
    # Optional rather than required: Reader is open, and a key only lifts the
    # rate limit. HttpExtractProvider raises for a missing key; `_resolve_key`
    # below turns that into no key.
    env_api_key: ClassVar[str | None] = ENV_JINA_API_KEY
    api_key_prefix: ClassVar[str] = JINA_API_KEY_PREFIX

    def __init__(self, **kwargs: Any) -> None:
        """
        Args:
            **kwargs: As :class:`~web_router.providers.HttpExtractProvider`.
                ``api_key`` is optional here and falls back to ``JINA_API_KEY``.
        """
        headers = {"Accept": JINA_ACCEPT, **(kwargs.pop("headers", None) or {})}
        super().__init__(headers=headers, **kwargs)

    def _resolve_key(self, api_key: str | None) -> str | None:
        """Unlike every other provider here, a missing key is not an error."""
        try:
            return super()._resolve_key(api_key)
        except ConfigurationError:
            return None

    def _call_kwargs(self, request: ExtractRequest, urls: list[str]) -> dict[str, Any]:
        """Send the format as a header, which is the only place Reader reads it."""
        return {
            "json": self._build_payload(request, urls),
            "headers": {JINA_RETURN_FORMAT_HEADER: JINA_EXTRACT_FORMATS[self.format_of(request)]},
        }

    def _build_payload(self, request: ExtractRequest, urls: list[str]) -> dict[str, Any]:
        return build_payload(request, url=urls[0])

    def _to_response(self, request: ExtractRequest, urls: list[str], payload: dict[str, Any]) -> ExtractResponse:
        body_format = self.format_of(request)
        data = payload.get("data") or {}
        document = ExtractedDocument(
            url=data.get("url") or urls[0],
            title=data.get("title") or "",
            content=data.get(JINA_BODY_FIELDS[body_format]) or "",
            format=body_format,
            status_code=data.get("httpStatus"),
            published_date=data.get("publishedTime"),
            warnings=[str(data["warning"])] if data.get("warning") else [],
            extra={k: v for k, v in data.items() if k not in _MAPPED_DATA_FIELDS},
        )
        return ExtractResponse(provider=self.name, documents=[document], raw=[payload])
