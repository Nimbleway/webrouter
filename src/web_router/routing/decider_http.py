"""The built-in deciders' transport: one checked endpoint, its key read once, and one JSON POST per call.

Experimental. Held to the ``remote`` router's rules, with its helpers: ``https``
only (plain ``http`` only to loopback), no credentials, query or fragment in the
base URL, no redirect followed, no proxy or other setting read from the
environment, no retry, and an answer of ``application/json`` under 256 KiB.
Each call opens its own client and closes it, so nothing outlives the call or
its event loop. A failure is reported by error class or HTTP status and the
scheme and host, never the URL's path or the key: the record shows only its
code, and with ``fail_on_router_error=True`` this error is the ``RouterError``'s
``__cause__``. The call itself is billed by its vendor, outside any budget. A key is read only from the variable
``api_key_env`` names, or given as ``api_key``; a preset's variable (such as
``OPENAI_API_KEY``) is sent only to that vendor's own host.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

from web_router._urls import host_of
from web_router.constants_decider import (
    DECIDER_MAX_RESPONSE_BYTES,
    KEY_HOMES,
    MSG_DECIDER_ENV_NAME,
    MSG_DECIDER_KEY,
    MSG_DECIDER_KEY_HOME,
    MSG_DECIDER_KEY_UNSET,
    MSG_DECIDER_NO_KEY_ENV,
    MSG_DECIDER_TEXT,
)
from web_router.constants_remote import (
    REMOTE_AUTH_HEADER,
    REMOTE_AUTH_VALUE,
    REMOTE_CONTENT_TYPE,
    REMOTE_WHY_NOT_JSON,
    REMOTE_WHY_TRANSPORT,
)
from web_router.exceptions import ConfigurationError
from web_router.routing.remote_config import checked_timeout, load_httpx, visible_ascii
from web_router.routing.remote_errors import RemoteRouterError, RemoteRouterInvalid, RemoteRouterTimeout
from web_router.routing.remote_reply import check_head, read_capped
from web_router.routing.remote_url import checked_url, is_loopback


@dataclass(frozen=True)
class Endpoint:
    """Where a built-in decider posts, and with what: read once, when the router is built."""

    url: str
    host: str
    """The URL's scheme and host, all an error shows of it."""
    domain: str
    """The host alone, which names the router in the record and its reasons: a reason's URLs are cut."""
    timeout_s: float
    headers: dict[str, str] = field(repr=False)
    """The content type and, when there is a key, ``Authorization``: never shown."""
    httpx: Any = field(repr=False)


def text(value: object, name: str, key: str) -> str:
    """``value`` when it is a non-empty string: a ``router_config`` entry such as ``model``."""
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(MSG_DECIDER_TEXT.format(name=name, field=key))
    return value


def _key(name: str, host: str, api_key: object, api_key_env: object) -> str | None:
    """The key: ``api_key``, else the variable ``api_key_env`` names, read now; ``None`` only for loopback."""
    if api_key is None:
        if api_key_env is None:
            if not is_loopback(host):
                raise ConfigurationError(MSG_DECIDER_NO_KEY_ENV.format(name=name))
            return None
        if not isinstance(api_key_env, str) or not api_key_env:
            raise ConfigurationError(MSG_DECIDER_ENV_NAME.format(name=name))
        home = KEY_HOMES.get(api_key_env)
        if home is not None and home != host:
            raise ConfigurationError(MSG_DECIDER_KEY_HOME.format(name=name, env=api_key_env, home=home))
        api_key = os.environ.get(api_key_env)
        if not api_key:
            raise ConfigurationError(MSG_DECIDER_KEY_UNSET.format(name=name, env=api_key_env))
    if not visible_ascii(api_key) or not api_key:
        raise ConfigurationError(MSG_DECIDER_KEY.format(name=name))
    return str(api_key)


def endpoint(
    name: str, base_url: object, path: str, *, api_key: object, api_key_env: object, timeout_s: object
) -> Endpoint:
    """The checked endpoint ``base_url`` + ``path``, its key and its timeout.

    Raises:
        ConfigurationError: for httpx not installed, a base URL :func:`~.remote_url.checked_url` refuses, a
            timeout that is not a positive number, or a key that is missing, not visible ASCII, or read from a
            vendor's variable for another host. No message echoes the URL or the key.
    """
    httpx = load_httpx()
    base = checked_url(text(base_url, name, "base_url"), httpx, field="base_url")
    url = checked_url(base.rstrip("/") + path, httpx, field="path")
    domain = httpx.URL(url).host
    key = _key(name, domain, api_key, api_key_env)
    headers = {**REMOTE_CONTENT_TYPE, **({REMOTE_AUTH_HEADER: REMOTE_AUTH_VALUE.format(key=key)} if key else {})}
    return Endpoint(
        url=url, host=host_of(url), domain=domain, timeout_s=checked_timeout(timeout_s), headers=headers, httpx=httpx
    )


async def post_json(target: Endpoint, body: dict[str, Any], deadline_s: float) -> Any:
    """POST ``body`` once and read the JSON answer, waiting at most ``deadline_s``.

    Raises:
        RemoteRouterTimeout: when the server does not answer in time.
        RemoteRouterError: for a transport error or a status other than 200.
        RemoteRouterInvalid: for an answer that is not ``application/json`` in the identity encoding, is over
            256 KiB, or does not parse.
    """
    httpx, raw, failed = target.httpx, b"", None
    try:
        async with (
            httpx.AsyncClient(headers=target.headers, follow_redirects=False, trust_env=False) as client,
            client.stream("POST", target.url, json=body, timeout=deadline_s) as response,
        ):
            check_head(response, target.host)
            raw = await read_capped(response, target.host, DECIDER_MAX_RESPONSE_BYTES)
    except httpx.HTTPError as exc:
        failed = exc  # raised below, outside this block: httpx's error names the whole URL
    if isinstance(failed, httpx.TimeoutException):
        raise RemoteRouterTimeout(target.host, REMOTE_WHY_TRANSPORT.format(kind=type(failed).__name__)) from None
    if failed is not None:
        raise RemoteRouterError(target.host, REMOTE_WHY_TRANSPORT.format(kind=type(failed).__name__)) from None
    try:
        return json.loads(raw)
    except (ValueError, RecursionError):
        raise RemoteRouterInvalid(target.host, REMOTE_WHY_NOT_JSON) from None
