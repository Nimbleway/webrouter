"""Oxylabs provider, over either of its two APIs: the Web Scraper API's realtime endpoint, or the Web API.

Extract only: both fetch a URL you name, they do not rank the web. Oxylabs
publishes no Python SDK for them, so the wire details are ours to maintain.
Install with the ``extract`` extra.

Oxylabs sells the two with separate credentials. The Web Scraper API takes a
username and password -- ``OXYLABS_USERNAME`` and ``OXYLABS_PASSWORD``, the
API-specific ones created under Web Scraper API in the dashboard, not the ones
you sign in with. The Web API takes a key, ``OXYLABS_WEB_API_KEY``, the one
its live data reads too. Either makes ``oxylabs`` an extract backend; with
both, the Web Scraper API serves.

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
from web_router.constants_live_data_vendors import (
    ENV_OXYLABS_WEB_API_KEY,
    OXYLABS_API_KEY_PREFIX,
    OXYLABS_SCRAPE_PREFIX,
    OXYLABS_WEB_API_BASE_URL,
    OXYLABS_WEB_API_OUTPUT_PARAM,
)
from web_router.constants_mode_parts_extract import OXYLABS_EXTRACT_TIERS, OXYLABS_WEB_API_EXTRACT_TIERS
from web_router.constants_oxylabs_classic import MSG_OXYLABS_EXTRACT_NO_CREDENTIALS
from web_router.exceptions import ConfigurationError
from web_router.providers._http import HttpExtractProvider
from web_router.providers._utils import build_payload, credential_given, resolve_setting
from web_router.providers.base import ExtractProvider
from web_router.types import (
    EXTRACT_FORMAT_ONLY,
    ExtractedDocument,
    ExtractFormat,
    ExtractRequest,
    ExtractResponse,
)

_MAPPED_RESULT_FIELDS = frozenset({"url", "content", "status_code", "type"})


def basic_auth(username: str, password: str) -> str:
    """The Web Scraper API's HTTP Basic credentials, not a bearer token: extract's and live data's alike."""
    return "Basic " + b64encode(f"{username}:{password}".encode()).decode()


class OxylabsRealtimeExtractProvider(HttpExtractProvider):
    """Fetches page contents through the Oxylabs Web Scraper API."""

    name: ClassVar[str] = PROVIDER_OXYLABS
    tiers = OXYLABS_EXTRACT_TIERS
    supported_parameters: ClassVar[frozenset[str]] = EXTRACT_FORMAT_ONLY
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
        return {self.api_key_header: basic_auth(self._username, key)}

    def _build_payload(self, request: ExtractRequest, urls: list[str]) -> dict[str, Any]:
        body_format = self.format_of(request)
        return build_payload(
            request,
            None,
            self,
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


class OxylabsWebApiExtractProvider(HttpExtractProvider):
    """Fetches page contents through the Oxylabs Web API's scrape endpoint, on a Web API key."""

    name: ClassVar[str] = PROVIDER_OXYLABS
    tiers = OXYLABS_WEB_API_EXTRACT_TIERS
    supported_parameters: ClassVar[frozenset[str]] = EXTRACT_FORMAT_ONLY
    supported_formats: ClassVar[frozenset[ExtractFormat]] = frozenset(OXYLABS_EXTRACT_FORMATS)
    default_format: ClassVar[ExtractFormat] = OXYLABS_EXTRACT_DEFAULT_FORMAT

    base_url: ClassVar[str] = OXYLABS_WEB_API_BASE_URL
    extract_path: ClassVar[str] = OXYLABS_SCRAPE_PREFIX
    env_api_key: ClassVar[str] = ENV_OXYLABS_WEB_API_KEY
    api_key_prefix: ClassVar[str] = OXYLABS_API_KEY_PREFIX

    def _build_payload(self, request: ExtractRequest, urls: list[str]) -> dict[str, Any]:
        return build_payload(
            request, None, self, url=urls[0], **{OXYLABS_WEB_API_OUTPUT_PARAM: [self.format_of(request)]}
        )

    def _to_response(self, request: ExtractRequest, urls: list[str], payload: dict[str, Any]) -> ExtractResponse:
        body_format = self.format_of(request)
        documents = []
        for result in payload.get("results") or []:
            metadata = result.get("metadata") or {}
            status = ((metadata.get("statuses") or {}).get("http_request") or {}).get("code")
            documents.append(
                ExtractedDocument(
                    url=metadata.get("url") or urls[0],
                    content=result.get(body_format) or "",
                    format=body_format,
                    status_code=status,
                )
            )
        return ExtractResponse(provider=self.name, documents=documents, raw=[payload])


class OxylabsExtractProvider(ExtractProvider):
    """Oxylabs' extract, over whichever of its two APIs the caller holds a credential for."""

    name: ClassVar[str] = PROVIDER_OXYLABS
    tiers = OXYLABS_EXTRACT_TIERS
    supported_parameters: ClassVar[frozenset[str]] = EXTRACT_FORMAT_ONLY
    supported_formats: ClassVar[frozenset[ExtractFormat]] = frozenset(OXYLABS_EXTRACT_FORMATS)
    default_format: ClassVar[ExtractFormat] = OXYLABS_EXTRACT_DEFAULT_FORMAT

    apis: ClassVar[tuple[type[HttpExtractProvider], ...]] = (
        OxylabsRealtimeExtractProvider,
        OxylabsWebApiExtractProvider,
    )
    """The two APIs a call may go to, the first preferred when both credentials are set: what a contract records."""

    def __init__(
        self,
        *,
        username: str | None = None,
        api_key: str | None = None,
        web_api_key: str | None = None,
        **kwargs: Any,
    ) -> None:
        """
        Args:
            username: The Web Scraper API username. Falls back to ``OXYLABS_USERNAME``.
            api_key: The Web Scraper API password. Falls back to ``OXYLABS_PASSWORD``.
            web_api_key: The Web API key. Falls back to ``OXYLABS_WEB_API_KEY``.
            **kwargs: Passed to the API that serves: ``timeout``, ``base_url`` and the ``httpx`` options.

        Raises:
            ConfigurationError: when neither credential is set, naming both.
        """
        super().__init__(**kwargs)
        realtime = credential_given(username, ENV_OXYLABS_USERNAME) and credential_given(api_key, ENV_OXYLABS_PASSWORD)
        if realtime:
            self.api: HttpExtractProvider = OxylabsRealtimeExtractProvider(username=username, api_key=api_key, **kwargs)
        elif credential_given(web_api_key, ENV_OXYLABS_WEB_API_KEY):
            self.api = OxylabsWebApiExtractProvider(api_key=web_api_key, **kwargs)
        else:
            raise ConfigurationError(MSG_OXYLABS_EXTRACT_NO_CREDENTIALS)

    @property
    def holds_credential(self) -> bool:
        """The serving API's, which holds the key: ``auto`` reads it before warning that a key was refused."""
        return self.api.holds_credential

    def _build_payload(self, request: ExtractRequest, urls: list[str]) -> dict[str, Any]:
        """What the serving API sends for ``request``: its contract's body."""
        return self.api._build_payload(request, urls)

    def _extract(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
        return self.api._extract(request, urls)

    async def _aextract(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
        return await self.api._aextract(request, urls)

    def close(self) -> None:
        self.api.close()

    async def aclose(self) -> None:
        await self.api.aclose()
