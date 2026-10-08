"""The LangChain tools: what the model sees, what the caller keeps, and who hears about a failure."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest

pytest.importorskip("langchain_core")

from langchain_core.messages import ToolMessage
from pydantic import ValidationError

from tests.conftest import FakeExtractProvider, FakeProvider
from web_router import AsyncWebRouter, WebRouter
from web_router.exceptions import ConfigurationError, ProviderError, UnsupportedParameterError
from web_router.integrations.langchain import WebRouterExtract, WebRouterSearch, render_search, web_router_tools
from web_router.registry import (
    register_extract_provider,
    register_provider,
    unregister_extract_provider,
    unregister_provider,
)
from web_router.types import (
    ExtractFailure,
    ExtractRequest,
    ExtractResponse,
    SearchRequest,
    SearchResponse,
    SearchResult,
)

LONG = "word " * 1000


class LongProvider(FakeProvider):
    """Answers with a synthesized answer and one result whose body runs long."""

    name = "long"

    def _respond(self, request: SearchRequest) -> SearchResponse:
        self.requests.append(request)
        return SearchResponse(
            provider=self.name,
            query=request.query,
            answer="forty-two",
            results=[SearchResult(url="https://example.com/a", title="A", content=LONG)],
        )


class DownProvider(FakeProvider):
    name = "down"

    def _respond(self, request: SearchRequest) -> SearchResponse:
        raise ProviderError(self.name, "HTTP 503: unavailable")


class NarrowProvider(FakeProvider):
    name = "narrow"
    supported_parameters = frozenset({"max_results"})


class HalfExtractProvider(FakeExtractProvider):
    """Reads the first URL and fails the rest, as a partly failed extract does."""

    name = "half"
    batches_urls = True

    def _respond(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
        first, *rest = urls
        return ExtractResponse(
            provider=self.name,
            documents=[self._document(request, first)],
            failures=[ExtractFailure(url=url, error="HTTP 404") for url in rest],
        )

    def _document(self, request: ExtractRequest, url: str) -> Any:
        return super()._respond(request, [url]).documents[0]


@pytest.fixture(autouse=True)
def providers() -> Iterator[None]:
    for provider in (FakeProvider, LongProvider, DownProvider, NarrowProvider):
        register_provider(provider.name, provider)
    for extractor in (FakeExtractProvider, HalfExtractProvider):
        register_extract_provider(extractor.name, extractor)
    yield
    for provider in (FakeProvider, LongProvider, DownProvider, NarrowProvider):
        unregister_provider(provider.name)
    for extractor in (FakeExtractProvider, HalfExtractProvider):
        unregister_extract_provider(extractor.name)


def _call(tool: Any, **args: Any) -> ToolMessage:
    message = tool.invoke({"type": "tool_call", "id": "call-1", "name": tool.name, "args": args})
    assert isinstance(message, ToolMessage)
    return message


def test_the_model_supplies_the_query_and_the_caller_every_other_option() -> None:
    router = WebRouter("fake")
    tool = WebRouterSearch(router=router, max_results=3, search_depth="deep", include_domains=["example.com"])

    message = _call(tool, query="who won")

    assert set(tool.args) == {"query"}
    request = router.get_provider("fake").requests[-1]
    assert (request.query, request.max_results, request.search_depth) == ("who won", 3, "deep")
    assert request.include_domains == ["example.com"]
    assert json.loads(message.content)["results"][0]["url"] == "https://example.com"


def test_the_model_reads_a_cut_rendering_and_the_artifact_keeps_the_whole_response() -> None:
    tool = WebRouterSearch(router=WebRouter(), provider="long", result_chars=100)

    message = _call(tool, query="q")

    shown = json.loads(message.content)
    assert shown["answer"] == "forty-two"
    assert len(shown["results"][0]["content"]) < 150
    assert shown["results"][0]["content"].endswith("[truncated]")
    assert isinstance(message.artifact, SearchResponse)
    assert message.artifact.results[0].content == LONG


def test_a_backend_failure_reaches_the_model_as_a_message_not_an_exception() -> None:
    message = _call(WebRouterSearch(router=WebRouter(), provider="down"), query="q")

    assert message.status == "error"
    assert message.content.startswith("web_search failed:")
    assert "HTTP 503" in message.content


def test_an_option_the_provider_does_not_map_raises_to_the_caller() -> None:
    tool = WebRouterSearch(router=WebRouter(), provider="narrow", country="US")

    with pytest.raises(UnsupportedParameterError):
        _call(tool, query="q")


async def test_the_async_router_serves_async_calls_when_given() -> None:
    async with AsyncWebRouter("fake") as router:
        tool = WebRouterSearch(async_router=router, max_results=2)
        message = await tool.ainvoke({"type": "tool_call", "id": "c", "name": tool.name, "args": {"query": "q"}})

        assert router.get_provider("fake").requests[-1].max_results == 2
        assert message.artifact.provider == "fake"


async def test_without_an_async_router_async_calls_run_the_sync_one() -> None:
    router = WebRouter("fake")

    await WebRouterSearch(router=router).ainvoke({"query": "q"})

    assert router.get_provider("fake").requests[-1].query == "q"


def test_an_extract_names_the_urls_it_could_not_read() -> None:
    tool = WebRouterExtract(router=WebRouter(), provider="half")

    message = _call(tool, urls=["https://a.example", "https://b.example"])

    shown = json.loads(message.content)
    assert [d["url"] for d in shown["documents"]] == ["https://a.example"]
    assert shown["failures"] == [{"url": "https://b.example", "error": "HTTP 404"}]


def test_an_extract_takes_a_bounded_number_of_urls_from_the_model() -> None:
    tool = WebRouterExtract(router=WebRouter(), provider="fake")

    with pytest.raises(ValidationError):
        tool.invoke({"urls": [f"https://{n}.example" for n in range(6)]})


def test_the_toolkit_shares_one_router_and_extracts_through_the_same_name_when_it_can() -> None:
    both = web_router_tools(provider="fake", max_results=4)
    search_only = web_router_tools(provider="long")

    assert [t.provider for t in both] == ["fake", "fake"]  # type: ignore[attr-defined]
    assert both[0].router is both[1].router  # type: ignore[attr-defined]
    assert both[0].max_results == 4  # type: ignore[attr-defined]
    assert search_only[1].provider is None  # type: ignore[attr-defined]
    assert web_router_tools(provider="FAKE")[1].provider == "fake"  # type: ignore[attr-defined]


def test_the_toolkit_raises_when_nothing_it_would_extract_through_extracts() -> None:
    with pytest.raises(ConfigurationError, match="extract_provider"):
        web_router_tools(WebRouter("long"), provider="long")


def test_every_backend_is_rendered_in_the_same_shape() -> None:
    one = SearchResponse(provider="a", query="q", results=[SearchResult(url="https://x", title="X", content="c")])
    other = one.model_copy(update={"provider": "b"})

    assert render_search(one) == render_search(other)
