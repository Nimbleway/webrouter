"""The HTTP base class for a browser backend: open a session in one request, end it by path and verb.

Re-exported by :mod:`web_router.providers._http`, where a provider imports it from.
"""

from __future__ import annotations

import abc
import contextlib
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, ClassVar, Literal
from urllib.parse import quote

import httpx
from pydantic import SecretStr

from web_router.constants import BROWSER_PERSIST_PROFILE_REQUIRES, BROWSER_SESSION_ID_FIELD
from web_router.providers._http_base import _HttpProvider
from web_router.providers.base import BrowserProvider
from web_router.types import BrowserRequest, BrowserSession

ReleaseMethod = Literal["POST", "PUT", "DELETE"]
"""The verbs a browser backend ends a session with."""


class HttpBrowserProvider(_HttpProvider, BrowserProvider):
    """A browser backend over a plain HTTP endpoint.

    Opening a session is one request with one shape, so this class owns it:
    a subclass builds the body with :meth:`_build_payload` and reads the answer
    with :meth:`_to_session`. Ending one is a path and a method, declared as
    :attr:`release_path` and :attr:`release_method`, because backends disagree
    about the verb -- a ``POST`` to a release action, a ``PUT`` to a stop, a
    ``DELETE`` on the session -- far more than about anything else. A backend
    whose release needs a body declares :attr:`release_body`, and one that
    answers a refused release with a 200 overrides :meth:`_check_release`. A
    backend that can report a session's state implements :meth:`_fetch`
    itself, over :meth:`_get`.
    """

    open_path: ClassVar[str]

    co_requirements: ClassVar[Mapping[str, Mapping[Any, frozenset[str]]]] = MappingProxyType(
        BROWSER_PERSIST_PROFILE_REQUIRES
    )
    """Saving a profile means nothing without one to save to, on every vendor that maps profiles.

    A subclass with partners of its own merges them over this, as Anchor does;
    one that maps no profiles sets it empty, which the contract tests require.
    """

    release_path: ClassVar[str]
    """Where a session is ended, with ``{session_id}`` where its id goes."""

    release_method: ClassVar[ReleaseMethod] = "POST"

    release_body: ClassVar[dict[str, Any] | None] = None
    """The JSON body the release call sends, for a backend whose release is an update; none by default."""

    session_id_field: ClassVar[str] = BROWSER_SESSION_ID_FIELD
    """Where the open response carries the session id; read only to release an orphan."""

    # -- What subclasses implement -------------------------------------------

    @abc.abstractmethod
    def _build_payload(self, request: BrowserRequest) -> dict[str, Any]:
        """Translate a normalized request into the body that opens a session."""

    @abc.abstractmethod
    def _to_session(self, request: BrowserRequest, payload: dict[str, Any]) -> BrowserSession:
        """Translate this API's answer into a normalized session."""

    # -- The HTTP plumbing ---------------------------------------------------

    def _open(self, request: BrowserRequest) -> BrowserSession:
        response = self._client.post(self.open_path, json=self._build_payload(request))
        payload = self._payload(response)
        try:
            return self._to_session(request, payload)
        except Exception:
            # The backend opened a session we cannot describe, and it bills
            # from now on. Release it if the id is there to release by; if that
            # fails too, the original error is still the one worth raising.
            orphan = self._orphan_id(payload)
            if orphan is not None:
                with contextlib.suppress(Exception):
                    self._release(orphan)
            raise

    async def _aopen(self, request: BrowserRequest) -> BrowserSession:
        response = await self._async_client.post(self.open_path, json=self._build_payload(request))
        payload = self._payload(response)
        try:
            return self._to_session(request, payload)
        except Exception:
            orphan = self._orphan_id(payload)
            if orphan is not None:
                with contextlib.suppress(Exception):
                    await self._arelease(orphan)
            raise

    @staticmethod
    def _secret_field(payload: Mapping[str, Any], field: str) -> SecretStr | None:
        """``payload[field]`` as a secret when it is a non-empty string, else ``None`` -- an empty link is no link."""
        value = payload.get(field)
        return SecretStr(value) if isinstance(value, str) and value else None

    def _orphan_id(self, payload: dict[str, Any]) -> str | None:
        session_id = payload.get(self.session_id_field)
        return session_id if isinstance(session_id, str) and session_id else None

    def _check_release(self, response: httpx.Response) -> None:
        """Raise if a release the backend answered with a success status did not end the session.

        Called after the status check. The default accepts, because most
        backends answer a release with nothing more than its status.
        """

    def _release(self, session_id: str) -> None:
        response = self._client.request(self.release_method, self._session_path(session_id), json=self.release_body)
        self._raise_for_status(response)
        self._check_release(response)

    async def _arelease(self, session_id: str) -> None:
        response = await self._async_client.request(
            self.release_method, self._session_path(session_id), json=self.release_body
        )
        self._raise_for_status(response)
        self._check_release(response)

    def _get(self, path: str) -> dict[str, Any]:
        return self._payload(self._client.get(path))

    async def _aget(self, path: str) -> dict[str, Any]:
        return self._payload(await self._async_client.get(path))

    def _session_path(self, session_id: str, template: str | None = None) -> str:
        """``template`` -- :attr:`release_path` by default -- with the session id in it.

        Quoted, because the id is whatever the backend sent: one carrying a
        slash or a ``?`` must not be able to point the request somewhere else.
        """
        return (template or self.release_path).format(session_id=quote(session_id, safe=""))
