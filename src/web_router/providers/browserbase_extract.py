"""Browserbase extract: page contents over the Fetch API, with a session's saved login.

Talks to ``POST /v1/fetch`` directly, as the session and browse providers in
:mod:`web_router.providers.browserbase` talk to theirs, and reads
``BROWSERBASE_API_KEY`` the same way.

Sessions and Fetch share one saved identity; browse tasks do not map
``profile``. A Browserbase Context holds the cookies and storage a session
ends with, and the session provider maps ``profile`` onto
its id and ``persist_profile`` onto whether the session writes back to it. The
extract provider sends the same id as the Fetch API's ``contextId``, so a login
made in a session serves the fetches that follow. Verified live by hand on
2026-10-29, and by the live tests in ``tests/providers/test_browserbase_extract.py``
on 2026-11-08:
a session opened with ``persist_profile=True`` set a cookie, a fetch naming the
same Context sent it back, and one naming no Context did not --
``open_browser(profile=X, persist_profile=True)``, log in, release, then
``extract(profile=X)``. Fetch never writes cookies back, and a Context has none
to send until a persisting session has ended, so an expired login means
another session.
"""

from __future__ import annotations

from typing import Any, ClassVar

from web_router.constants import (
    ENV_BROWSERBASE_API_KEY,
    PROVIDER_BROWSERBASE,
)
from web_router.constants_browserbase import (
    BROWSERBASE_API_KEY_HEADER,
    BROWSERBASE_BASE_URL,
    BROWSERBASE_EXTRACT_DEFAULT_FORMAT,
    BROWSERBASE_EXTRACT_FORMATS,
    BROWSERBASE_EXTRACT_PATH,
    BROWSERBASE_FETCH_BINARY_ENCODING,
    BROWSERBASE_HTML_CONTENT_TYPES,
    BROWSERBASE_PROFILE_FIELD,
    MSG_BROWSERBASE_FETCH_NO_TEXT,
    MSG_BROWSERBASE_FETCH_NOT_FORMAT,
)
from web_router.providers._http import HttpExtractProvider
from web_router.providers._utils import build_payload
from web_router.types import ExtractedDocument, ExtractFailure, ExtractFormat, ExtractRequest, ExtractResponse


class BrowserbaseExtractProvider(HttpExtractProvider):
    """Fetches page contents through the Browserbase Fetch API.

    A plain HTTP fetch, never a rendered page, so a page built by JavaScript
    comes back as its shell; what Fetch adds is ``profile``, the Context whose
    saved cookies ride along, which is how a page behind a login is read
    without opening a session. Redirects are not followed unless
    ``allowRedirects`` is passed through ``provider_options``, so a login that
    has expired tends to come back as the target's redirect or 401, which is
    reported on the document's ``status_code`` rather than invented into a
    failure: Fetch itself answered, and the body is what the target served.
    """

    name: ClassVar[str] = PROVIDER_BROWSERBASE
    supported_parameters: ClassVar[frozenset[str]] = frozenset({"format", "profile"})
    supported_formats: ClassVar[frozenset[ExtractFormat]] = frozenset(BROWSERBASE_EXTRACT_FORMATS)
    default_format: ClassVar[ExtractFormat] = BROWSERBASE_EXTRACT_DEFAULT_FORMAT

    base_url: ClassVar[str] = BROWSERBASE_BASE_URL
    extract_path: ClassVar[str] = BROWSERBASE_EXTRACT_PATH
    env_api_key: ClassVar[str] = ENV_BROWSERBASE_API_KEY
    api_key_header: ClassVar[str] = BROWSERBASE_API_KEY_HEADER

    def _build_payload(self, request: ExtractRequest, urls: list[str]) -> dict[str, Any]:
        return build_payload(
            request,
            {"profile": BROWSERBASE_PROFILE_FIELD},
            url=urls[0],
            format=BROWSERBASE_EXTRACT_FORMATS[self.format_of(request)],
        )

    def _to_response(self, request: ExtractRequest, urls: list[str], payload: dict[str, Any]) -> ExtractResponse:
        wanted = self.format_of(request)
        error = _unservable(payload, wanted)
        if error is not None:
            return ExtractResponse(
                provider=self.name, failures=[ExtractFailure(url=urls[0], error=error)], raw=[payload]
            )
        document = ExtractedDocument(
            # Fetch does not echo the URL, and with redirects off the one asked
            # for is the one answered.
            url=urls[0],
            content=payload["content"],
            format=wanted,
            status_code=payload.get("statusCode"),
            extra={key: value for key, value in payload.items() if key not in _MAPPED_FETCH_FIELDS},
        )
        return ExtractResponse(provider=self.name, documents=[document], raw=[payload])


def _unservable(payload: dict[str, Any], wanted: ExtractFormat) -> str | None:
    """Why a 200 from Fetch is not a document in ``wanted``, or ``None`` when it is.

    ``raw`` is the target's body unchanged, so a PDF comes back Base64-encoded
    and a JSON endpoint as JSON; labelling either ``html`` would hand the caller
    something the format does not describe.
    """
    content = payload.get("content")
    if not isinstance(content, str):
        return MSG_BROWSERBASE_FETCH_NO_TEXT.format(kind=type(content).__name__)
    content_type = str(payload.get("contentType") or "")
    encoding = str(payload.get("encoding") or "")
    binary = encoding == BROWSERBASE_FETCH_BINARY_ENCODING
    not_html = wanted == "html" and bool(content_type) and not content_type.startswith(BROWSERBASE_HTML_CONTENT_TYPES)
    if binary or not_html:
        return MSG_BROWSERBASE_FETCH_NOT_FORMAT.format(content_type=content_type, encoding=encoding, format=wanted)
    return None


_MAPPED_FETCH_FIELDS = frozenset({"content", "statusCode"})
