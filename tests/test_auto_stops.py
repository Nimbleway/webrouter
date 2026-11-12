"""``auto`` moves on from a backend's failure, but not from one the next backend would repeat, and not silently."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterator
from typing import Any, ClassVar

import httpx
import pytest

from web_router.constants import ENV_OXYLABS_USERNAME
from web_router.constants_live_data_vendors import ENV_OXYLABS_WEB_API_KEY
from web_router.exceptions import ConfigurationError, ProviderError
from web_router.providers.auto import AutoExtractProvider, AutoProvider
from web_router.providers.base import ExtractProvider, SearchProvider
from web_router.registry import (
    register_extract_provider,
    register_provider,
    unregister_extract_provider,
    unregister_provider,
)
from web_router.types import (
    ExtractedDocument,
    ExtractFailure,
    ExtractRequest,
    ExtractResponse,
    SearchRequest,
    SearchResponse,
    SearchResult,
)

TEXT = "A page with enough text in it to count as the page that was asked for, not a shell."


def _searcher(name: str, fails: Callable[[], Exception] | None = None) -> type[SearchProvider]:
    """A search backend that answers, or raises what ``fails`` builds."""

    class Member(SearchProvider):
        supported_parameters = frozenset({"max_results"})
        calls: ClassVar[int] = 0

        def _search(self, request: SearchRequest) -> SearchResponse:
            type(self).calls += 1
            if fails is not None:
                raise fails()
            return SearchResponse(provider=name, query=request.query, results=[SearchResult(url="https://a.example")])

        async def _asearch(self, request: SearchRequest) -> SearchResponse:
            return self._search(request)

    Member.name, Member.calls = name, 0
    return Member


@pytest.fixture
def chain() -> Iterator[Callable[..., AutoProvider]]:
    names: list[str] = []

    def build(*members: type[SearchProvider]) -> AutoProvider:
        for member in members:
            register_provider(member.name, member)
            names.append(member.name)
        return AutoProvider(chain=[(member.name, {}) for member in members])

    yield build
    for name in names:
        unregister_provider(name)


def _refused(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://api.example.com")
    return httpx.HTTPStatusError("refused", request=request, response=httpx.Response(status, request=request))


def test_a_providers_own_bug_raises_instead_of_moving_on(chain: Callable[..., AutoProvider]) -> None:
    """A KeyError in a provider's parsing would quietly bill the next backend on every call."""
    broken, spare = _searcher("broken", lambda: KeyError("results")), _searcher("spare")

    with pytest.raises(ProviderError) as caught:
        chain(broken, spare).search(SearchRequest(query="q"))

    assert caught.value.kind == "local"
    assert spare.calls == 0


def test_a_target_side_failure_raises_instead_of_moving_on(chain: Callable[..., AutoProvider]) -> None:
    gone = _searcher("gone", lambda: ProviderError("gone", "HTTP 400: nx_domain_error"))
    spare = _searcher("spare")

    with pytest.raises(ProviderError, match="nx_domain_error"):
        chain(gone, spare).search(SearchRequest(query="q"))

    assert spare.calls == 0


def test_a_refused_key_moves_on_and_is_logged(
    chain: Callable[..., AutoProvider], caplog: pytest.LogCaptureFixture
) -> None:
    refused = _searcher("refused", lambda: ProviderError("refused", "HTTP 401", original=_refused(401)))
    spare = _searcher("spare")

    with caplog.at_level(logging.WARNING, logger="web_router"):
        response = chain(refused, spare).search(SearchRequest(query="q"))

    assert response.provider == "spare"
    assert any("refused refused its credential (HTTP 401)" in record.getMessage() for record in caplog.records)


