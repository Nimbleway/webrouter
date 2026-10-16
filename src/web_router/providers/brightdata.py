"""Bright Data provider, over the Web Unlocker REST API.

Extract only: the unlocker fetches a URL through Bright Data's network, it does
not rank the web. Bright Data publishes no Python SDK for this endpoint, so the
wire details are ours to maintain. Install with
the ``extract`` extra.

Needs two settings, not one: ``BRIGHTDATA_API_TOKEN`` and the
``BRIGHTDATA_ZONE`` that routes the request. A zone is created in Bright Data's
console and has no default worth guessing, so a missing one is reported the same
way a missing key is.

Slim by design: only ``format`` is mapped. Rendering, geo-targeting and the rest
are reachable through ``provider_options``; mapping one properly is a welcome PR
-- see CONTRIBUTING.md.
"""

from __future__ import annotations

from typing import Any, ClassVar

from web_router.constants import (
    BRIGHTDATA_API_KEY_PREFIX,
    BRIGHTDATA_BASE_URL,
    BRIGHTDATA_ERROR_HEADER,
    BRIGHTDATA_EXTRACT_DEFAULT_FORMAT,
    BRIGHTDATA_EXTRACT_FORMATS,
    BRIGHTDATA_EXTRACT_PATH,
    BRIGHTDATA_RESPONSE_FORMAT,
    ENV_BRIGHTDATA_API_KEY,
    ENV_BRIGHTDATA_ZONE,
    PROVIDER_BRIGHTDATA,
)
from web_router.providers._http import HttpExtractProvider
from web_router.providers._utils import build_payload, resolve_setting
from web_router.types import (
    EXTRACT_PARAMETERS,
    ExtractedDocument,
    ExtractFailure,
    ExtractFormat,
    ExtractRequest,
    ExtractResponse,
)

_MAPPED_ENVELOPE_FIELDS = frozenset({"body", "status_code"})


class BrightDataExtractProvider(HttpExtractProvider):
    """Fetches page contents through the Bright Data Web Unlocker."""

    name: ClassVar[str] = PROVIDER_BRIGHTDATA
    supported_parameters: ClassVar[frozenset[str]] = EXTRACT_PARAMETERS
    supported_formats: ClassVar[frozenset[ExtractFormat]] = frozenset(BRIGHTDATA_EXTRACT_FORMATS)
    default_format: ClassVar[ExtractFormat] = BRIGHTDATA_EXTRACT_DEFAULT_FORMAT

    base_url: ClassVar[str] = BRIGHTDATA_BASE_URL
    extract_path: ClassVar[str] = BRIGHTDATA_EXTRACT_PATH
    env_api_key: ClassVar[str] = ENV_BRIGHTDATA_API_KEY
    api_key_prefix: ClassVar[str] = BRIGHTDATA_API_KEY_PREFIX

    def __init__(self, *, zone: str | None = None, **kwargs: Any) -> None:
        """
        Args:
            zone: The Web Unlocker zone to route through. Falls back to
                ``BRIGHTDATA_ZONE``.
            **kwargs: As :class:`~web_router.providers.HttpExtractProvider`.
        """
        self._zone = resolve_setting(zone, env_var=ENV_BRIGHTDATA_ZONE, provider=self.name, setting="zone")
        super().__init__(**kwargs)

    def _build_payload(self, request: ExtractRequest, urls: list[str]) -> dict[str, Any]:
        return build_payload(
            request,
            url=urls[0],
            zone=self._zone,
            format=BRIGHTDATA_RESPONSE_FORMAT,
            **BRIGHTDATA_EXTRACT_FORMATS[self.format_of(request)],
        )

    def _to_response(self, request: ExtractRequest, urls: list[str], payload: dict[str, Any]) -> ExtractResponse:
        # The unlocker answers 200 even when it could not reach the page, so a
        # failed fetch arrives as an empty body; only this header tells it from
        # a page that really is empty.
        error = (payload.get("headers") or {}).get(BRIGHTDATA_ERROR_HEADER)
        if error:
            failure = ExtractFailure(url=urls[0], error=str(error))
            return ExtractResponse(provider=self.name, failures=[failure], raw=[payload])

        document = ExtractedDocument(
            # The envelope describes the fetch, not the page, so it carries no
            # URL of its own -- this call's own URL is the only one there is.
            url=urls[0],
            content=payload.get("body") or "",
            format=self.format_of(request),
            status_code=payload.get("status_code"),
            extra={k: v for k, v in payload.items() if k not in _MAPPED_ENVELOPE_FIELDS},
        )
        return ExtractResponse(provider=self.name, documents=[document], raw=[payload])
