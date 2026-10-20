"""Hyperbrowser provider, over the Sessions API.

Browser only. Talks to the REST endpoint directly rather than through the
``hyperbrowser`` SDK: web-router opens, asks about and stops a session, which an
SDK dependency would not simplify. Install with the ``all`` extra.
Reads ``HYPERBROWSER_API_KEY`` unless a key is passed explicitly.

``timeout`` is mapped from Hyperbrowser's OpenAPI reference and SDK models, not
a live call. ``stealth``, ``solve_captchas`` and ``proxy_country`` are mapped
from https://hyperbrowser.ai/docs/api-reference/create-new-session and
https://hyperbrowser.ai/docs/sessions/parameters, read 2026-09-24 --
**unverified against a live call**. The live test
``test_live_session_opens_with_stealth_captchas_and_a_proxy_country`` is the
check that confirms them. Hyperbrowser has no idle timeout to map
``idle_timeout`` onto. ``profile`` and ``persist_profile`` map onto
``profile.id`` and ``profile.persistChanges``, and ``liveUrl`` is the
session's control URL -- checked live on 2026-10-20. Its screen size is
reachable through ``provider_options``; mapping it properly is a welcome PR --
see CONTRIBUTING.md.
"""

from __future__ import annotations

import math
from typing import Any, ClassVar

import httpx
from pydantic import SecretStr

from web_router.constants import (
    ENV_HYPERBROWSER_API_KEY,
    HYPERBROWSER_API_KEY_HEADER,
    HYPERBROWSER_BASE_URL,
    HYPERBROWSER_CDP_URL_FIELD,
    HYPERBROWSER_CONTROL_URL_FIELD,
    HYPERBROWSER_FIELDS,
    HYPERBROWSER_OPEN_PATH,
    HYPERBROWSER_PROXY_COUNTRIES,
    HYPERBROWSER_RELEASE_PATH,
    HYPERBROWSER_RELEASED_FIELD,
    HYPERBROWSER_SECONDS_PER_MINUTE,
    HYPERBROWSER_SESSION_PATH,
    HYPERBROWSER_SESSION_STATES,
    HYPERBROWSER_STEALTH,
    MSG_HYPERBROWSER_NOT_STOPPED,
    PROVIDER_HYPERBROWSER,
)
from web_router.exceptions import ProviderError
from web_router.providers._http import HttpBrowserProvider, ReleaseMethod
from web_router.providers._utils import build_browser_payload
from web_router.types import BrowserRequest, BrowserSession, BrowserState, StealthLevel


def _minutes(seconds: float) -> int:
    # Rounded up, because Hyperbrowser takes whole minutes: a session that
    # outlives the caller's limit by under a minute is released on exit anyway,
    # while one cut short would end under a driver still using it.
    return math.ceil(seconds / HYPERBROWSER_SECONDS_PER_MINUTE)


class HyperbrowserBrowserProvider(HttpBrowserProvider):
    """Opens cloud browser sessions through the Hyperbrowser Sessions API."""

    name: ClassVar[str] = PROVIDER_HYPERBROWSER
    supported_parameters: ClassVar[frozenset[str]] = frozenset(
        {"timeout", "stealth", "solve_captchas", "proxy_country", "profile", "persist_profile"}
    )
    stealth_levels: ClassVar[frozenset[StealthLevel]] = frozenset(HYPERBROWSER_STEALTH)
    proxy_countries: ClassVar[frozenset[str] | None] = HYPERBROWSER_PROXY_COUNTRIES

    base_url: ClassVar[str] = HYPERBROWSER_BASE_URL
    open_path: ClassVar[str] = HYPERBROWSER_OPEN_PATH
    release_path: ClassVar[str] = HYPERBROWSER_RELEASE_PATH
    release_method: ClassVar[ReleaseMethod] = "PUT"
    # Not `failed`: Hyperbrowser's `close-error` maps there, and it is a stop that
    # itself went wrong, on a session that may still bill. A failed release of
    # one must be reported, not taken as done.
    release_ended_states: ClassVar[frozenset[BrowserState]] = frozenset({"released", "expired"})
    env_api_key: ClassVar[str] = ENV_HYPERBROWSER_API_KEY
    api_key_header: ClassVar[str] = HYPERBROWSER_API_KEY_HEADER

    def _build_payload(self, request: BrowserRequest) -> dict[str, Any]:
        return build_browser_payload(request, HYPERBROWSER_FIELDS, _minutes)

    def _to_session(self, request: BrowserRequest, payload: dict[str, Any]) -> BrowserSession:
        # `wsEndpoint` is connected to as it comes, and carries a session token
        # in its query string, so it goes nowhere but the secret `cdp_url`.
        # `liveUrl` is the control URL, never `live_view_url`: Hyperbrowser says
        # to protect it as a secret whether or not the session was opened
        # view-only. No expiry is reported, so `expires_at` stays unset rather
        # than being worked out from the timeout.
        control_url = payload.get(HYPERBROWSER_CONTROL_URL_FIELD)
        return BrowserSession(
            provider=self.name,
            session_id=payload["id"],
            cdp_url=SecretStr(payload[HYPERBROWSER_CDP_URL_FIELD]),
            control_url=SecretStr(control_url) if control_url else None,
            raw=payload,
        )

    def _fetch(self, session_id: str) -> BrowserState:
        return self._state(self._get(self._session_path(session_id, HYPERBROWSER_SESSION_PATH)))

    async def _afetch(self, session_id: str) -> BrowserState:
        return self._state(await self._aget(self._session_path(session_id, HYPERBROWSER_SESSION_PATH)))

    def _check_release(self, response: httpx.Response) -> None:
        # Hyperbrowser puts its verdict in the body of a 200 that a status check
        # alone would read as success.
        success = self._payload(response).get(HYPERBROWSER_RELEASED_FIELD)
        if success is not True:
            detail = MSG_HYPERBROWSER_NOT_STOPPED.format(status=response.status_code, success=success)
            raise ProviderError(self.name, detail)

    def _state(self, payload: dict[str, Any]) -> BrowserState:
        return self.state_of(payload["status"], HYPERBROWSER_SESSION_STATES)
