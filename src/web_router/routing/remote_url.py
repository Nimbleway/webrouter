"""The ``remote`` router's URL check: ``https``, or plain ``http`` to this machine only, read as httpx reads it.

Experimental. Apart from :mod:`.remote` so each stays small. The check runs on
the :class:`httpx.URL` the client connects with, so no parser difference can
let it see one host while httpx dials another; a URL a browser could read two
ways is refused outright, as :mod:`.url_policy` refuses one. Plain ``http`` is
allowed only to the exact name ``localhost`` and literal loopback addresses
(127.0.0.0/8, ``::1``): a ``*.localhost`` name goes to a resolver, which may
answer anything, and ``0.0.0.0`` is no loopback address. Credentials, a query
or a fragment in the URL are refused, so no secret rides in it; no message
echoes the URL.
"""

from __future__ import annotations

import ipaddress
from typing import Any

from web_router.constants_remote import (
    MSG_REMOTE_HTTPS,
    MSG_REMOTE_NO_URL,
    MSG_REMOTE_URL,
    MSG_REMOTE_URL_PARTS,
    REMOTE_LOOPBACK_NAME,
    REMOTE_LOOPBACK_SCHEME,
    REMOTE_SECURE_SCHEME,
)
from web_router.exceptions import ConfigurationError
from web_router.routing.url_policy import _ambiguous


def _loopback_literal(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def checked_url(url: object, httpx: Any) -> str:
    """``url`` when it is ``https``, or ``http`` to ``localhost`` or a loopback address, with nothing secret in it.

    Raises:
        ConfigurationError: for no URL, one httpx cannot parse or a browser could read two ways (credentials
            included), one with no host, a query or a fragment, or a plain-``http`` one to any other host. Never
            echoing the URL.
    """
    if not isinstance(url, str) or not url.strip():
        raise ConfigurationError(MSG_REMOTE_NO_URL)
    if _ambiguous(url):
        raise ConfigurationError(MSG_REMOTE_URL)
    try:
        parsed = httpx.URL(url)
    except (httpx.InvalidURL, TypeError, ValueError):
        raise ConfigurationError(MSG_REMOTE_URL) from None
    scheme, host = parsed.scheme.lower(), parsed.host
    if not host or scheme not in (REMOTE_SECURE_SCHEME, REMOTE_LOOPBACK_SCHEME):
        raise ConfigurationError(MSG_REMOTE_URL)
    if "?" in url or "#" in url:  # credentials are an @ in the authority, which _ambiguous refuses
        raise ConfigurationError(MSG_REMOTE_URL_PARTS)
    if scheme != REMOTE_SECURE_SCHEME and not (host == REMOTE_LOOPBACK_NAME or _loopback_literal(host)):
        raise ConfigurationError(MSG_REMOTE_HTTPS)
    return str(parsed)
