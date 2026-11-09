"""Tavily extraction, over the Extract API, and what both Tavily classes share.

:class:`TavilyExtractProvider` fetches page contents through the official
``tavily-python`` SDK; :mod:`web_router.providers.tavily`, which searches,
re-exports it and builds its clients, key and deadline-clamped timeout with
the helpers here. Install with the ``extract`` extra. Reads ``TAVILY_API_KEY``
unless a key is passed explicitly, and can run keyless against Tavily's
anonymous tier -- see ``allow_keyless``. An extract sends the URLs and
``format``; everything else its API offers is reachable through
``provider_options``.
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
    TAVILY_SDK_EXTRACT_TIMEOUT,
)
from web_router.exceptions import ConfigurationError, ProviderError
from web_router.providers._deadline import call_budget, current_deadline
from web_router.providers._utils import build_payload, compact, resolve_api_key
from web_router.providers.base import ExtractProvider
from web_router.types import (
    EXTRACT_PARAMETERS,
    ExtractedDocument,
    ExtractFailure,
    ExtractFormat,
    ExtractRequest,
    ExtractResponse,
)

_MAPPED_DOCUMENT_FIELDS = frozenset({"url", "title", TAVILY_EXTRACT_RESULT_FIELD})


def tavily_key(api_key: str | None, *, allow_keyless: bool, provider: str) -> str | None:
    """Tavily's key, or ``None`` to use its anonymous tier when that was asked for."""
    if not allow_keyless:
        return resolve_api_key(api_key, env_var=ENV_TAVILY_API_KEY, provider=provider)
    try:
        return resolve_api_key(api_key, env_var=ENV_TAVILY_API_KEY, provider=provider)
    except ConfigurationError:
        return None


def sdk_clients(
    key: str | None, api_base_url: str | None, kwargs: dict[str, Any]
) -> tuple[TavilyClient, AsyncTavilyClient]:
    """The sync and async SDK clients, each handed the one transport argument it takes."""
    shared = compact({"api_base_url": api_base_url, **kwargs})
    session, client = shared.pop("session", None), shared.pop("client", None)
    # api_key=None is what puts the SDK in keyless mode; it is not the same
    # as omitting the argument, so pass it through either way.
    return (
        TavilyClient(api_key=key, **shared, **compact({"session": session})),
        AsyncTavilyClient(api_key=key, **shared, **compact({"client": client})),
    )


def clamped_timeout(payload: dict[str, Any], sdk_default: float, minimum: float = 0.0) -> dict[str, Any]:
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
            **kwargs: Forwarded to both Tavily clients, but ``session`` (a
                ``requests.Session``) only to the sync one and ``client`` (an
                ``httpx.AsyncClient``) only to the async one.
        """
        key = tavily_key(api_key, allow_keyless=allow_keyless, provider=self.name)
        super().__init__(api_key=key, api_base_url=api_base_url, **kwargs)

        self._timeout = timeout
        self._client, self._async_client = sdk_clients(key, api_base_url, kwargs)

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
        return clamped_timeout(payload, TAVILY_SDK_EXTRACT_TIMEOUT, TAVILY_EXTRACT_MIN_TIMEOUT)

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
