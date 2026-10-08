"""Jina Reader provider, the keyless route to a page's contents.

Extract only, and the one extract backend that needs no credential: Reader
answers anonymously at 20 requests a minute, and ``JINA_API_KEY`` raises that
rather than unlocking anything. Install with
the ``extract`` extra -- it needs ``httpx`` and nothing else.

Two independent knobs decide what comes back, and conflating them is the easy
mistake: ``Accept`` chooses between a JSON envelope and the bare page, while
``X-Return-Format`` chooses what is inside it. This provider always asks for the
envelope, because the bare page carries no title, status or publication date to
normalize from.
"""

from __future__ import annotations

import os
from typing import Any, ClassVar

from web_router.constants import (
    ENV_JINA_API_KEY,
    JINA_ACCEPT,
    JINA_API_KEY_PREFIX,
    JINA_BASE_URL,
    JINA_BODY_FIELDS,
    JINA_EXTRACT_DEFAULT_FORMAT,
    JINA_EXTRACT_FORMATS,
    JINA_EXTRACT_PATH,
    JINA_RETURN_FORMAT_HEADER,
    PROVIDER_JINA,
)
from web_router.providers._http import HttpExtractProvider
from web_router.providers._utils import build_payload
from web_router.types import (
    EXTRACT_PARAMETERS,
    ExtractedDocument,
    ExtractFormat,
    ExtractRequest,
    ExtractResponse,
)

_MAPPED_DATA_FIELDS = frozenset({"url", "title", "publishedTime", "httpStatus", *JINA_BODY_FIELDS.values()})


class JinaExtractProvider(HttpExtractProvider):
    """Fetches page contents through Jina Reader."""

    name: ClassVar[str] = PROVIDER_JINA
    supported_parameters: ClassVar[frozenset[str]] = EXTRACT_PARAMETERS
    supported_formats: ClassVar[frozenset[ExtractFormat]] = frozenset(JINA_EXTRACT_FORMATS)
    default_format: ClassVar[ExtractFormat] = JINA_EXTRACT_DEFAULT_FORMAT

    base_url: ClassVar[str] = JINA_BASE_URL
    extract_path: ClassVar[str] = JINA_EXTRACT_PATH
    # Optional rather than required: Reader is open, and a key only lifts the
    # rate limit. HttpExtractProvider raises for a missing key when this is set,
    # so it stays None and the key arrives through `api_key` or not at all.
    env_api_key: ClassVar[str | None] = None
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
        return api_key or os.environ.get(ENV_JINA_API_KEY)

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
            extra={k: v for k, v in data.items() if k not in _MAPPED_DATA_FIELDS},
        )
        return ExtractResponse(provider=self.name, documents=[document], raw=[payload])
