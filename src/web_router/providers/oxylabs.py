"""Oxylabs provider, over the Web Scraper API's realtime endpoint.

Extract only: this API fetches a URL you name, it does not rank the web.
Oxylabs publishes no Python SDK for it, so the wire details are ours to
maintain. Install with the ``extract`` extra.

Authenticates with a username and password rather than a key --
``OXYLABS_USERNAME`` and ``OXYLABS_PASSWORD``, which are the API-specific
credentials created under Web Scraper API in the dashboard, not the ones you
sign in with.

Slim by design: only ``format`` is mapped. Rendering, geo-targeting, sessions
and Oxylabs' parsing instructions are all reachable through
``provider_options``; mapping one properly is a welcome PR -- see CONTRIBUTING.md.
"""

from __future__ import annotations

from base64 import b64encode
from typing import Any, ClassVar

from web_router.constants import (
    ENV_OXYLABS_PASSWORD,
    ENV_OXYLABS_USERNAME,
    OXYLABS_BASE_URL,
    OXYLABS_EXTRACT_DEFAULT_FORMAT,
    OXYLABS_EXTRACT_FORMATS,
    OXYLABS_EXTRACT_PATH,
    OXYLABS_MARKDOWN_PARAM,
    OXYLABS_UNIVERSAL_SOURCE,
    PROVIDER_OXYLABS,
)
from web_router.providers._http import HttpExtractProvider
from web_router.providers._utils import build_payload, resolve_setting
from web_router.types import (
    ExtractedDocument,
    ExtractFormat,
    ExtractRequest,
    ExtractResponse,
)

_MAPPED_RESULT_FIELDS = frozenset({"url", "content", "status_code", "type"})


class OxylabsExtractProvider(HttpExtractProvider):
    """Fetches page contents through the Oxylabs Web Scraper API."""

    name: ClassVar[str] = PROVIDER_OXYLABS
    supported_parameters: ClassVar[frozenset[str]] = frozenset({"format"})
    supported_formats: ClassVar[frozenset[ExtractFormat]] = frozenset(OXYLABS_EXTRACT_FORMATS)
    default_format: ClassVar[ExtractFormat] = OXYLABS_EXTRACT_DEFAULT_FORMAT

    base_url: ClassVar[str] = OXYLABS_BASE_URL
    extract_path: ClassVar[str] = OXYLABS_EXTRACT_PATH
    env_api_key: ClassVar[str] = ENV_OXYLABS_PASSWORD

    def __init__(self, *, username: str | None = None, **kwargs: Any) -> None:
        """
        Args:
            username: Oxylabs API username. Falls back to ``OXYLABS_USERNAME``.
            **kwargs: As :class:`~web_router.providers.HttpExtractProvider`;
                ``api_key`` is the password and falls back to
                ``OXYLABS_PASSWORD``.
        """
        # Resolved before super(), which needs it to build the auth header.
        self._username = resolve_setting(username, env_var=ENV_OXYLABS_USERNAME, provider=self.name, setting="username")
        super().__init__(**kwargs)

    def _auth_headers(self, key: str) -> dict[str, str]:
        """Oxylabs takes HTTP Basic credentials, not a bearer token."""
        credentials = b64encode(f"{self._username}:{key}".encode()).decode()
        return {self.api_key_header: f"Basic {credentials}"}

    def _build_payload(self, request: ExtractRequest, urls: list[str]) -> dict[str, Any]:
        body_format = self.format_of(request)
        return build_payload(
            request,
            source=OXYLABS_UNIVERSAL_SOURCE,
            url=urls[0],
            # Asking for markdown adds a result rather than replacing one, so
            # there is nothing to send for the default HTML.
            **({OXYLABS_MARKDOWN_PARAM: True} if body_format == "markdown" else {}),
        )

    def _to_response(self, request: ExtractRequest, urls: list[str], payload: dict[str, Any]) -> ExtractResponse:
        body_format = self.format_of(request)
        wanted = OXYLABS_EXTRACT_FORMATS[body_format]
        documents = [
            ExtractedDocument(
                url=result.get("url") or urls[0],
                content=result.get("content") or "",
                format=body_format,
                status_code=result.get("status_code"),
                extra={k: v for k, v in result.items() if k not in _MAPPED_RESULT_FIELDS},
            )
            # Chosen by tag, never by position -- see OXYLABS_EXTRACT_FORMATS.
            for result in payload.get("results") or []
            if result.get("type") == wanted
        ]

        return ExtractResponse(provider=self.name, documents=documents, raw=[payload])
