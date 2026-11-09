"""Keenable extraction, over the Fetch API, and the keyless switch both Keenable classes share.

:class:`KeenableExtractProvider` fetches one page as markdown;
:mod:`web_router.providers.keenable`, which searches, re-exports it and builds
its search on the same keyless switch. Install with the ``extract`` extra.
Reads ``KEENABLE_API_KEY`` unless a key is passed explicitly, and can run
keyless against Keenable's ``/public`` endpoints -- see ``allow_keyless``.
Only ``format`` is mapped; Fetch's ``live`` and ``prompt`` are reachable
through ``provider_options``.
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
    KEENABLE_TITLE,
    KEENABLE_TITLE_HEADER,
    MSG_EXTRACT_UNSUCCESSFUL,
    PROVIDER_KEENABLE,
)
from web_router.exceptions import ConfigurationError
from web_router.providers._http import HttpExtractProvider, _HttpProvider
from web_router.providers._utils import build_payload
from web_router.types import (
    EXTRACT_PARAMETERS,
    ExtractedDocument,
    ExtractFailure,
    ExtractFormat,
    ExtractRequest,
    ExtractResponse,
)

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
