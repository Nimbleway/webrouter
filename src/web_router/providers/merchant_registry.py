"""Reading the registry for the ``merchant`` backend, sync and async, through the provider's own HTTP clients.

Two documents decide a call: the version pointer (``latest.json``), then the exact domain record in the snapshot it
names. The pointer is trusted for a short time and a snapshot's records are kept, so a store call costs one registry
read in the common case, not two. Every request goes through the provider's clients, so the router's timeout and
deadline clamp it. A missing record is a :class:`~web_router.LiveDataSourceNotFoundError`; any other failure is not.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import httpx

from web_router.constants_merchant import (
    MSG_REGISTRY_INVALID,
    MSG_REGISTRY_UNREADABLE,
    PROVIDER_MERCHANT,
    REGISTRY_DOMAIN_PATH,
    REGISTRY_INDEX_PATH,
    REGISTRY_LATEST,
    REGISTRY_MAX_BYTES,
    REGISTRY_POINTER_TTL_S,
)
from web_router.exceptions import ProviderError
from web_router.exceptions_live_data import LiveDataSourceNotFoundError
from web_router.providers.merchant_record import read_pointer
from web_router.types_errors import ERROR_KIND_UPSTREAM


class RegistryReader:
    """Mixed into the provider: the registry's documents, cached. Needs ``_client``, ``_async_client``, ``_clamped``."""

    def _init_registry(self) -> None:
        self._pointer: tuple[str, float] | None = None
        self._records: dict[tuple[str, str], Any] = {}
        self._indexes: dict[str, list[Any]] = {}
        self._cache_lock = threading.Lock()  # threads sharing a router write the caches and rebuild them

    # -- decoding ------------------------------------------------------------

    def _decode(self, response: httpx.Response, url: str) -> Any:
        if response.status_code >= httpx.codes.BAD_REQUEST:
            raise ProviderError(
                PROVIDER_MERCHANT,
                MSG_REGISTRY_UNREADABLE.format(url=url, reason=f"HTTP {response.status_code}"),
                original=httpx.HTTPStatusError(
                    f"HTTP {response.status_code}", request=response.request, response=response
                ),
                kind=ERROR_KIND_UPSTREAM,
            )
        if len(response.content) > REGISTRY_MAX_BYTES:
            raise ProviderError(PROVIDER_MERCHANT, MSG_REGISTRY_INVALID.format(reason="response too large"))
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderError(
                PROVIDER_MERCHANT,
                MSG_REGISTRY_INVALID.format(reason="not JSON"),
                original=exc,
                kind=ERROR_KIND_UPSTREAM,
            ) from exc

    def _fresh(self) -> str | None:
        if self._pointer is not None and time.monotonic() - self._pointer[1] < REGISTRY_POINTER_TTL_S:
            return self._pointer[0]
        return None

    def _remember(self, document: Any) -> str:
        snapshot_id = read_pointer(document)
        with self._cache_lock:
            self._pointer = (snapshot_id, time.monotonic())
            self._records = {key: value for key, value in self._records.items() if key[0] == snapshot_id}
            self._indexes = {key: value for key, value in self._indexes.items() if key == snapshot_id}
        return snapshot_id

    # -- sync ----------------------------------------------------------------

    def _fetch(self, path: str) -> Any:
        response = self._client.get(path, **self._clamped())  # type: ignore[attr-defined]
        return self._decode(response, path)

    def _snapshot(self) -> str:
        return self._fresh() or self._remember(self._fetch(REGISTRY_LATEST))

    def _record(self, domain: str, snapshot_id: str) -> Any:
        if (snapshot_id, domain) not in self._records:
            path = REGISTRY_DOMAIN_PATH.format(snapshot_id=snapshot_id, domain=domain)
            try:
                self._store_record(snapshot_id, domain, self._fetch(path))
            except ProviderError as exc:
                raise self._not_found(exc, domain, snapshot_id) from exc
        return self._records[(snapshot_id, domain)]

    def _index(self, snapshot_id: str) -> list[Any]:
        if snapshot_id not in self._indexes:
            document = self._fetch(REGISTRY_INDEX_PATH.format(snapshot_id=snapshot_id))
            self._store_index(snapshot_id, self._sites(document))
        return self._indexes[snapshot_id]

    # -- async ---------------------------------------------------------------

    async def _afetch(self, path: str) -> Any:
        response = await self._async_client.get(path, **self._clamped())  # type: ignore[attr-defined]
        return self._decode(response, path)

    async def _asnapshot(self) -> str:
        return self._fresh() or self._remember(await self._afetch(REGISTRY_LATEST))

    async def _arecord(self, domain: str, snapshot_id: str) -> Any:
        if (snapshot_id, domain) not in self._records:
            path = REGISTRY_DOMAIN_PATH.format(snapshot_id=snapshot_id, domain=domain)
            try:
                self._store_record(snapshot_id, domain, await self._afetch(path))
            except ProviderError as exc:
                raise self._not_found(exc, domain, snapshot_id) from exc
        return self._records[(snapshot_id, domain)]

    async def _aindex(self, snapshot_id: str) -> list[Any]:
        if snapshot_id not in self._indexes:
            document = await self._afetch(REGISTRY_INDEX_PATH.format(snapshot_id=snapshot_id))
            self._store_index(snapshot_id, self._sites(document))
        return self._indexes[snapshot_id]

    # -- shared --------------------------------------------------------------

    def _store_record(self, snapshot_id: str, domain: str, record: Any) -> None:
        with self._cache_lock:
            self._records[(snapshot_id, domain)] = record

    def _store_index(self, snapshot_id: str, sites: list[Any]) -> None:
        with self._cache_lock:
            self._indexes[snapshot_id] = sites

    @staticmethod
    def _sites(document: Any) -> list[Any]:
        sites = document.get("sites") if isinstance(document, dict) else None
        if not isinstance(sites, list):
            raise ProviderError(PROVIDER_MERCHANT, MSG_REGISTRY_INVALID.format(reason="the index has no site list"))
        return sites

    @staticmethod
    def _not_found(error: ProviderError, domain: str, snapshot_id: str) -> ProviderError:
        original = error.original
        if isinstance(original, httpx.HTTPStatusError) and original.response.status_code == httpx.codes.NOT_FOUND:
            return LiveDataSourceNotFoundError(
                PROVIDER_MERCHANT, domain, original=original, hint=f" It is not in registry snapshot {snapshot_id}."
            )
        return error
