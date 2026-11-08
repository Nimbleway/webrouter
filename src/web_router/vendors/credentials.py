"""Where vendor credentials come from, and the OAuth tokens minted from them.

A call asks a :class:`CredentialSource` for each value its vendor's file names
and never reads the environment itself. Today that is the process environment
(and the ``.env`` the MCP server loads into it), or values passed to the
router; a hosted vault holding OAuth apps for the user is one more source, and
nothing that sends a call has to change to use it.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable
from urllib.parse import quote, quote_plus

from web_router.constants_vendors import (
    MSG_VENDOR_TOKEN_ERROR,
    MSG_VENDOR_TOKEN_NO_ACCESS,
    VENDOR_ERROR_BODY_LIMIT,
    VENDOR_MASK,
    VENDOR_TOKEN_DEFAULT_LIFETIME,
    VENDOR_TOKEN_REFRESH_MARGIN,
)
from web_router.exceptions import ProviderError
from web_router.vendors.schema import Vendor, VendorOAuth2
from web_router.vendors.types import VendorSummary

if TYPE_CHECKING:
    import httpx


@runtime_checkable
class CredentialSource(Protocol):
    """Answers "what is ``name`` for ``vendor``?", or ``None`` when it does not know.

    ``env`` is the variables the vendor's file lists for that value, in order,
    for the sources that read the environment; others may ignore it.
    """

    def get(self, vendor: str, name: str, env: Sequence[str]) -> str | None: ...


class EnvCredentials:
    """Values from the process environment: the first of the file's variables that is set and not blank."""

    def get(self, vendor: str, name: str, env: Sequence[str]) -> str | None:
        for variable in env:
            value = (os.environ.get(variable) or "").strip()
            if value:
                return value
        return None


class MappingCredentials:
    """Values passed in code, as ``{vendor: {name: value}}``."""

    def __init__(self, values: Mapping[str, Mapping[str, str]]) -> None:
        self._values = {vendor: dict(names) for vendor, names in values.items()}

    def get(self, vendor: str, name: str, env: Sequence[str]) -> str | None:
        value = (self._values.get(vendor, {}).get(name) or "").strip()
        return value or None


class ChainedCredentials:
    """The first source that knows a value wins."""

    def __init__(self, *sources: CredentialSource) -> None:
        self.sources = sources

    def get(self, vendor: str, name: str, env: Sequence[str]) -> str | None:
        for source in self.sources:
            value = source.get(vendor, name, env)
            if value:
                return value
        return None


def credential_source(
    vendor_credentials: CredentialSource | Mapping[str, Mapping[str, str]] | None,
) -> CredentialSource:
    """What a router built with ``vendor_credentials=`` reads: those values first, then the environment."""
    if vendor_credentials is None:
        return EnvCredentials()
    # Mapping first: a dict has a get() too, so it would pass for a source.
    if isinstance(vendor_credentials, Mapping):
        return ChainedCredentials(MappingCredentials(vendor_credentials), EnvCredentials())
    return vendor_credentials


def missing_credentials(vendor: Vendor, source: CredentialSource) -> list[str]:
    """The first-choice variable of each credential ``vendor`` requires that ``source`` does not hold."""
    return [
        spec.env[0]
        for name, spec in vendor.auth.credentials.items()
        if spec.required and not source.get(vendor.vendor, name, spec.env)
    ]


def summarize(vendor: Vendor, source: CredentialSource) -> VendorSummary:
    """One vendor's listing row, configured or not according to ``source``."""
    missing = missing_credentials(vendor, source)
    return VendorSummary(
        vendor=vendor.vendor,
        name=vendor.name,
        category=vendor.category,
        summary=vendor.summary,
        docs_url=vendor.docs_url,
        endpoint_count=len(vendor.endpoints),
        configured=not missing,
        missing=missing,
    )


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
    never share one, and a rotated refresh token mints afresh.
    """

    def __init__(self) -> None:
        self._tokens: dict[str, _Token] = {}
        self._lock = threading.Lock()
        self._alocks: dict[str, asyncio.Lock] = {}

    def _fresh(self, key: str) -> str | None:
        token = self._tokens.get(key)
        if token is not None and token.expires_at - VENDOR_TOKEN_REFRESH_MARGIN > time.monotonic():
            return token.value
        return None

    def get(self, request: TokenRequest, client: httpx.Client) -> str:
        with self._lock:
            cached = self._fresh(request.key)
            if cached is not None:
                return cached
            response = client.post(request.url, data=request.form, **request.auth_kwargs())
            return self._store(request, response)

    async def aget(self, request: TokenRequest, client: httpx.AsyncClient) -> str:
        lock = self._alocks.setdefault(request.key, asyncio.Lock())
        async with lock:
            cached = self._fresh(request.key)
            if cached is not None:
                return cached
            response = await client.post(request.url, data=request.form, **request.auth_kwargs())
            return self._store(request, response)

    def _store(self, request: TokenRequest, response: httpx.Response) -> str:
        secrets = [str(value) for value in (*request.form.values(), *(request.auth or ())) if value]
        if response.status_code >= 400:
            body = mask(response.text, secrets)[:VENDOR_ERROR_BODY_LIMIT]
            detail = MSG_VENDOR_TOKEN_ERROR.format(url=request.url, status=response.status_code, body=body)
            raise ProviderError(request.vendor, detail)
        payload: dict[str, Any] = response.json()
        access = payload.get("access_token")
        if not isinstance(access, str) or not access:
            raise ProviderError(request.vendor, MSG_VENDOR_TOKEN_NO_ACCESS.format(url=request.url))
        lifetime = payload.get("expires_in")
        seconds = float(lifetime) if isinstance(lifetime, (int, float, str)) and str(lifetime).strip() else None
        expires = time.monotonic() + (seconds if seconds else VENDOR_TOKEN_DEFAULT_LIFETIME)
        self._tokens[request.key] = _Token(access, expires)
        return access


def mask(text: str, secrets: Sequence[str]) -> str:
    """Replace every secret in ``text`` with ``***``, as written and URL-encoded, longest first.

    Longest first, so one secret inside another cannot leave a tail behind;
    URL-encoded too, since an error body often echoes the request URL, query
    credential and all.
    """
    spellings = {form for s in secrets if s and len(s) >= 4 for form in (s, quote(s, safe=""), quote_plus(s))}
    for secret in sorted(spellings, key=len, reverse=True):
        text = text.replace(secret, VENDOR_MASK)
    return text
