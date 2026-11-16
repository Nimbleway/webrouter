"""``auto`` warns about a refused key only when the backend held one: a keyless backend's 401 or 403 refused nothing."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from web_router.exceptions import ProviderError
from web_router.providers.agentcore import AgentCoreProvider
from web_router.providers.auto import AutoProvider
from web_router.providers.base import SearchProvider
from web_router.registry import register_provider, unregister_provider
from web_router.types import SearchRequest, SearchResponse, SearchResult


class _Refusing(SearchProvider):
    """Refuses every call with a 403, the way a backend refuses a key -- or a keyless caller."""

    name = "refusing"
    supported_parameters = frozenset({"max_results"})

    def _search(self, request: SearchRequest) -> SearchResponse:
        return _refuse(self.name)

    async def _asearch(self, request: SearchRequest) -> SearchResponse:
        return self._search(request)


def _refuse(name: str) -> SearchResponse:
    http = httpx.Request("GET", "https://api.example.com")
    refused = httpx.HTTPStatusError("refused", request=http, response=httpx.Response(403, request=http))
    raise ProviderError(name, "HTTP 403", original=refused)


class _EnvRefusing(_Refusing):
    """Refuses with a 403, its key read from the environment rather than passed."""

    name = "envrefusing"
    env_api_key = "WEB_ROUTER_TEST_REFUSING_KEY"


class _SignedRefusing(AgentCoreProvider):
    """AgentCore, its AWS signature refused with a 403."""

    name = "signedrefusing"

    def _search(self, request: SearchRequest) -> SearchResponse:
        return _refuse(self.name)

    async def _asearch(self, request: SearchRequest) -> SearchResponse:
        return _refuse(self.name)


class _Spare(_Refusing):
    name = "spare"

    def _search(self, request: SearchRequest) -> SearchResponse:
        return SearchResponse(provider=self.name, query=request.query, results=[SearchResult(url="https://a.example")])


_MEMBERS = (_Refusing, _EnvRefusing, _SignedRefusing, _Spare)


@pytest.fixture(autouse=True)
def _registered() -> Iterator[None]:
    for member in _MEMBERS:
        register_provider(member.name, member)
    yield
    for member in _MEMBERS:
        unregister_provider(member.name)


@pytest.mark.parametrize(("config", "warned"), [({"api_key": "k"}, True), ({}, False)])
def test_a_refusal_warns_only_when_a_key_was_held(
    caplog: pytest.LogCaptureFixture, config: dict[str, Any], warned: bool
) -> None:
    auto = AutoProvider(chain=[(_Refusing.name, config), (_Spare.name, {})])

    with caplog.at_level(logging.INFO, logger="web_router"):
        response = auto.search(SearchRequest(query="q"))

    assert response.provider == "spare"
    warnings = [record.getMessage().split(";")[0] for record in caplog.records if record.levelno == logging.WARNING]
    assert warnings == (["refusing refused its credential (HTTP 403)"] if warned else [])
    assert any("refusing" in record.getMessage() for record in caplog.records if record.levelno == logging.INFO)


def _warned(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [record.getMessage().split(";")[0] for record in caplog.records if record.levelno == logging.WARNING]


def test_a_refusal_warns_when_the_key_comes_from_the_environment(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(_EnvRefusing.env_api_key, "k")
    auto = AutoProvider(chain=[(_EnvRefusing.name, {}), (_Spare.name, {})])

    with caplog.at_level(logging.INFO, logger="web_router"):
        auto.search(SearchRequest(query="q"))

    assert _warned(caplog) == ["envrefusing refused its credential (HTTP 403)"]


@pytest.mark.parametrize(("config", "warned"), [({"api_key": "k"}, True), ({}, False)])
def test_the_async_router_warns_only_when_a_key_was_held(
    caplog: pytest.LogCaptureFixture, config: dict[str, Any], warned: bool
) -> None:
    auto = AutoProvider(chain=[(_Refusing.name, config), (_Spare.name, {})])

    with caplog.at_level(logging.INFO, logger="web_router"):
        response = asyncio.run(auto.asearch(SearchRequest(query="q")))

    assert response.provider == "spare"
    assert _warned(caplog) == (["refusing refused its credential (HTTP 403)"] if warned else [])


def test_agentcore_refusing_its_signature_warns(caplog: pytest.LogCaptureFixture) -> None:
    gateway = {"gateway_url": "https://gw.example.amazonaws.com/mcp"}
    auto = AutoProvider(chain=[(_SignedRefusing.name, gateway), (_Spare.name, {})])

    with caplog.at_level(logging.INFO, logger="web_router"):
        auto.search(SearchRequest(query="q"))

    assert _warned(caplog) == ["signedrefusing refused its credential (HTTP 403)"]


def test_nimble_with_a_callers_client_holds_a_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    async_nimble = pytest.importorskip("nimble_python").AsyncNimble
    from web_router.providers.nimble import NimbleProvider

    monkeypatch.delenv("NIMBLE_API_KEY", raising=False)
    assert NimbleProvider(async_client=async_nimble(api_key="caller"), allow_keyless=True).holds_credential
    assert not NimbleProvider(allow_keyless=True).holds_credential
