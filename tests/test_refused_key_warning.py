"""``auto`` warns about a refused key only when the backend held one: a keyless backend's 401 or 403 refused nothing."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from web_router.exceptions import ProviderError
from web_router.providers.auto import AutoProvider
from web_router.providers.base import SearchProvider
from web_router.registry import register_provider, unregister_provider
from web_router.types import SearchRequest, SearchResponse, SearchResult


class _Refusing(SearchProvider):
    """Refuses every call with a 403, the way a backend refuses a key -- or a keyless caller."""

    name = "refusing"
    supported_parameters = frozenset({"max_results"})

    def _search(self, request: SearchRequest) -> SearchResponse:
        http = httpx.Request("GET", "https://api.example.com")
        refused = httpx.HTTPStatusError("refused", request=http, response=httpx.Response(403, request=http))
        raise ProviderError(self.name, "HTTP 403", original=refused)

    async def _asearch(self, request: SearchRequest) -> SearchResponse:
        return self._search(request)


class _Spare(_Refusing):
    name = "spare"

    def _search(self, request: SearchRequest) -> SearchResponse:
        return SearchResponse(provider=self.name, query=request.query, results=[SearchResult(url="https://a.example")])


@pytest.fixture(autouse=True)
def _registered() -> Iterator[None]:
    for member in (_Refusing, _Spare):
        register_provider(member.name, member)
    yield
    for member in (_Refusing, _Spare):
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
