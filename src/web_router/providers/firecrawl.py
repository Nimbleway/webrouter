"""Firecrawl provider, over the Scrape API.

Extract only. Talks to the REST endpoint directly rather than through
``firecrawl-py``: web-router uses three fields of this API, which an SDK
dependency would not simplify. Install with the ``extract`` extra.
Reads ``FIRECRAWL_API_KEY`` unless a key is passed explicitly, and can run
keyless against Firecrawl's anonymous tier -- see ``allow_keyless``.

Slim by design: only ``format`` is mapped. Firecrawl's structured extraction,
browser actions, caching and proxy tiers are all reachable through
``provider_options``; mapping one properly is a welcome PR -- see CONTRIBUTING.md.
"""

from __future__ import annotations

from typing import Any, ClassVar

from web_router.constants import (
    ENV_FIRECRAWL_API_KEY,
    FIRECRAWL_API_KEY_PREFIX,
    FIRECRAWL_BASE_URL,
    FIRECRAWL_EXTRACT_DEFAULT_FORMAT,
    FIRECRAWL_EXTRACT_FORMATS,
    FIRECRAWL_EXTRACT_PATH,
    MSG_EXTRACT_UNSUCCESSFUL,
    PROVIDER_FIRECRAWL,
)
from web_router.exceptions import ConfigurationError
from web_router.providers._http import HttpExtractProvider
from web_router.providers._utils import build_payload
from web_router.types import (
    EXTRACT_FORMAT_ONLY,
    ExtractedDocument,
    ExtractFailure,
    ExtractFormat,
    ExtractRequest,
    ExtractResponse,
)

_MAPPED_METADATA_FIELDS = frozenset({"title", "statusCode", "sourceURL"})


class FirecrawlExtractProvider(HttpExtractProvider):
    """Fetches page contents through the Firecrawl Scrape API."""

    name: ClassVar[str] = PROVIDER_FIRECRAWL
    supported_parameters: ClassVar[frozenset[str]] = EXTRACT_FORMAT_ONLY
    supported_formats: ClassVar[frozenset[ExtractFormat]] = frozenset(FIRECRAWL_EXTRACT_FORMATS)
    default_format: ClassVar[ExtractFormat] = FIRECRAWL_EXTRACT_DEFAULT_FORMAT

    base_url: ClassVar[str] = FIRECRAWL_BASE_URL
    extract_path: ClassVar[str] = FIRECRAWL_EXTRACT_PATH
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
            **kwargs: Forwarded to :class:`HttpExtractProvider`.
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
            return ExtractResponse(provider=self.name, failures=[failure], raw=[payload])

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
        return ExtractResponse(provider=self.name, documents=[document], raw=[payload])
