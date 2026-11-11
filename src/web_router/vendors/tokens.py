"""The OAuth tokens a vendor call mints from its credentials, and the cache that reuses them.

Apart from :mod:`web_router.vendors.credentials`, which only says where a
credential's value comes from.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from web_router.constants_vendors import (
    MSG_VENDOR_TOKEN_ERROR,
    MSG_VENDOR_TOKEN_NO_ACCESS,
    MSG_VENDOR_TOKEN_UNREACHABLE,
    MSG_VENDOR_TOKEN_UNREADABLE,
    VENDOR_ERROR_BODY_LIMIT,
    VENDOR_TOKEN_DEFAULT_LIFETIME,
    VENDOR_TOKEN_REFRESH_MARGIN,
)
from web_router.exceptions import ProviderError
from web_router.vendors.masking import mask
from web_router.vendors.schema import VendorOAuth2

if TYPE_CHECKING:
    import httpx


@dataclass
class _Token:
    value: str
    expires_at: float


@dataclass(frozen=True)
class TokenRequest:
    """One rendered token-endpoint call: everything needed to mint, and the key its token is cached under."""

    vendor: str
    url: str
    form: dict[str, str]
    auth: tuple[str | bytes, str | bytes] | None

    def auth_kwargs(self) -> dict[str, Any]:
        """``auth=`` for the token call, only when the client authenticates with HTTP Basic."""
        return {"auth": self.auth} if self.auth else {}

    @property
    def key(self) -> str:
        material = repr((self.vendor, self.url, sorted(self.form.items()), self.auth))
        return hashlib.sha256(material.encode()).hexdigest()


def token_request(vendor: str, oauth: VendorOAuth2, values: Mapping[str, str]) -> TokenRequest:
    """Render an ``oauth2`` block against resolved values."""
    client_id = oauth.client_id.format_map(values)
    client_secret = oauth.client_secret.format_map(values)
    form: dict[str, str] = {"grant_type": oauth.grant}
    if oauth.grant == "refresh_token" and oauth.refresh_token:
        form["refresh_token"] = oauth.refresh_token.format_map(values)
    if oauth.scope:
        form["scope"] = oauth.scope
    auth: tuple[str | bytes, str | bytes] | None = None
    if oauth.client_auth == "basic":
        auth = (client_id, client_secret)
    else:
        form[oauth.client_id_param] = client_id
        form["client_secret"] = client_secret
    return TokenRequest(vendor=vendor, url=oauth.token_url, form=form, auth=auth)


class TokenCache:
    """Access tokens, reused until shortly before they expire. One per router.

    Keyed on everything that mints the token, so two accounts on one vendor
    never share one, and a rotated refresh token mints afresh. Each key has its
    own lock: one slow token endpoint holds up only the calls waiting for that
    same token, never another vendor's or another account's.
    """

    def __init__(self) -> None:
        self._tokens: dict[str, _Token] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self._alocks: dict[str, asyncio.Lock] = {}

    def _fresh(self, key: str) -> str | None:
        token = self._tokens.get(key)
        if token is not None and token.expires_at - VENDOR_TOKEN_REFRESH_MARGIN > time.monotonic():
            return token.value
        return None

    def _lock(self, key: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(key, threading.Lock())

    def get(self, request: TokenRequest, client: httpx.Client) -> str:
        import httpx

        with self._lock(request.key):
            cached = self._fresh(request.key)
            if cached is not None:
                return cached
            try:
                response = client.post(request.url, data=request.form, **request.auth_kwargs())
            except httpx.HTTPError as exc:
                raise self._unreachable(request, exc) from None
            return self._store(request, response)

    async def aget(self, request: TokenRequest, client: httpx.AsyncClient) -> str:
        import httpx

        # Its own lock, not the sync path's: a sync call made on the event loop's thread would block on a
        # lock held across this await and hang the loop. A sync and an async call racing on a cold cache
        # may each mint a token, which is harmless.
        async with self._alocks.setdefault(request.key, asyncio.Lock()):
            cached = self._fresh(request.key)
            if cached is not None:
                return cached
            try:
                response = await client.post(request.url, data=request.form, **request.auth_kwargs())
            except httpx.HTTPError as exc:
                raise self._unreachable(request, exc) from None
            return self._store(request, response)

    @staticmethod
    def _secrets(request: TokenRequest) -> list[str]:
        return [str(value) for value in (*request.form.values(), *(request.auth or ())) if value]

    def _unreachable(self, request: TokenRequest, exc: Exception) -> ProviderError:
        error = mask(f"{type(exc).__name__}: {exc}", self._secrets(request))
        return ProviderError(request.vendor, MSG_VENDOR_TOKEN_UNREACHABLE.format(url=request.url, error=error))

    def _store(self, request: TokenRequest, response: httpx.Response) -> str:
        secrets = self._secrets(request)
        if response.status_code >= 400:
            body = mask(response.text, secrets)[:VENDOR_ERROR_BODY_LIMIT]
            detail = MSG_VENDOR_TOKEN_ERROR.format(url=request.url, status=response.status_code, body=body)
            raise ProviderError(request.vendor, detail)
        try:
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError(f"a JSON {type(payload).__name__}, not an object")
            lifetime = payload.get("expires_in")
            seconds: float | None = None
            if isinstance(lifetime, (int, float, str)) and lifetime != "":
                seconds = float(lifetime)
            elif lifetime not in (None, ""):
                raise TypeError(f"expires_in is a {type(lifetime).__name__}")
            if seconds is not None and not math.isfinite(seconds):
                raise ValueError(f"expires_in is not finite: {seconds}")
        except (ValueError, TypeError) as exc:
            error = mask(f"{type(exc).__name__}: {exc}", secrets)
            raise ProviderError(
                request.vendor, MSG_VENDOR_TOKEN_UNREADABLE.format(url=request.url, error=error)
            ) from None
        access = payload.get("access_token")
        if not isinstance(access, str) or not access:
            raise ProviderError(request.vendor, MSG_VENDOR_TOKEN_NO_ACCESS.format(url=request.url))
        # The default only when the vendor gave no lifetime: a stated one, however short, is believed.
        lifetime_s = VENDOR_TOKEN_DEFAULT_LIFETIME if seconds is None else seconds
        if lifetime_s > VENDOR_TOKEN_REFRESH_MARGIN:
            self._tokens[request.key] = _Token(access, time.monotonic() + lifetime_s)
        else:
            self._tokens.pop(request.key, None)  # good for this call only; the next one mints afresh
        return access
