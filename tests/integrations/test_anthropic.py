"""The Claude tools: what the agent is given, what the model reads back, and who hears about a failure."""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest

from tests.conftest import FakeExtractProvider, FakeProvider
from web_router import AsyncWebRouter, WebRouter
from web_router.exceptions import ConfigurationError, ProviderError, UnsupportedParameterError
from web_router.integrations.anthropic import WebRouterTools, routing_guide
from web_router.registry import (
    register_extract_provider,
    register_provider,
    unregister_extract_provider,
    unregister_provider,
)
from web_router.types import SearchRequest, SearchResponse


class DownProvider(FakeProvider):
    name = "down"

    def _respond(self, request: SearchRequest) -> SearchResponse:
        raise ProviderError(self.name, "HTTP 503: unavailable")


class NarrowProvider(FakeProvider):
    name = "narrow"
    supported_parameters = frozenset({"max_results"})


@pytest.fixture(autouse=True)
def providers() -> Iterator[None]:
    for provider in (FakeProvider, DownProvider, NarrowProvider):
        register_provider(provider.name, provider)
    register_extract_provider(FakeExtractProvider.name, FakeExtractProvider)
    yield
    for provider in (FakeProvider, DownProvider, NarrowProvider):
        unregister_provider(provider.name)
    unregister_extract_provider(FakeExtractProvider.name)


def test_the_agent_gets_custom_tools_whose_only_input_is_the_query_or_the_urls() -> None:
    definitions = WebRouterTools(WebRouter(), provider="fake").definitions()

    assert [d["name"] for d in definitions] == ["web_router_search", "web_router_extract"]
    assert all(d["type"] == "custom" for d in definitions)
    assert [d["input_schema"]["required"] for d in definitions] == [["query"], ["urls"]]
    assert all(d["input_schema"]["additionalProperties"] is False for d in definitions)
    json.dumps(definitions)  # sent as-is in a request body


def test_methods_choose_which_tools_are_exposed_and_an_unknown_one_is_refused() -> None:
    assert WebRouterTools(WebRouter(), methods=["search"]).names == ("web_router_search",)
    with pytest.raises(ValueError, match="research"):
        WebRouterTools(WebRouter(), methods=["search", "research"])


def test_a_call_sends_the_callers_options_and_returns_the_rendering_and_the_response() -> None:
    router = WebRouter()
    tools = WebRouterTools(router, provider="fake", max_results=3, search_depth="deep")

    result = tools.call("web_router_search", {"query": "who won"})

    request = router.get_provider("fake").requests[-1]
    assert (request.query, request.max_results, request.search_depth) == ("who won", 3, "deep")
    assert not result.is_error
    assert json.loads(result.content)["results"][0]["url"] == "https://example.com"
    assert result.provider == "fake"
    assert result.event_fields() == {"content": [{"type": "text", "text": result.content}], "is_error": False}


def test_a_backend_failure_is_an_error_result_the_model_reads() -> None:
    result = WebRouterTools(WebRouter(), provider="down").call("web_router_search", {"query": "q"})

    assert result.is_error
    assert result.content.startswith("web_router_search failed:") and "HTTP 503" in result.content
    assert result.response is None


def test_a_setup_mistake_raises_to_the_caller() -> None:
    tools = WebRouterTools(WebRouter(), provider="narrow", country="US")

    with pytest.raises(UnsupportedParameterError):
        tools.call("web_router_search", {"query": "q"})


@pytest.mark.parametrize(
    ("name", "tool_input"),
    [
        ("web_router_search", {}),
        ("web_router_search", {"query": "  "}),
        ("web_router_extract", {"urls": []}),
        ("web_router_extract", {"urls": [f"https://{n}.example" for n in range(6)]}),
        ("web_router_extract", {"urls": "https://a.example"}),
    ],
)
def test_malformed_input_from_the_model_is_an_error_result_and_bills_nothing(name: str, tool_input: dict) -> None:
    router = WebRouter()
    tools = WebRouterTools(router, provider="fake")

    result = tools.call(name, tool_input)

    assert result.is_error and name in result.content
    assert router.get_provider("fake").requests == []


def test_a_tool_name_this_instance_does_not_serve_raises() -> None:
    with pytest.raises(ValueError, match="web_router_extract"):
        WebRouterTools(WebRouter(), methods=["search"]).call("web_router_extract", {"urls": ["https://a.example"]})


def test_extract_goes_to_the_search_provider_when_it_also_extracts() -> None:
    assert WebRouterTools(WebRouter(), provider="fake").extract_provider == "fake"
    assert WebRouterTools(WebRouter(), provider="down").extract_provider is None
    assert WebRouterTools(WebRouter(), provider="FAKE").extract_provider == "fake"


def test_a_router_whose_default_cannot_extract_raises_when_the_tools_are_built() -> None:
    with pytest.raises(ConfigurationError, match="extract_provider"):
        WebRouterTools(WebRouter("down"), provider="down")

    assert WebRouterTools(WebRouter("down"), provider="down", extract_provider="fake").extract_provider == "fake"
    assert WebRouterTools(WebRouter("down"), methods=["search"]).names == ("web_router_search",)


async def test_acall_runs_on_the_async_router() -> None:
    async with AsyncWebRouter() as router:
        tools = WebRouterTools(router, provider="fake", max_results=2)
        result = await tools.acall("web_router_extract", {"urls": ["https://a.example"]})

        assert json.loads(result.content)["documents"][0]["url"] == "https://a.example"
        assert router.get_extract_provider("fake").calls[-1] == ["https://a.example"]


async def test_acall_on_a_sync_router_runs_it_on_a_worker_thread() -> None:
    router = WebRouter()

    result = await WebRouterTools(router, provider="fake").acall("web_router_search", {"query": "q"})

    assert result.provider == "fake"


def test_the_guide_names_each_exposed_tool_cheapest_first_and_nothing_else() -> None:
    both = routing_guide()
    search_only = routing_guide(["search"])

    assert both.index("web_router_search") < both.index("web_router_extract")
    assert "web_router_extract" not in search_only.replace("web_router_search", "")
    assert WebRouterTools(WebRouter(), methods=["search"]).guide() == search_only
    assert "untrusted" in both


def test_the_model_reads_the_same_rendering_the_langchain_tool_gives() -> None:
    pytest.importorskip("langchain_core")
    from web_router.integrations.langchain import WebRouterSearch

    router = WebRouter()
    langchain = WebRouterSearch(router=router, provider="fake").invoke(
        {"type": "tool_call", "id": "c", "name": "web_search", "args": {"query": "q"}}
    )
    claude = WebRouterTools(router, provider="fake").call("web_router_search", {"query": "q"})

    assert claude.content == langchain.content
