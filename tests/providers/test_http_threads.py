"""A provider shared across threads builds each of its HTTP clients, and each ``auto`` member, once.

Clients are built on first use. Without a lock, threads making that first call
together each built one; only the last was kept, so ``close()`` left the others'
sockets open.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any, ClassVar

import httpx
import pytest

from web_router import registry
from web_router.providers._http import HttpSearchProvider
from web_router.providers.auto import AutoProvider
from web_router.types import SearchRequest, SearchResponse
from web_router.vendors.caller import VendorCaller

THREADS = 8


class _Keyless(HttpSearchProvider):
    name: ClassVar[str] = "acme-threads"
    supported_parameters: ClassVar[frozenset[str]] = frozenset()
    base_url: ClassVar[str] = "https://api.acme.test"
    search_path: ClassVar[str] = "/v1/search"

    def _build_payload(self, request: SearchRequest) -> dict[str, Any]:
        return {"q": request.query}

    def _to_response(self, request: SearchRequest, payload: dict[str, Any]) -> SearchResponse:
        return SearchResponse(provider=self.name, query=request.query, raw=payload)


@pytest.fixture
def built(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Every client constructed, by kind; construction is slowed to widen the race window."""
    seen: list[str] = []

    def counting(cls: type[Any], kind: str) -> type[Any]:
        class Counting(cls):  # type: ignore[misc,valid-type]
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                seen.append(kind)
                time.sleep(0.02)
                super().__init__(*args, **kwargs)

        return Counting

    monkeypatch.setattr(httpx, "Client", counting(httpx.Client, "sync"))
    monkeypatch.setattr(httpx, "AsyncClient", counting(httpx.AsyncClient, "async"))
    yield seen


def _together(call: Callable[[], object]) -> None:
    """Run ``call`` on every thread at once, re-raising the first failure."""
    start = threading.Barrier(THREADS)

    def run() -> object:
        start.wait()
        return call()

    with ThreadPoolExecutor(THREADS) as pool:
        for future in [pool.submit(run) for _ in range(THREADS)]:
            future.result()


def test_threads_sharing_a_provider_build_one_sync_client(built: list[str]) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"results": []}))
    provider = _Keyless(transport=transport)
    _together(lambda: provider.search(SearchRequest(query="q")))
    assert built == ["sync"]
    provider.close()


def test_threads_sharing_a_provider_build_one_async_client(built: list[str]) -> None:
    provider = _Keyless()
    _together(lambda: provider._async_client)
    assert built == ["async"]


def test_threads_sharing_a_vendor_caller_build_one_client_of_each_kind(built: list[str]) -> None:
    caller = VendorCaller({})
    _together(caller._client)
    _together(caller._aclient)
    assert sorted(built) == ["async", "sync"]
    caller.close()


def test_threads_sharing_an_auto_provider_build_each_member_once(
    built: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"results": []}))
    members: list[str] = []

    def create(name: str, **options: Any) -> _Keyless:
        members.append(name)
        time.sleep(0.02)
        return _Keyless(transport=transport)

    monkeypatch.setattr(registry, "create_provider", create)
    auto = AutoProvider(chain=["tavily"], fallback_on_empty=False)
    _together(lambda: auto.search(SearchRequest(query="q")))
    assert members == ["tavily"]
    assert built == ["sync"]
    auto.close()