def _extractor(name: str, failure: dict[str, Any] | None = None) -> type[ExtractProvider]:
    """An extract backend that reads every URL, except one it reports failed as ``failure`` says."""

    class Member(ExtractProvider):
        supported_parameters = frozenset({"urls", "format"})
        supported_formats = frozenset({"markdown"})
        default_format = "markdown"
        batches_urls = True
        calls: ClassVar[list[list[str]]] = []

        def _extract(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
            type(self).calls.append(list(urls))
            failed = [ExtractFailure(**failure)] if failure else []
            skipped = {item.url for item in failed}
            documents = [ExtractedDocument(url=url, content=TEXT) for url in urls if url not in skipped]
            return ExtractResponse(provider=name, documents=documents, failures=failed, raw=[{}])

        async def _aextract(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
            return self._extract(request, urls)

    Member.name, Member.calls = name, []
    return Member


def test_a_batch_does_not_retry_a_url_whose_page_is_gone() -> None:
    gone = "https://example.org/removed"
    first = _extractor("first", {"url": gone, "error": "HTTP 404", "kind": "target", "status_code": 404})
    second = _extractor("second")
    for member in (first, second):
        register_extract_provider(member.name, member)
    try:
        auto = AutoExtractProvider(chain=[("first", {}), ("second", {})])
        response = auto.extract(ExtractRequest(urls=["https://example.org/a", gone]))
    finally:
        for member in (first, second):
            unregister_extract_provider(member.name)

    assert [failure.url for failure in response.failures] == [gone]
    assert second.calls == [], "every backend would read the same 404"


def test_oxylabs_live_data_says_the_extract_credentials_are_another_set(monkeypatch: pytest.MonkeyPatch) -> None:
    from web_router.providers.oxylabs_live_data import OxylabsLiveDataProvider

    monkeypatch.delenv(ENV_OXYLABS_WEB_API_KEY, raising=False)
    monkeypatch.setenv(ENV_OXYLABS_USERNAME, "realtime-user")

    with pytest.raises(ConfigurationError, match="separate credential"):
        OxylabsLiveDataProvider()

    monkeypatch.delenv(ENV_OXYLABS_USERNAME)
    with pytest.raises(ConfigurationError) as caught:
        OxylabsLiveDataProvider()
    assert "separate credential" not in str(caught.value), "nothing to confuse it with"
    assert os.environ.get(ENV_OXYLABS_WEB_API_KEY) is None


def test_a_page_that_is_not_retried_keeps_its_failure_and_kind() -> None:
    """The 404 is left out of the retry, not out of the answer, while the blocked page is retried and read."""
    gone, blocked = "https://example.org/removed", "https://example.org/blocked"

    class GoneAndBlocked(ExtractProvider):
        name = "goneandblocked"
        supported_parameters = frozenset({"urls", "format"})
        supported_formats = frozenset({"markdown"})
        default_format = "markdown"
        batches_urls = True

        def _extract(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
            failures = [
                ExtractFailure(url=gone, error="HTTP 404", kind="target", status_code=404),
                ExtractFailure(url=blocked, error="challenge", kind="upstream"),
            ]
            documents = [ExtractedDocument(url=url, content=TEXT) for url in urls if url not in (gone, blocked)]
            return ExtractResponse(provider=self.name, documents=documents, failures=failures, raw=[{}])

        async def _aextract(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
            return self._extract(request, urls)

    second = _extractor("second")
    for member in (GoneAndBlocked, second):
        register_extract_provider(member.name, member)
    try:
        auto = AutoExtractProvider(chain=[("goneandblocked", {}), ("second", {})])
        response = auto.extract(ExtractRequest(urls=["https://example.org/a", gone, blocked]))
    finally:
        for member in (GoneAndBlocked, second):
            unregister_extract_provider(member.name)

    assert second.calls == [[blocked]], "only the blocked page is worth another backend"
    assert [document.url for document in response.documents] == ["https://example.org/a", blocked]
    assert [(failure.url, failure.kind, failure.status_code) for failure in response.failures] == [
        (gone, "target", 404)
    ]


def test_a_batch_whose_every_page_is_gone_stops_auto() -> None:
    gone = "https://example.org/removed"

    class AllGone(ExtractProvider):
        name = "allgone"
        supported_parameters = frozenset({"urls", "format"})
        supported_formats = frozenset({"markdown"})
        default_format = "markdown"

        def _extract(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
            failure = ExtractFailure(url=urls[0], error="HTTP 404", kind="target", status_code=404)
            return ExtractResponse(provider="allgone", documents=[], failures=[failure], raw=[{}])

        async def _aextract(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
            return self._extract(request, urls)

    second = _extractor("second")
    for member in (AllGone, second):
        register_extract_provider(member.name, member)
    try:
        auto = AutoExtractProvider(chain=[("allgone", {}), ("second", {})])
        with pytest.raises(ProviderError) as caught:
            auto.extract(ExtractRequest(urls=[gone]))
    finally:
        for member in (AllGone, second):
            unregister_extract_provider(member.name)

    assert caught.value.kind == "target"
    assert second.calls == []
