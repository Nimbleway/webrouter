"""The OpenAI tools: what the model is given, what it reads back, and who hears about a failure."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from typing import Any

import pytest

from tests.conftest import FakeExtractProvider, FakeProvider
from web_router import AsyncWebRouter, WebRouter
from web_router.exceptions import ConfigurationError, ProviderError, UnsupportedParameterError
from web_router.integrations import anthropic
from web_router.integrations.openai import WebRouterTools, routing_guide
from web_router.registry import (
    register_extract_provider,
    register_provider,
    unregister_extract_provider,
    unregister_provider,
)
from web_router.types import SearchRequest, SearchResponse, SearchResult

STRICT_KEYWORDS = {
    "type",
    "properties",
    "required",
    "additionalProperties",
    "description",
    "items",
    "minLength",
    "minItems",
    "maxItems",
}
"""The JSON Schema keywords strict mode was seen to accept on both APIs; it answers a 400 for ones it does not."""

LIVE_MODEL = "gpt-4.1-mini"

CODE_WORD = "QUILLWORT"


class DownProvider(FakeProvider):
    name = "down"

    def _respond(self, request: SearchRequest) -> SearchResponse:
        raise ProviderError(self.name, "HTTP 503: unavailable")


class NarrowProvider(FakeProvider):
    name = "narrow"
    supported_parameters = frozenset({"max_results"})


class CodeWordProvider(FakeProvider):
    """A search whose one result holds a word the model can only know by calling the tool."""

    name = "codeword"

    def _respond(self, request: SearchRequest) -> SearchResponse:
        self.requests.append(request)
        result = SearchResult(
            url="https://example.com/code", title="Code word", content=f"The code word is {CODE_WORD}."
        )
        return SearchResponse(provider=self.name, query=request.query, results=[result])


@pytest.fixture(autouse=True)
def providers() -> Iterator[None]:
    for provider in (FakeProvider, DownProvider, NarrowProvider, CodeWordProvider):
        register_provider(provider.name, provider)
    register_extract_provider(FakeExtractProvider.name, FakeExtractProvider)
    yield
    for provider in (FakeProvider, DownProvider, NarrowProvider, CodeWordProvider):
        unregister_provider(provider.name)
    unregister_extract_provider(FakeExtractProvider.name)


def _keywords(schema: Any) -> set[str]:
    if not isinstance(schema, dict):
        return set()
    found = set(schema)
    for key, value in schema.items():
        if key == "properties":
            found |= set().union(*(_keywords(sub) for sub in value.values()))
        elif isinstance(value, dict):
            found |= _keywords(value)
    return found


def test_the_model_gets_strict_function_tools_whose_only_input_is_the_query_or_the_urls() -> None:
    tools = WebRouterTools(WebRouter(), provider="fake")
    responses = tools.definitions()
    chat = [d["function"] for d in tools.chat_definitions()]

    for definitions in (responses, chat):
        assert [d["name"] for d in definitions] == ["web_router_search", "web_router_extract"]
        assert [d["parameters"]["required"] for d in definitions] == [["query"], ["urls"]]
        assert all(d["parameters"]["additionalProperties"] is False and d["strict"] is True for d in definitions)
    assert all(d["type"] == "function" for d in [*responses, *tools.chat_definitions()])
    json.dumps([responses, tools.chat_definitions()])  # sent as-is in a request body


def test_the_model_is_shown_the_same_tools_a_claude_agent_is() -> None:
    claude = anthropic.WebRouterTools(WebRouter(), provider="fake").definitions()
    openai = WebRouterTools(WebRouter(), provider="fake").definitions()

    assert [(d["name"], d["description"], d["input_schema"]) for d in claude] == [
        (d["name"], d["description"], d["parameters"]) for d in openai
    ]
    assert routing_guide() == anthropic.routing_guide()


def test_the_schemas_use_only_keywords_strict_mode_accepts() -> None:
    for definition in WebRouterTools(WebRouter(), provider="fake").definitions():
        assert _keywords(definition["parameters"]) <= STRICT_KEYWORDS


@pytest.mark.parametrize("arguments", ['{"query": "who won"}', {"query": "who won"}])
def test_arguments_are_taken_as_the_json_string_openai_hands_over_or_already_decoded(arguments: Any) -> None:
    router = WebRouter()
    tools = WebRouterTools(router, provider="fake", max_results=3, search_depth="deep")

    result = tools.call("web_router_search", arguments)

    request = router.get_provider("fake").requests[-1]
    assert (request.query, request.max_results, request.search_depth) == ("who won", 3, "deep")
    assert not result.is_error
    assert json.loads(result.content)["results"][0]["url"] == "https://example.com"
    assert result.provider == "fake"


def test_a_result_goes_back_as_a_responses_item_or_a_chat_message() -> None:
    result = WebRouterTools(WebRouter(), provider="fake").call("web_router_search", '{"query": "q"}')

    assert result.function_call_output("call_1") == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": result.content,
    }
    assert result.tool_message("call_1") == {"role": "tool", "tool_call_id": "call_1", "content": result.content}


def test_a_backend_failure_is_an_error_result_whose_text_says_so() -> None:
    result = WebRouterTools(WebRouter(), provider="down").call("web_router_search", '{"query": "q"}')

    assert result.is_error and result.response is None
    output = result.function_call_output("call_1")["output"]
    assert output.startswith("web_router_search failed:") and "HTTP 503" in output


def test_a_setup_mistake_raises_to_the_caller() -> None:
    tools = WebRouterTools(WebRouter(), provider="narrow", country="US")

    with pytest.raises(UnsupportedParameterError):
        tools.call("web_router_search", '{"query": "q"}')


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("web_router_search", '{"query": "who'),
        ("web_router_search", "[]"),
        ("web_router_search", "{}"),
        ("web_router_search", '{"query": "  "}'),
        ("web_router_search", {"query": 5}),
        ("web_router_extract", '{"urls": [""]}'),  # what a strict model sent when asked for no URLs
        ("web_router_extract", {"urls": [f"https://{n}.example" for n in range(6)]}),
        ("web_router_extract", '{"urls": "https://a.example"}'),
    ],
)
def test_arguments_the_model_got_wrong_are_an_error_result_and_bill_nothing(name: str, arguments: Any) -> None:
    router = WebRouter()
    tools = WebRouterTools(router, provider="fake")

    result = tools.call(name, arguments)

    assert result.is_error
    assert result.content.startswith(f"{name} did not run:")
    assert router.get_provider("fake").requests == []
    assert router.get_extract_provider("fake").calls == []


def test_a_tool_name_this_instance_does_not_serve_raises() -> None:
    with pytest.raises(ValueError, match="web_router_extract"):
        WebRouterTools(WebRouter(), methods=["search"]).call("web_router_extract", "not json")


def test_methods_choose_which_tools_are_exposed_and_an_unknown_one_is_refused() -> None:
    tools = WebRouterTools(WebRouter(), methods=["search"])

    assert [d["name"] for d in tools.definitions()] == ["web_router_search"]
    assert [d["function"]["name"] for d in tools.chat_definitions()] == ["web_router_search"]
    assert tools.guide() == routing_guide(["search"])
    with pytest.raises(ValueError, match="research"):
        WebRouterTools(WebRouter(), methods=["search", "research"])


def test_extract_goes_to_the_search_provider_when_it_also_extracts() -> None:
    assert WebRouterTools(WebRouter(), provider="FAKE").extract_provider == "fake"
    assert WebRouterTools(WebRouter(), provider="down").extract_provider is None


def test_a_router_whose_default_cannot_extract_raises_when_the_tools_are_built() -> None:
    with pytest.raises(ConfigurationError, match="extract_provider"):
        WebRouterTools(WebRouter("down"), provider="down")

    assert WebRouterTools(WebRouter("down"), provider="down", extract_provider="fake").extract_provider == "fake"
    assert WebRouterTools(WebRouter("down"), methods=["search"]).names == ("web_router_search",)


async def test_acall_runs_on_the_async_router() -> None:
    async with AsyncWebRouter() as router:
        tools = WebRouterTools(router, provider="fake")
        result = await tools.acall("web_router_extract", '{"urls": ["https://a.example"]}')

        assert json.loads(result.content)["documents"][0]["url"] == "https://a.example"
        assert router.get_extract_provider("fake").calls[-1] == ["https://a.example"]


async def test_acall_on_a_sync_router_runs_it_on_a_worker_thread() -> None:
    result = await WebRouterTools(WebRouter(), provider="fake").acall("web_router_search", {"query": "q"})

    assert result.provider == "fake"


def test_the_module_needs_no_openai_sdk() -> None:
    code = "import sys, web_router.integrations.openai; assert 'openai' not in sys.modules"

    subprocess.run([sys.executable, "-c", code], check=True)


def test_the_model_reads_the_same_rendering_a_claude_agent_does() -> None:
    router = WebRouter()

    claude = anthropic.WebRouterTools(router, provider="fake").call("web_router_search", {"query": "q"})
    openai = WebRouterTools(router, provider="fake").call("web_router_search", '{"query": "q"}')

    assert openai.content == claude.content


_LIVE_PROMPT = "Use web_router_search to find the code word, then reply with the code word alone."

_live = pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set")


@pytest.mark.integration
@_live
def test_live_the_responses_api_accepts_the_tools_and_reads_the_result() -> None:
    openai = pytest.importorskip("openai")
    client = openai.OpenAI()
    tools = WebRouterTools(WebRouter(), provider="codeword")
    request = {"model": LIVE_MODEL, "instructions": tools.guide(), "tools": tools.definitions()}

    response = client.responses.create(
        input=_LIVE_PROMPT, tool_choice={"type": "function", "name": "web_router_search"}, **request
    )
    calls = [item for item in response.output if item.type == "function_call"]
    outputs = [tools.call(call.name, call.arguments).function_call_output(call.call_id) for call in calls]
    answer = client.responses.create(input=outputs, previous_response_id=response.id, tool_choice="none", **request)

    assert calls and all(call.name == "web_router_search" for call in calls)
    assert CODE_WORD in answer.output_text.upper()


@pytest.mark.integration
@_live
def test_live_chat_completions_accepts_the_tools_and_reads_the_result() -> None:
    openai = pytest.importorskip("openai")
    client = openai.OpenAI()
    tools = WebRouterTools(WebRouter(), provider="codeword")
    messages: list[Any] = [{"role": "system", "content": tools.guide()}, {"role": "user", "content": _LIVE_PROMPT}]

    first = client.chat.completions.create(
        model=LIVE_MODEL,
        messages=messages,
        tools=tools.chat_definitions(),
        tool_choice={"type": "function", "function": {"name": "web_router_search"}},
    )
    message = first.choices[0].message
    messages.append(message)
    for call in message.tool_calls or []:
        messages.append(tools.call(call.function.name, call.function.arguments).tool_message(call.id))
    answer = client.chat.completions.create(
        model=LIVE_MODEL, messages=messages, tools=tools.chat_definitions(), tool_choice="none"
    )

    assert message.tool_calls
    assert CODE_WORD in (answer.choices[0].message.content or "").upper()
