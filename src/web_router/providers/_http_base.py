"""The base every HTTP provider builds on: client construction, auth headers, HTTP error mapping, cleanup.

Apart from :mod:`web_router.providers._http`, which re-exports it with the
classes a provider subclasses, so each module stays readable on its own.
"""

from __future__ import annotations

import threading
from typing import Any, ClassVar, Literal

import httpx

from web_router.constants import (
    DEFAULT_HTTP_TIMEOUT,
    FOLLOW_REDIRECTS,
    HTTP_ERROR_BODY_LIMIT,
    MSG_HTTP_ERROR,
    MSG_REDIRECTS_WITH_CREDENTIAL,
)
from web_router.exceptions import ConfigurationError, ProviderError
from web_router.providers._clamp import clamped
from web_router.providers._deadline_body import bound_body
from web_router.providers._utils import WarmClients, compact, resolve_api_key
from web_router.providers.base import _BaseProvider


class _HttpProvider(WarmClients, _BaseProvider):
    """A provider backed by a plain HTTP endpoint."""

    base_url: ClassVar[str]

    env_api_key: ClassVar[str | None] = None
    """Variable holding this provider's credential, or ``None`` if it needs none.

    A keyless endpoint sends no auth header and never raises for a missing key.
    """

    api_key_header: ClassVar[str] = "Authorization"
    """Header carrying the credential."""

    api_key_prefix: ClassVar[str] = ""
    """Prepended to the key, e.g. ``"Bearer "``."""

    http_method: ClassVar[Literal["GET", "POST"]] = "POST"
    """``POST`` sends the payload as JSON; ``GET`` sends it as query parameters."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = DEFAULT_HTTP_TIMEOUT,
        **kwargs: Any,
    ) -> None:
        """
        Args:
            api_key: Falls back to this provider's ``env_api_key`` variable.
                Ignored by a keyless provider, which declares no variable.
            base_url: Override the API host.
            timeout: Request timeout in seconds, applied to both clients.
            **kwargs: Forwarded to the underlying ``httpx`` clients.

        Raises:
            ConfigurationError: if ``follow_redirects=True`` is passed to a
                provider that sends a credential -- see
                :data:`~web_router.constants.FOLLOW_REDIRECTS`.
        """
        key = self._resolve_key(api_key)
        url = base_url or self.base_url
        super().__init__(api_key=key, base_url=url, timeout=timeout, **kwargs)

        # Merged, not replaced: a caller's headers= must not drop the auth header.
        headers = {"Accept": "application/json"}
        if key is not None:
            headers.update(self._auth_headers(key))
        headers.update(kwargs.pop("headers", None) or {})

        follow_redirects = bool(kwargs.pop("follow_redirects", FOLLOW_REDIRECTS))
        if follow_redirects and key is not None:
            raise ConfigurationError(MSG_REDIRECTS_WITH_CREDENTIAL.format(provider=self.name))

        self._client_kwargs: dict[str, Any] = {
            "base_url": url,
            "timeout": timeout,
            "headers": headers,
            # Pinned, not httpx's default, so the credential never travels to a host this provider did not mean to call.
            "follow_redirects": follow_redirects,
            **compact(kwargs),
        }
        # Built on first use, and only the one used: building both eagerly left a sync caller holding an async pool
        # close() could not shut. The lock stops threads sharing one router from each building, and leaking, a client.
        self._sync: httpx.Client | None = None
        self._async: httpx.AsyncClient | None = None
        self._client_lock = threading.Lock()

    def _clamped(self) -> dict[str, Any]:
        """A one-call request's timeout under a deadline; see :func:`~web_router.providers._deadline.clamped`."""
        return clamped(self._client_kwargs["timeout"])

    def _resolve_key(self, api_key: str | None) -> str | None:
        if not self.env_api_key:
            return None
        return resolve_api_key(api_key, env_var=self.env_api_key, provider=self.name)

    def _auth_headers(self, key: str) -> dict[str, str]:
        """Headers that carry the credential. Overridden by providers that do not use one header.

        Whatever this returns, a provider whose key is set is one the redirect
        pin above refuses to relax, so an unusual auth scheme cannot opt out of
        it by building its own header.
        """
        return {self.api_key_header: f"{self.api_key_prefix}{key}"}

    @property
    def _client(self) -> httpx.Client:
        with self._client_lock:
            if self._sync is None:
                self._sync = httpx.Client(**{**self._client_kwargs, "event_hooks": self._sync_hooks()})
        return self._sync

    def _sync_hooks(self) -> dict[str, list[Any]]:
        """The caller's event hooks, with the deadline's body bound first so a hook reading the body is bounded too.

        Sync only: the async client is cancelled at the deadline, body included.
        """
        hooks = {kind: list(found) for kind, found in (self._client_kwargs.get("event_hooks") or {}).items()}
        hooks["response"] = [bound_body, *hooks.get("response", [])]
        return hooks

    @property
    def _async_client(self) -> httpx.AsyncClient:
        with self._client_lock:
            if self._async is None:
                self._async = httpx.AsyncClient(**self._client_kwargs)
        return self._async

    def _payload(self, response: httpx.Response) -> dict[str, Any]:
        """Return the decoded body, turning an HTTP error into a message worth reading.

        Everything else -- timeouts, connection failures, bad JSON -- is wrapped
        by the public ``search``/``extract``.
        """
        self._raise_for_status(response)
        body: dict[str, Any] = response.json()
        return body

    def _raise_for_status(self, response: httpx.Response) -> None:
        """Turn an HTTP error into a message worth reading, without decoding a body.

        Separate from :meth:`_payload` for the calls whose success has no body
        to read -- ending a browser session is often a bare 204.
        """
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:HTTP_ERROR_BODY_LIMIT]
            detail = MSG_HTTP_ERROR.format(status=exc.response.status_code, body=body)
            raise ProviderError(self.name, detail, original=exc, kind=self._error_kind(exc)) from exc

    def _error_kind(self, exc: httpx.HTTPStatusError) -> str | None:
        """The failure's kind when the body says more than its status; ``None`` classifies it by status."""
        return None

    def close(self) -> None:
        # Via the attribute, not the property: closing must never construct a client.
        if self._sync is not None:
            self._sync.close()

    async def aclose(self) -> None:
        if self._async is not None:
            await self._async.aclose()
        # An async caller may still have built a sync client -- through the sync path, or a library that probed it --
        # and aclose() is the only shutdown they will call.
        self.close()
