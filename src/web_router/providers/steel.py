"""Steel provider, over the Sessions API.

Browser only. Talks to the REST endpoint directly rather than through
``steel-sdk``: web-router opens, asks about and releases a session, which an SDK
dependency would not simplify. Install with the ``all`` extra.
Reads ``STEEL_API_KEY`` unless a key is passed explicitly.

``timeout`` and ``idle_timeout`` are mapped under AGENTS.md's
documentation-only exception for a browser session's lifetime. ``solve_captchas``
(-> ``solveCaptcha``) and ``proxy_country`` (-> ``useProxy.geolocation.country``,
upper-case) are also declared in ``supported_parameters``; the live integration test in
``tests/providers/test_steel_browser.py`` is the check that confirms them. Both are doc-derived from
https://docs.steel.dev/overview/sessions-api/configuration and
https://docs.steel.dev/overview/stealth/proxies (fetched 2026-09-24) and are
**unverified against a live call**. Steel does not publish an enumerated list
of proxy countries ("over 200 countries via their two-letter Alpha-2 codes"),
so ``proxy_countries`` is left ``None`` rather than guessed at -- Steel is left
to refuse a code it lacks.

``stealth`` is deliberately left unmapped. The only stealth-adjacent field
Steel's reference documents is ``stealthConfig``, whose keys
(``autoCaptchaSolving``, ``humanizeInteractions``, ``skipFingerprintInjection``)
tune CAPTCHA auto-solving and fingerprint behaviour that Steel already applies
to every session by default -- there is no on/off or tiered switch to map a
``StealthLevel`` onto (same configuration page, 2026-09-24). Mapping it would
mean inventing a level Steel does not expose. Steel's profiles and dimensions
remain reachable through ``provider_options``; mapping one properly is a
welcome PR -- see CONTRIBUTING.md.
"""

from __future__ import annotations

import math
from typing import Any, ClassVar
from urllib.parse import urlencode

from pydantic import SecretStr

from web_router.constants import (
    ENV_STEEL_API_KEY,
    PROVIDER_STEEL,
    STEEL_API_KEY_HEADER,
    STEEL_BASE_URL,
    STEEL_CONNECT_KEY_PARAM,
    STEEL_CONNECT_SESSION_PARAM,
    STEEL_CONNECT_URL,
    STEEL_FIELDS,
    STEEL_MS_PER_SECOND,
    STEEL_OPEN_PATH,
    STEEL_RELEASE_PATH,
    STEEL_RELEASE_REASONS,
    STEEL_SESSION_PATH,
    STEEL_SESSION_STATES,
)
from web_router.providers._http import HttpBrowserProvider, ReleaseMethod
from web_router.providers._utils import build_browser_payload
from web_router.types import BrowserRequest, BrowserSession, BrowserState


def _ms(seconds: float) -> int:
    return math.ceil(seconds * STEEL_MS_PER_SECOND)


class SteelBrowserProvider(HttpBrowserProvider):
    """Opens cloud browser sessions through the Steel Sessions API."""

    name: ClassVar[str] = PROVIDER_STEEL
    supported_parameters: ClassVar[frozenset[str]] = frozenset(
        {"timeout", "idle_timeout", "solve_captchas", "proxy_country"}
    )
    # Steel's proxy countries are not enumerated in its reference ("over 200
    # countries via their two-letter Alpha-2 codes"), so any code is sent and
    # Steel is left to refuse one it lacks.
    proxy_countries: ClassVar[frozenset[str] | None] = None

    base_url: ClassVar[str] = STEEL_BASE_URL
    open_path: ClassVar[str] = STEEL_OPEN_PATH
    release_path: ClassVar[str] = STEEL_RELEASE_PATH
    release_method: ClassVar[ReleaseMethod] = "POST"
    env_api_key: ClassVar[str] = ENV_STEEL_API_KEY
    api_key_header: ClassVar[str] = STEEL_API_KEY_HEADER

    def _build_payload(self, request: BrowserRequest) -> dict[str, Any]:
        return build_browser_payload(request, STEEL_FIELDS, _ms)

    def _to_session(self, request: BrowserRequest, payload: dict[str, Any]) -> BrowserSession:
        session_id = payload["id"]
        # Steel reports no expiry, only the timeout it applied, so `expires_at`
        # stays unset rather than being worked out from a clock that is not its.
        # `debugUrl` stays in `raw` alone: it grants control of the browser, and
        # `live_view_url` is meant to be safe to show.
        return BrowserSession(
            provider=self.name,
            session_id=session_id,
            cdp_url=SecretStr(self._connect_url(session_id)),
            live_view_url=payload.get("sessionViewerUrl"),
            raw=payload,
        )

    def _fetch(self, session_id: str) -> BrowserState:
        return self._state(self._get(self._session_path(session_id, STEEL_SESSION_PATH)))

    async def _afetch(self, session_id: str) -> BrowserState:
        return self._state(await self._aget(self._session_path(session_id, STEEL_SESSION_PATH)))

    def _connect_url(self, session_id: str) -> str:
        # Built rather than read from `websocketUrl`, which Steel says not to
        # connect to directly. The key rides in the query string, which is the
        # reason `cdp_url` is a secret at all.
        query = urlencode({STEEL_CONNECT_KEY_PARAM: self.api_key, STEEL_CONNECT_SESSION_PARAM: session_id})
        return f"{STEEL_CONNECT_URL}?{query}"

    def _state(self, payload: dict[str, Any]) -> BrowserState:
        state = self.state_of(payload["status"], STEEL_SESSION_STATES)
        reason = payload.get("releaseReason")
        # Kept as a guard rather than folded into a table: the reason refines only `released`, and
        # tabulating it would mean a cross-product of status and reason keyed by a made-up string.
        if state == "released" and reason is not None:
            return self.state_of(reason, STEEL_RELEASE_REASONS)
        return state
