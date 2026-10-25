"""Anchor Browser provider, over the Sessions API.

Browser only. Talks to the REST endpoint directly rather than through the
``anchorbrowser`` SDK: web-router opens, asks about and ends a session, which an
SDK dependency would not simplify. Install with the ``all`` extra.
Reads ``ANCHOR_API_KEY`` unless a key is passed explicitly.

``timeout`` and ``idle_timeout`` are mapped and unconfirmed against a live call,
as before. ``stealth``, ``solve_captchas`` and ``proxy_country`` are new here
and are **doc-derived, unverified against a live call**: read from
https://docs.anchorbrowser.io/essentials/stealth and
https://docs.anchorbrowser.io/advanced/proxy on 2026-09-24, no account was
available. The live integration test in ``tests/providers/test_anchor_browser.py`` is
the check that confirms them -- see AGENTS.md on verifying against the live
service. Anchor's docs describe one stealth tier (``extra_stealth``), not two,
so ``stealth="low"`` and ``stealth="high"`` send the same field; nothing in the
docs distinguishes a stronger level. The docs say extra stealth is silently
turned off by Anchor when its proxy is not active, so ``stealth`` in
``{"low", "high"}`` requires ``proxy_country`` here -- without that
co-requirement, a caller asking for stealth would silently get none. Anchor's
proxy guide shows an optional ``type`` field (example: ``"anchor_residential"``)
that is not required, so it is left unmapped. ``profile`` (a profile's name)
and ``persist_profile`` map onto ``browser.profile``, and the session's
``live_view_url`` is its control URL -- checked live on 2026-10-20. Viewport
and headless mode are still reachable only through ``provider_options`` --
mapping one properly is a welcome PR, see CONTRIBUTING.md.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, ClassVar

from pydantic import SecretStr

from web_router.constants import (
    ANCHOR_API_KEY_HEADER,
    ANCHOR_BASE_URL,
    ANCHOR_CONTROL_URL_FIELD,
    ANCHOR_FIELDS,
    ANCHOR_OPEN_PATH,
    ANCHOR_PROXY_COUNTRIES,
    ANCHOR_SECONDS_PER_MINUTE,
    ANCHOR_SESSION_PATH,
    ANCHOR_SESSION_STATES,
    ANCHOR_STEALTH,
    ENV_ANCHOR_API_KEY,
    PROVIDER_ANCHOR,
)
from web_router.providers._http import HttpBrowserProvider, ReleaseMethod
from web_router.providers._utils import build_browser_payload
from web_router.types import BrowserRequest, BrowserSession, BrowserState, StealthLevel


def _minutes(seconds: float) -> int:
    # Rounded up, so a session never ends before the caller asked it to; Anchor
    # takes whole minutes only.
    return math.ceil(seconds / ANCHOR_SECONDS_PER_MINUTE)


class AnchorBrowserProvider(HttpBrowserProvider):
    """Opens cloud browser sessions through the Anchor Browser Sessions API.

    Anchor runs a session headful and records it unless told otherwise. Unset
    means unset, so neither is switched off on the caller's behalf; pass
    ``provider_options`` to change either.
    """

    name: ClassVar[str] = PROVIDER_ANCHOR
    supported_parameters: ClassVar[frozenset[str]] = frozenset(
        {"timeout", "idle_timeout", "stealth", "solve_captchas", "proxy_country", "profile", "persist_profile"}
    )
    stealth_levels: ClassVar[frozenset[StealthLevel]] = frozenset(ANCHOR_STEALTH)
    proxy_countries: ClassVar[frozenset[str] | None] = ANCHOR_PROXY_COUNTRIES
    co_requirements: ClassVar[Mapping[str, Mapping[Any, frozenset[str]]]] = MappingProxyType(
        {
            "stealth": {"low": frozenset({"proxy_country"}), "high": frozenset({"proxy_country"})},
            **HttpBrowserProvider.co_requirements,
        }
    )
    """Anchor turns extra stealth off on its own unless the proxy is active; asking for it without a
    country would silently get none. Saving a profile needs the profile's name."""

    base_url: ClassVar[str] = ANCHOR_BASE_URL
    open_path: ClassVar[str] = ANCHOR_OPEN_PATH
    release_path: ClassVar[str] = ANCHOR_SESSION_PATH
    status_path: ClassVar[str] = ANCHOR_SESSION_PATH
    release_method: ClassVar[ReleaseMethod] = "DELETE"
    env_api_key: ClassVar[str] = ENV_ANCHOR_API_KEY
    api_key_header: ClassVar[str] = ANCHOR_API_KEY_HEADER

    def _build_payload(self, request: BrowserRequest) -> dict[str, Any]:
        return build_browser_payload(request, ANCHOR_FIELDS, _minutes)

    def _to_session(self, request: BrowserRequest, payload: dict[str, Any]) -> BrowserSession:
        data = payload["data"]
        # Anchor reports no expiry, so `expires_at` stays unset. Its live view is
        # interactive unless the session was opened read-only, so it is the
        # control URL, never `live_view_url`.
        return BrowserSession(
            provider=self.name,
            session_id=data["id"],
            cdp_url=SecretStr(data["cdp_url"]),
            control_url=self._secret_field(data, ANCHOR_CONTROL_URL_FIELD),
            raw=payload,
        )

    def _fetch(self, session_id: str) -> BrowserState:
        return self._state(self._get(self._session_path(session_id, self.status_path)))

    async def _afetch(self, session_id: str) -> BrowserState:
        return self._state(await self._aget(self._session_path(session_id, self.status_path)))

    def _orphan_id(self, payload: dict[str, Any]) -> str | None:
        # Anchor wraps every answer in `data`, so the id is one level below
        # where the base class looks for it.
        data = payload.get("data")
        return super()._orphan_id(data) if isinstance(data, dict) else None

    def _state(self, payload: dict[str, Any]) -> BrowserState:
        return self.state_of(payload["data"]["status"], ANCHOR_SESSION_STATES)
