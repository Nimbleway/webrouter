"""The remote router's reply, checked before it is parsed: a 200 of JSON, uncompressed, under the size cap."""

from __future__ import annotations

from typing import Any

from web_router.constants_remote import (
    REMOTE_CONTENT_ENCODING_HEADER,
    REMOTE_CONTENT_TYPE_HEADER,
    REMOTE_HTTP_OK,
    REMOTE_IDENTITY_ENCODING,
    REMOTE_JSON_TYPE,
    REMOTE_MAX_RESPONSE_BYTES,
    REMOTE_MEDIA_PARAMS,
    REMOTE_WHY_CONTENT_TYPE,
    REMOTE_WHY_ENCODING,
    REMOTE_WHY_STATUS,
    REMOTE_WHY_TOO_LARGE,
)
from web_router.routing.remote_errors import RemoteRouterError, RemoteRouterInvalid


def check_head(response: Any, host: str) -> None:
    """Refuse a reply that is not a 200 of ``application/json`` in the identity encoding.

    Raises:
        RemoteRouterError: for any status but 200.
        RemoteRouterInvalid: for another content type, or a compressed body.
    """
    if response.status_code != REMOTE_HTTP_OK:
        raise RemoteRouterError(host, REMOTE_WHY_STATUS.format(status=response.status_code))
    header = response.headers.get(REMOTE_CONTENT_TYPE_HEADER, "")
    media = header.partition(REMOTE_MEDIA_PARAMS)[0].strip().lower()
    if media != REMOTE_JSON_TYPE:
        raise RemoteRouterInvalid(host, REMOTE_WHY_CONTENT_TYPE)
    coding = response.headers.get(REMOTE_CONTENT_ENCODING_HEADER, "").strip().lower()
    if coding not in ("", REMOTE_IDENTITY_ENCODING):  # asked for identity; a compressed answer is not read
        raise RemoteRouterInvalid(host, REMOTE_WHY_ENCODING)


async def read_capped(response: Any, host: str) -> bytes:
    """The answer's bytes as sent, refused past the size cap: identity only, so nothing grows past it.

    Raises:
        RemoteRouterInvalid: once the body passes the cap.
    """
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_raw():
        size += len(chunk)
        if size > REMOTE_MAX_RESPONSE_BYTES:
            raise RemoteRouterInvalid(host, REMOTE_WHY_TOO_LARGE.format(limit=REMOTE_MAX_RESPONSE_BYTES))
        chunks.append(chunk)
    return b"".join(chunks)
