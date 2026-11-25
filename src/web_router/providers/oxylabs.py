"""Oxylabs providers, over the Web Scraper API's realtime endpoint.

:class:`OxylabsExtractProvider` fetches a URL you name; :class:`OxylabsProvider`
searches by fetching one page of Google through the ``google_search`` source,
parsed. Oxylabs publishes no Python SDK for this API, so the wire details are
ours to maintain. Install with the ``extract`` or ``search`` extra.

Authenticates with a username and password rather than a key --
``OXYLABS_USERNAME`` and ``OXYLABS_PASSWORD``, which are the API-specific
credentials created under Web Scraper API in the dashboard, not the ones you
sign in with.

Slim by design: extract maps only ``format``, search only ``max_results``.
Rendering, geo-targeting, sessions and Oxylabs' parsing instructions are all
reachable through ``provider_options``; mapping one properly is a welcome PR --
see CONTRIBUTING.md.
"""

from __future__ import annotations

from base64 import b64encode
from typing import Any, ClassVar

from web_router.constants import (
    ENV_OXYLABS_PASSWORD,
    ENV_OXYLABS_USERNAME,
    PROVIDER_OXYLABS,
)
from web_router.constants_live_data_vendors import OXYLABS_FAILED_FETCH_FLOOR
from web_router.constants_mode_parts_extract import OXYLABS_EXTRACT_TIERS
from web_router.constants_oxylabs import (
    MSG_OXYLABS_SEARCH_NOT_PARSED,
    OXYLABS_BASE_URL,
    OXYLABS_EXTRACT_DEFAULT_FORMAT,
    OXYLABS_EXTRACT_FORMATS,
    OXYLABS_EXTRACT_PATH,
    OXYLABS_MARKDOWN_PARAM,
    OXYLABS_ORGANIC,
    OXYLABS_SEARCH_NAMES,
    OXYLABS_SEARCH_PARAMETERS,
    OXYLABS_SEARCH_SOURCE,
    OXYLABS_UNIVERSAL_SOURCE,
)
from web_router.constants_oxylabs_classic import OXYLABS_PARSE_STATUS, OXYLABS_PARSED
from web_router.exceptions import ProviderError
from web_router.providers._http import HttpExtractProvider, HttpSearchProvider, _HttpProvider
from web_router.providers._search_text import named_result
from web_router.providers._utils import build_payload, field_names, resolve_setting
from web_router.types import (
    EXTRACT_FORMAT_ONLY,
    ExtractedDocument,
    ExtractFormat,
    ExtractRequest,
    ExtractResponse,
    SearchRequest,
    SearchResponse,
)

_MAPPED_RESULT_FIELDS = frozenset({"url", "content", "status_code", "type"})


def basic_auth(username: str, password: str) -> str:
    """The Web Scraper API's HTTP Basic credentials, not a bearer token: extract's and live data's alike."""
    return "Basic " + b64encode(f"{username}:{password}".encode()).decode()


class _OxylabsRealtime(_HttpProvider):
    """The realtime host and the username-and-password credential both Oxylabs classes share."""

    base_url: ClassVar[str] = OXYLABS_BASE_URL
    env_api_key: ClassVar[str] = ENV_OXYLABS_PASSWORD

    def __init__(self, *, username: str | None = None, web_api_key: str | None = None, **kwargs: Any) -> None:
        """
        Args:
            username: Oxylabs API username. Falls back to ``OXYLABS_USERNAME``.
            web_api_key: Accepted and unused: it is the Web API key Oxylabs'
                live data reads, from the same ``provider_config["oxylabs"]``
                entry; search and extract run on the Realtime credentials.
            **kwargs: Forwarded to the HTTP provider base; ``api_key`` is the
                password and falls back to ``OXYLABS_PASSWORD``.
        """
        del web_api_key
        # Resolved before super(), which needs it to build the auth header.
        self._username = resolve_setting(username, env_var=ENV_OXYLABS_USERNAME, provider=self.name, setting="username")
        super().__init__(**kwargs)

    def _auth_headers(self, key: str) -> dict[str, str]:
        return {self.api_key_header: basic_auth(self._username, key)}


class OxylabsProvider(_OxylabsRealtime, HttpSearchProvider):
    """Routes searches to Google through the Oxylabs Web Scraper API.

    A result is one of the page's organic results: its title, link and
    Google's snippet (``desc``), which is both its description and its
    content. Paid results and the rest of the page stay in ``raw``. No score
    or date is invented.
    """

    name: ClassVar[str] = PROVIDER_OXYLABS
    supported_parameters: ClassVar[frozenset[str]] = OXYLABS_SEARCH_PARAMETERS
    search_path: ClassVar[str] = OXYLABS_EXTRACT_PATH

    def _build_payload(self, request: SearchRequest) -> dict[str, Any]:
        return build_payload(
            request, OXYLABS_SEARCH_NAMES, source=OXYLABS_SEARCH_SOURCE, query=request.query, parse=True
        )

    def _to_response(self, request: SearchRequest, payload: dict[str, Any]) -> SearchResponse:
        organic = self._google_page(payload).get(OXYLABS_ORGANIC)
        hits = [hit for hit in organic if isinstance(hit, dict) and hit.get("url")] if isinstance(organic, list) else []
        job = payload.get("job")
        job_id = job.get("id") if isinstance(job, dict) else None
        return SearchResponse(
            provider=self.name,
            query=request.query,
            results=[named_result(hit, snippet="desc") for hit in hits][: request.max_results],
            request_id=str(job_id) if job_id is not None else None,
            raw=payload,
        )

    def _google_page(self, payload: dict[str, Any]) -> dict[str, Any]:
        """The parsed Google page Oxylabs answered with.

        Raises:
            ProviderError: for no result, a fetch that failed, or a page the parser could not read --
                an error, not a search that found nothing.
        """
        pages = payload.get("results")
        first = pages[0] if isinstance(pages, list) and pages and isinstance(pages[0], dict) else {}
        found = first.get("content")
        content: dict[str, Any] = found if isinstance(found, dict) else {}
        status, code, page = first.get("status_code"), content.get(OXYLABS_PARSE_STATUS), content.get("results")
        fetched = not (isinstance(status, int) and status >= OXYLABS_FAILED_FETCH_FLOOR)
        if fetched and (code is None or code in OXYLABS_PARSED) and isinstance(page, dict):
            return page
        fields = field_names(payload)
        raise ProviderError(self.name, MSG_OXYLABS_SEARCH_NOT_PARSED.format(status=status, code=code, fields=fields))


class OxylabsExtractProvider(_OxylabsRealtime, HttpExtractProvider):
    """Fetches page contents through the Oxylabs Web Scraper API."""

    name: ClassVar[str] = PROVIDER_OXYLABS
    tiers = OXYLABS_EXTRACT_TIERS
    supported_parameters: ClassVar[frozenset[str]] = EXTRACT_FORMAT_ONLY
    supported_formats: ClassVar[frozenset[ExtractFormat]] = frozenset(OXYLABS_EXTRACT_FORMATS)
    default_format: ClassVar[ExtractFormat] = OXYLABS_EXTRACT_DEFAULT_FORMAT

    extract_path: ClassVar[str] = OXYLABS_EXTRACT_PATH

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
