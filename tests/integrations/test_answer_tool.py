"""The Claude and OpenAI answer tool: opt-in, what the model reads, and who hears about a failure."""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest

from tests.conftest import FakeExtractProvider, FakeProvider
from tests.integrations.test_openai import STRICT_KEYWORDS, _keywords
from web_router import AsyncWebRouter, WebRouter
from web_router.constants import ANSWER_ENV_BY_PROVIDER
from web_router.exceptions import NoAnswerProviderError, ProviderError
from web_router.integrations import openai
from web_router.integrations._render import render_answer
from web_router.integrations.anthropic import WebRouterTools, routing_guide
from web_router.registry import (
    register_extract_provider,
    register_provider,
    unregister_extract_provider,
    unregister_provider,
)
from web_router.types import Citation, SearchRequest, SearchResponse
from web_router.types_answer import AnswerResponse


class DownProvider(FakeProvider):
    name = "down"

    def _respond(self, request: SearchRequest) -> SearchResponse:
        raise ProviderError(self.name, "HTTP 503: unavailable")


@pytest.fixture(autouse=True)
def providers(fake_answer_provider: type) -> Iterator[None]:
    for provider in (FakeProvider, DownProvider):
        register_provider(provider.name, provider)
    register_extract_provider(FakeExtractProvider.name, FakeExtractProvider)
    yield
    for provider in (FakeProvider, DownProvider):
        unregister_provider(provider.name)
    unregister_extract_provider(FakeExtractProvider.name)


def _no_answer_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """No answer backend configured, whatever .env holds."""
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


def test_the_default_answer_backend_is_chosen_once_when_the_tools_are_built(
    fake_answer_provider: type, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_answer_keys(monkeypatch)
    monkeypatch.setitem(ANSWER_ENV_BY_PROVIDER, "fake", ("WEB_ROUTER_TEST_FAKE_KEY",))
    monkeypatch.setenv("WEB_ROUTER_TEST_FAKE_KEY", "k")
    tools = WebRouterTools(WebRouter("down"), methods=["answer"])

    monkeypatch.setitem(ANSWER_ENV_BY_PROVIDER, "other", ("WEB_ROUTER_TEST_OTHER_KEY",))
    monkeypatch.setenv("WEB_ROUTER_TEST_OTHER_KEY", "k")

    assert tools.answer_provider == "fake"
    assert not tools.call("web_router_answer", {"query": "q"}).is_error, "a key added later changes nothing"


def test_a_router_with_no_answer_backend_raises_when_the_answer_tool_is_built(
    fake_answer_provider: type, monkeypatch: pytest.MonkeyPatch
) -> None:
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


def test_the_openai_answer_tool_is_strict_on_both_apis() -> None:
    tools = openai.WebRouterTools(WebRouter(), methods=["answer"], provider="fake")
    chat = [d["function"] for d in tools.chat_definitions()]

    for definitions in (tools.definitions(), chat):
        assert [d["name"] for d in definitions] == ["web_router_answer"]
        assert all(d["strict"] is True and d["parameters"]["additionalProperties"] is False for d in definitions)
        assert all(_keywords(d["parameters"]) <= STRICT_KEYWORDS for d in definitions)
