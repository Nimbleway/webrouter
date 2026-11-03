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


def _no_answer_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """No answer backend configured, whatever .env holds."""
    from web_router.constants import ANSWER_ENV_BY_PROVIDER

    for name in list(ANSWER_ENV_BY_PROVIDER):
        monkeypatch.delitem(ANSWER_ENV_BY_PROVIDER, name)


def test_the_answer_tool_is_exposed_only_when_asked_for(fake_answer_provider: type) -> None:
    assert "web_router_answer" not in WebRouterTools(WebRouter(), provider="fake").names

    definitions = WebRouterTools(WebRouter(), methods=["search", "answer"], provider="fake").definitions()

    assert [d["name"] for d in definitions] == ["web_router_search", "web_router_answer"]
    assert definitions[1]["input_schema"]["required"] == ["query"]
    assert definitions[1]["input_schema"]["additionalProperties"] is False


def test_an_answer_call_sends_only_the_question_and_returns_the_answer_and_its_sources(
    fake_answer_provider: type,
) -> None:
    router = WebRouter()
    tools = WebRouterTools(router, methods=["answer"], provider="fake", include_domains=["example.com"])

    result = tools.call("web_router_answer", {"query": "who won"})

    request = router.get_answer_provider("fake").requests[-1]
    assert (request.query, request.include_domains) == ("who won", None), "search options are not the answer's"
    assert json.loads(result.content) == {
        "answer": "the answer",
        "citations": [{"title": "Example", "url": "https://example.com", "snippet": ""}],
    }
    assert not result.is_error
    assert result.provider == "fake"


def test_the_answer_backend_is_the_one_named_else_the_search_one_when_it_answers(fake_answer_provider: type) -> None:
    assert WebRouterTools(WebRouter(), methods=["answer"], provider="fake").answer_provider == "fake"
    assert WebRouterTools(WebRouter(), methods=["answer"], answer_provider="FAKE").answer_provider == "fake"


def test_a_router_with_no_answer_backend_raises_when_the_answer_tool_is_built(
    fake_answer_provider: type, monkeypatch: pytest.MonkeyPatch
) -> None:
    from web_router.exceptions import NoAnswerProviderError

    _no_answer_keys(monkeypatch)

    with pytest.raises(NoAnswerProviderError):
        WebRouterTools(WebRouter("down"), methods=["search", "answer"], provider="down")

    assert WebRouterTools(WebRouter("down"), methods=["search"]).names == ("web_router_search",), "asked for only"


def test_an_answer_backend_failure_is_an_error_result_the_model_reads(
    fake_answer_provider: type, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fake_answer_provider, "raises", ProviderError("fake", "HTTP 503: unavailable"))

    result = WebRouterTools(WebRouter(), methods=["answer"], provider="fake").call("web_router_answer", {"query": "q"})

    assert result.is_error and result.content.startswith("web_router_answer failed:")


def test_an_empty_question_is_an_error_result_and_bills_nothing(fake_answer_provider: type) -> None:
    router = WebRouter()

    result = WebRouterTools(router, methods=["answer"], provider="fake").call("web_router_answer", {"query": " "})

    assert result.is_error and "web_router_answer did not run" in result.content
    assert router.get_answer_provider("fake").requests == []


async def test_an_answer_acall_runs_on_the_async_router(fake_answer_provider: type) -> None:
    async with AsyncWebRouter() as router:
        result = await WebRouterTools(router, methods=["answer"], provider="fake").acall(
            "web_router_answer", {"query": "q"}
        )

    assert json.loads(result.content)["answer"] == "the answer"


def test_the_guide_brings_in_answer_last_and_only_when_it_is_exposed() -> None:
    assert "web_router_answer" not in routing_guide()

    guide = routing_guide(["search", "extract", "answer"])

    assert guide.index("web_router_extract") < guide.index("web_router_answer")
    assert "billed" in guide


def test_an_answer_rendering_cuts_each_snippet_and_says_when_the_backend_stopped_early() -> None:
    from web_router.integrations._render import render_answer
    from web_router.types import Citation
    from web_router.types_answer import AnswerResponse

    response = AnswerResponse(
        provider="fake",
        query="q",
        answer="a long answer " * 50,
        citations=[
            Citation(url="https://example.com", snippet="x" * 100),
            Citation(url="https://b.example", field="[2]"),
        ],
        outcome="partial",
    )

    rendered = json.loads(render_answer(response, result_chars=10))

    assert rendered["answer"] == response.answer, "the backend's answer is never cut"
    assert rendered["citations"][0]["snippet"] == "x" * 10 + " ... [truncated]"
    assert rendered["outcome"] == "partial"
    assert "field" not in rendered["citations"][0], "a citation with no marker renders as before"
    assert rendered["citations"][1]["field"] == "[2]", "the model can tell which source backs [2]"
