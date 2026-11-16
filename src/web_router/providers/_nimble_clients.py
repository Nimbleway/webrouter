"""The constructor and SDK clients every Nimble class shares, and the keyless switch search and extract add.

Kept apart from :mod:`web_router.providers.nimble` so that module stays small;
:mod:`web_router.providers.nimble_live_data` builds on the same clients.
"""

from __future__ import annotations

from typing import Any, ClassVar, TypeVar

from nimble_python import AsyncNimble, Nimble, omit

from web_router.constants import ENV_NIMBLE_API_KEY, MSG_NIMBLE_ASYNC_CLIENT_ONLY, MSG_NIMBLE_CLIENT_AND_KEY
from web_router.constants_nimble import NIMBLE_AUTH_HEADER
from web_router.exceptions import ConfigurationError
from web_router.providers._clamp import clamp_timeout
from web_router.providers._deadline import current_deadline
from web_router.providers._utils import compact, resolve_api_key
from web_router.providers.base import _BaseProvider

_ClientT = TypeVar("_ClientT", Nimble, AsyncNimble)


class _NimbleClients(_BaseProvider):
    """What the four Nimble classes share: one constructor, and the SDK clients it builds or is handed.

    Each class lists this first and its method's base second, so the
    constructor here resolves the key and builds the clients before the
    method's base sees the kwargs.

    By default a provider builds a sync and an async client from its key and
    closes both. Given an ``async_client``, it uses that one instead -- one
    authenticated as the caller's end user, say, with its own headers and a
    connection pool shared across the caller's app. It builds no sync client,
    reads no key from the environment and never closes the caller's client.
    Options passed beside it (``timeout``, ``base_url``, ...) are laid over it
    with ``copy()``, which shares its connection pool rather than opening one.
    """

    name: ClassVar[str]
    options: dict[str, Any]
    _client: Nimble | None
    _async_client: AsyncNimble
    _owns_clients: bool

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
        async_client: AsyncNimble | None = None,
        **kwargs: Any,
    ) -> None:
        """
        Args:
            api_key: Nimble's key; unset, read from ``NIMBLE_API_KEY``.
            base_url: Where the SDK sends its requests; unset, the SDK's own.
            timeout: Each request's limit, in seconds; unset, the SDK's own.
            async_client: A caller's own ``AsyncNimble``, used instead of
                building clients from a key -- see the class docstring.
            **kwargs: Further SDK client options, laid over the client.

        Raises:
            ConfigurationError: for no key anywhere, or a key beside a client.
        """
        key = self._key(api_key, async_client)
        super().__init__(api_key=key, base_url=base_url, timeout=timeout, **kwargs)
        self._callers_client = async_client is not None
        self._connect(key, async_client)

    @property
    def holds_credential(self) -> bool:
        """A key, or the caller's own client, which carries its own credential."""
        return self.api_key is not None or self._callers_client

    def _key(self, api_key: str | None, async_client: AsyncNimble | None) -> str | None:
        return _resolve_key(api_key, async_client, self.name)

    def _connect(self, api_key: str | None, async_client: AsyncNimble | None) -> None:
        client_kwargs = compact({**self.options, "api_key": api_key})
        if async_client is None:
            self._client = Nimble(**client_kwargs)
            self._async_client = AsyncNimble(**client_kwargs)
            self._owns_clients = True
        else:
            self._client = None
            self._async_client = async_client.copy(**client_kwargs) if client_kwargs else async_client
            self._owns_clients = False

    @property
    def _sync_client(self) -> Nimble:
        """The sync client, or a clear error for a provider handed only an async one."""
        if self._client is None:
            raise ConfigurationError(MSG_NIMBLE_ASYNC_CLIENT_ONLY)
        return self._client

    @staticmethod
    def _within_deadline(client: _ClientT) -> _ClientT:
        """``client`` for one call, its every limit clamped to the time a deadline in force leaves.

        The client itself with no deadline. Under one the SDK's retries are
        off as well: a retry starts a new request with the full clamped limit,
        so two of them would hold the caller past the deadline.
        """
        if current_deadline() is None:
            return client
        return client.with_options(timeout=clamp_timeout(client.timeout), max_retries=0)

    def close(self) -> None:
        if self._client is not None:
            self._client.close()

    async def aclose(self) -> None:
        if self._owns_clients:
            await self._async_client.close()


def _resolve_key(api_key: str | None, async_client: AsyncNimble | None, provider: str) -> str | None:
    """The key to build clients from: none when the caller's client carries its own.

    Raises:
        ConfigurationError: for a key passed beside a client, which would
            replace the client's credential -- an end user's, say -- with it.
    """
    if async_client is None:
        return resolve_api_key(api_key, env_var=ENV_NIMBLE_API_KEY, provider=provider)
    if api_key is not None:
        raise ConfigurationError(MSG_NIMBLE_CLIENT_AND_KEY)
    return None


class _NimbleKeyless(_NimbleClients):
    """The keyless switch Nimble search and extract share.

    Without a key a call goes to the same endpoint with no ``Authorization``
    header, which Nimble serves on its keyless tier. The SDK refuses a request
    with no credential unless the header is omitted on the call itself, so each
    call's payload goes through ``_authed``.
    """

    def __init__(self, *, allow_keyless: bool = False, **kwargs: Any) -> None:
        """
        Args:
            allow_keyless: Run without a credential when none is available, on
                Nimble's keyless tier, which caps each caller's calls a day. Off
                by default: a missing key on a provider the caller named should
                say so, not quietly downgrade the account it searches under.
            **kwargs: Forwarded to :class:`_NimbleClients`.
        """
        # Set before the base class resolves the key, which reads both.
        self._allow_keyless = allow_keyless
        self._keyless = False
        super().__init__(**kwargs)

    def _key(self, api_key: str | None, async_client: AsyncNimble | None) -> str | None:
        # A caller's client carries its own credential; a key beside one is a conflict, never a cue to go keyless.
        if async_client is not None or not self._allow_keyless:
            return super()._key(api_key, async_client)
        try:
            return super()._key(api_key, async_client)
        except ConfigurationError:
            self._keyless = True
            return None

    def _connect(self, api_key: str | None, async_client: AsyncNimble | None) -> None:
        # A spent daily cap answers 429; retrying it only delays the fallback to the next keyless backend.
        if self._keyless:
            self.options = {"max_retries": 0, **self.options}
        super()._connect(api_key, async_client)

    def _authed(self, payload: dict[str, Any]) -> dict[str, Any]:
        """``payload``, keyless, with the key's header omitted beside any ``extra_headers`` the caller set."""
        if not self._keyless:
            return payload
        return {**payload, "extra_headers": {**(payload.get("extra_headers") or {}), NIMBLE_AUTH_HEADER: omit}}
