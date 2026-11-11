"""The remote router's settings, read once when it is built: httpx, its timeout, its key and the headers it sends.

The URL has a module of its own, :mod:`.remote_url`.
"""

from __future__ import annotations

import math
import os
from typing import Any, TypeGuard

from web_router.constants_remote import (
    ENV_REMOTE_ROUTER_API_KEY,
    EXTRA_REMOTE,
    MSG_REMOTE_KEY,
    MSG_REMOTE_NO_HTTPX,
    MSG_REMOTE_TIMEOUT,
    REMOTE_AUTH_HEADER,
    REMOTE_AUTH_VALUE,
    REMOTE_CONTENT_TYPE,
)
from web_router.exceptions import ConfigurationError


def load_httpx() -> Any:
    """The httpx module, imported only now: the ``remote`` extra installs it."""
    try:
        import httpx
    except ImportError as exc:
        raise ConfigurationError(MSG_REMOTE_NO_HTTPX.format(extra=EXTRA_REMOTE)) from exc
    return httpx


def checked_timeout(value: object) -> float:
    """``value`` as seconds, when it is a positive, finite number and not a bool."""
    if isinstance(value, bool) or not isinstance(value, int | float) or not 0 < value < math.inf:
        raise ConfigurationError(MSG_REMOTE_TIMEOUT.format(value=value))
    return float(value)


def visible_ascii(key: object) -> TypeGuard[str]:
    """Whether ``key`` can ride in a header: a string of visible ASCII characters, no space."""
    return isinstance(key, str) and key.isascii() and key.isprintable() and " " not in key


def bearer_key(api_key: object) -> str | None:
    """``api_key``, else ``WEB_ROUTER_REMOTE_ROUTER_API_KEY``, else ``None``.

    A key that is not visible ASCII is refused now, never echoed: as a header it would fail every call, and a
    non-ASCII one would carry the whole ``Authorization`` value in its ``UnicodeEncodeError``.
    """
    key = os.environ.get(ENV_REMOTE_ROUTER_API_KEY) if api_key is None else api_key
    if key is not None and not visible_ascii(key):
        raise ConfigurationError(MSG_REMOTE_KEY)
    return key or None


def request_headers(key: str | None) -> dict[str, str]:
    """The headers every request carries: its content type, and the bearer key when there is one."""
    return {**REMOTE_CONTENT_TYPE, **({REMOTE_AUTH_HEADER: REMOTE_AUTH_VALUE.format(key=key)} if key else {})}
