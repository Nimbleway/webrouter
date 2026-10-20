"""Kernel provider, over the Browsers API.

Browser only. Talks to the REST endpoint directly rather than through the
``kernel`` SDK: web-router opens, asks about and deletes a session, which an SDK
dependency would not simplify. Install with the ``all`` extra.
Reads ``KERNEL_API_KEY`` unless a key is passed explicitly.

Every mapped option was checked against a live call on 2026-10-20; see
``tests/providers/test_kernel_browser.py``. Kernel has no lifetime limit, only
an idle one, so ``timeout`` is refused, and no ``BROWSER_TIMEOUT`` default is
sent. ``proxy_country`` is refused because a country needs a proxy created
first through Kernel's Proxies API, and ``solve_captchas`` because Kernel's
solver has no switch of its own: it comes with ``stealth``. An existing proxy
is reachable through ``provider_options`` as ``proxy.id``.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, ClassVar

import httpx
from pydantic import SecretStr

from web_router.constants import BROWSER_PERSIST_PROFILE_REQUIRES, ENV_KERNEL_API_KEY, PROVIDER_KERNEL
from web_router.constants_kernel import (
    KERNEL_API_KEY_PREFIX,
    KERNEL_BASE_URL,
    KERNEL_CONTROL_URL_FIELD,
    KERNEL_FIELDS,
    KERNEL_OPEN_PATH,
    KERNEL_SESSION_GONE_STATES,
    KERNEL_SESSION_ID_FIELD,
    KERNEL_SESSION_PATH,
    KERNEL_STEALTH,
)
from web_router.providers._http import HttpBrowserProvider, ReleaseMethod
from web_router.providers._utils import build_browser_payload
from web_router.types import BrowserRequest, BrowserSession, BrowserState, StealthLevel


def _seconds(seconds: float) -> int:
    # Rounded up, so a session never ends before the caller asked it to; Kernel
    # takes whole seconds only.
    return math.ceil(seconds)


class KernelBrowserProvider(HttpBrowserProvider):
    """Opens cloud browser sessions through the Kernel Browsers API.

    Kernel deletes a session once it has gone ``idle_timeout`` -- 60 seconds
    unless set -- with no CDP, WebDriver or live-view client connected, so a
    session opened and not connected to soon ends on its own.
    """

    name: ClassVar[str] = PROVIDER_KERNEL
    supported_parameters: ClassVar[frozenset[str]] = frozenset(
        {"idle_timeout", "stealth", "headless", "viewport", "profile", "persist_profile"}
    )
    stealth_levels: ClassVar[frozenset[StealthLevel]] = frozenset(KERNEL_STEALTH)
    co_requirements: ClassVar[Mapping[str, Mapping[Any, frozenset[str]]]] = MappingProxyType(
        BROWSER_PERSIST_PROFILE_REQUIRES
    )

    base_url: ClassVar[str] = KERNEL_BASE_URL
    open_path: ClassVar[str] = KERNEL_OPEN_PATH
    release_path: ClassVar[str] = KERNEL_SESSION_PATH
    release_method: ClassVar[ReleaseMethod] = "DELETE"
    session_id_field: ClassVar[str] = KERNEL_SESSION_ID_FIELD
    env_api_key: ClassVar[str] = ENV_KERNEL_API_KEY
    api_key_prefix: ClassVar[str] = KERNEL_API_KEY_PREFIX

    def _build_payload(self, request: BrowserRequest) -> dict[str, Any]:
        return build_browser_payload(request, KERNEL_FIELDS, _seconds)

    def _to_session(self, request: BrowserRequest, payload: dict[str, Any]) -> BrowserSession:
        # Kernel reports no expiry, only its idle limit, so `expires_at` stays
        # unset. Its live view is interactive, so it is the control URL, never
        # `live_view_url`.
        control_url = payload.get(KERNEL_CONTROL_URL_FIELD)
        return BrowserSession(
            provider=self.name,
            session_id=payload["session_id"],
            cdp_url=SecretStr(payload["cdp_ws_url"]),
            control_url=SecretStr(control_url) if control_url else None,
            raw=payload,
        )

    def _fetch(self, session_id: str) -> BrowserState:
        return self._state(self._client.get(self._session_path(session_id)))

    async def _afetch(self, session_id: str) -> BrowserState:
        return self._state(await self._async_client.get(self._session_path(session_id)))

    def _state(self, response: httpx.Response) -> BrowserState:
        # Kernel reports no status: a session it returns is running, and one it
        # has deleted answers 404 with an error code, which the table reads.
        if response.status_code == httpx.codes.NOT_FOUND:
            return self.state_of(str(response.json().get("code")), KERNEL_SESSION_GONE_STATES)
        self._payload(response)
        return "running"
