"""Firecrawl providers, over the Search and Scrape APIs.

Talks to the REST endpoints directly rather than through ``firecrawl-py``:
web-router uses a few fields of each, which an SDK dependency would not
simplify. :class:`FirecrawlProvider` searches; :class:`FirecrawlExtractProvider`
reads a page. Install with the ``search`` or ``extract`` extra. Both read
``FIRECRAWL_API_KEY`` unless a key is passed explicitly, and can run keyless
against Firecrawl's anonymous tier -- see ``allow_keyless``.

Slim by design: search maps ``max_results`` and ``search_depth``, extract only
``format``. Firecrawl's time and location filters, structured extraction,
browser actions, caching and proxy tiers are all reachable through
``provider_options``; mapping one properly is a welcome PR -- see CONTRIBUTING.md.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, ClassVar

from web_router.constants import (
    ENV_FIRECRAWL_API_KEY,
    MSG_EXTRACT_UNSUCCESSFUL,
    PROVIDER_FIRECRAWL,
)
from web_router.constants_firecrawl import (
    FIRECRAWL_API_KEY_PREFIX,
    FIRECRAWL_BASE_URL,
    FIRECRAWL_DEPTH,
    FIRECRAWL_EXTRACT_DEFAULT_FORMAT,
    FIRECRAWL_EXTRACT_FORMATS,
    FIRECRAWL_EXTRACT_PATH,
    FIRECRAWL_MAX_LIMIT,
    FIRECRAWL_SEARCH_BODY_FIELD,
    FIRECRAWL_SEARCH_PARAMETERS,
    FIRECRAWL_SEARCH_PATH,
    FIRECRAWL_WEB_RESULTS,
    MSG_FIRECRAWL_NO_RESULTS,
    MSG_FIRECRAWL_SEARCH_UNSUCCESSFUL,
)
from web_router.exceptions import ConfigurationError, ProviderError
from web_router.providers._cost import keyless_cost
from web_router.providers._http import HttpExtractProvider, HttpSearchProvider, _HttpProvider
from web_router.providers._search_text import named_result
from web_router.providers._utils import build_payload, field_names, mapped
from web_router.types import (
    EXTRACT_FORMAT_ONLY,
    ExtractedDocument,
    ExtractFailure,
    ExtractFormat,
    ExtractRequest,
    ExtractResponse,
    SearchDepth,
    SearchRequest,
    SearchResponse,
)

_MAPPED_METADATA_FIELDS = frozenset({"title", "statusCode", "sourceURL"})


class _FirecrawlAccount(_HttpProvider):
    """The host, the key and the keyless tier both Firecrawl classes share."""

    base_url: ClassVar[str] = FIRECRAWL_BASE_URL
    env_api_key: ClassVar[str] = ENV_FIRECRAWL_API_KEY
    api_key_prefix: ClassVar[str] = FIRECRAWL_API_KEY_PREFIX

    def __init__(self, *, allow_keyless: bool = False, **kwargs: Any) -> None:
        """
        Args:
            allow_keyless: Run without a credential when none is available, on
                Firecrawl's keyless tier, which caps requests and credits per IP
                per day and answers 429 past either. Off by default: a missing
                key on a provider the caller named should say so, not quietly
                downgrade the account it reads under.
            **kwargs: Forwarded to the HTTP provider base.
        """
        # Set before the base class resolves the key, which reads it.
        self._allow_keyless = allow_keyless
        super().__init__(**kwargs)

    def _resolve_key(self, api_key: str | None) -> str | None:
        try:
            return super()._resolve_key(api_key)
        except ConfigurationError:
            if self._allow_keyless:
                return None
            raise


class FirecrawlProvider(_FirecrawlAccount, HttpSearchProvider):
    """Routes searches to the Firecrawl Search API.

    A result's description is the opening of Firecrawl's ``description``, which
    is often the page's markdown rather than a snippet; its content is that
    whole description, or the page Firecrawl scraped when ``search_depth`` is
    ``deep``. Firecrawl returns no score or date.
    """

    name: ClassVar[str] = PROVIDER_FIRECRAWL
    supported_parameters: ClassVar[frozenset[str]] = FIRECRAWL_SEARCH_PARAMETERS
    depth_levels: ClassVar[frozenset[SearchDepth]] = frozenset(FIRECRAWL_DEPTH)
    search_path: ClassVar[str] = FIRECRAWL_SEARCH_PATH

    def _build_payload(self, request: SearchRequest) -> dict[str, Any]:
        # A copy, so a body merged into later can never change the table every search reads.
        depth = deepcopy(mapped(FIRECRAWL_DEPTH, request.search_depth) or {})
        return build_payload(
            request,
            query=request.query,
            limit=min(request.max_results, FIRECRAWL_MAX_LIMIT) if request.max_results else None,
            **depth,
        )

    def _to_response(self, request: SearchRequest, payload: dict[str, Any]) -> SearchResponse:
        if payload.get("success") is False:
            error = payload.get("error") or payload.get("warning") or MSG_EXTRACT_UNSUCCESSFUL
            raise ProviderError(self.name, MSG_FIRECRAWL_SEARCH_UNSUCCESSFUL.format(error=error))
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ProviderError(self.name, MSG_FIRECRAWL_NO_RESULTS.format(fields=field_names(payload)))
        # No web list is a search that found no pages: `sources` can ask for news or images only, kept in raw.
        hits = data.get(FIRECRAWL_WEB_RESULTS) or []
        return SearchResponse(
            provider=self.name,
            query=request.query,
            results=[
                named_result(hit, request.search_depth, body=FIRECRAWL_SEARCH_BODY_FIELD)
                for hit in hits
                if hit.get("url")
            ],
            request_id=payload.get("id"),
            raw=payload,
            **keyless_cost(self),
        )


class FirecrawlExtractProvider(_FirecrawlAccount, HttpExtractProvider):
    """Fetches page contents through the Firecrawl Scrape API."""

    name: ClassVar[str] = PROVIDER_FIRECRAWL
    supported_parameters: ClassVar[frozenset[str]] = EXTRACT_FORMAT_ONLY
    supported_formats: ClassVar[frozenset[ExtractFormat]] = frozenset(FIRECRAWL_EXTRACT_FORMATS)
    default_format: ClassVar[ExtractFormat] = FIRECRAWL_EXTRACT_DEFAULT_FORMAT

    extract_path: ClassVar[str] = FIRECRAWL_EXTRACT_PATH

    def _build_payload(self, request: ExtractRequest, urls: list[str]) -> dict[str, Any]:
        return build_payload(
            request,
            url=urls[0],
            formats=[FIRECRAWL_EXTRACT_FORMATS[self.format_of(request)]],
        )

    def _to_response(self, request: ExtractRequest, urls: list[str], payload: dict[str, Any]) -> ExtractResponse:
        body_format = self.format_of(request)
        data = payload.get("data") or {}
        metadata = data.get("metadata") or {}

        # Firecrawl answers 200 and says in `success` whether it got the page,
        # so a refused fetch arrives as a successful call carrying no content.
        if not payload.get("success"):
            error = payload.get("error") or payload.get("warning") or metadata.get("error")
            failure = ExtractFailure(url=urls[0], error=str(error or MSG_EXTRACT_UNSUCCESSFUL))
            return ExtractResponse(provider=self.name, failures=[failure], raw=[payload], **keyless_cost(self))

        document = ExtractedDocument(
            url=metadata.get("sourceURL") or urls[0],
            title=metadata.get("title") or "",
            content=data.get(FIRECRAWL_EXTRACT_FORMATS[body_format]) or "",
            format=body_format,
            status_code=metadata.get("statusCode"),
            # Metadata stays nested rather than merged in: Firecrawl repeats
            # some of its keys at both levels, and flattening would drop one.
            extra={
                "metadata": {k: v for k, v in metadata.items() if k not in _MAPPED_METADATA_FIELDS},
                **{k: v for k, v in data.items() if k not in {"metadata", *FIRECRAWL_EXTRACT_FORMATS.values()}},
            },
        )
        return ExtractResponse(provider=self.name, documents=[document], raw=[payload], **keyless_cost(self))
