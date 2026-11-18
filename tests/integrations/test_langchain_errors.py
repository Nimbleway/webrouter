"""The LangChain tools fail in the Claude and OpenAI tools' words, and refuse the URLs they refuse."""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest

pytest.importorskip("langchain_core")

from tests.conftest import FakeExtractProvider
from tests.integrations.test_tool_errors import DeadExtractProvider, NoisyProvider
from web_router import WebRouter
from web_router.integrations.anthropic import WebRouterTools
from web_router.integrations.langchain import WebRouterExtract, WebRouterSearch
from web_router.registry import (
    register_extract_provider,
    register_provider,
    unregister_extract_provider,
    unregister_provider,
)

EXTRACTORS = (FakeExtractProvider, DeadExtractProvider)


@pytest.fixture(autouse=True)
def providers() -> Iterator[None]:
    register_provider(NoisyProvider.name, NoisyProvider)
    for extractor in EXTRACTORS:
        register_extract_provider(extractor.name, extractor)
    yield
    unregister_provider(NoisyProvider.name)
    for extractor in EXTRACTORS:
        unregister_extract_provider(extractor.name)


def _content(tool: WebRouterSearch | WebRouterExtract, **args: object) -> tuple[str, str]:
    message = tool.invoke({"type": "tool_call", "id": "c", "name": tool.name, "args": args})
    return message.status, message.content


def test_a_backend_failure_reads_as_the_other_tools_failure_under_its_own_name() -> None:
    status, content = _content(WebRouterSearch(router=WebRouter(), provider="noisy"), query="q")
    other = WebRouterTools(WebRouter(), provider="noisy").call("web_router_search", {"query": "q"}).content

    assert status == "error"
    assert content == other.replace("web_router_search", "web_search")


def test_an_extract_that_read_nothing_lists_each_url_as_a_partial_one_does() -> None:
    tool = WebRouterExtract(router=WebRouter(), provider="dead")
    status, content = _content(tool, urls=["https://a.example", "http://localhost/"])

    shown = json.loads(content)
    assert status == "error" and shown["error"].startswith("web_extract failed: dead: ")
    assert [row["kind"] for row in shown["failures"]] == ["target", "refused"]


def test_a_private_url_is_refused_by_default_and_never_sent() -> None:
    router = WebRouter()
    status, content = _content(WebRouterExtract(router=router, provider="fake"), urls=["http://169.254.169.254/"])

    assert status == "error"
    assert json.loads(content)["error"] == "web_extract did not run: every URL was refused; see failures."
    assert router.get_extract_provider("fake").calls == []


def test_url_policy_none_sends_it() -> None:
    router = WebRouter()
    status, _ = _content(WebRouterExtract(router=router, provider="fake", url_policy=None), urls=["http://10.0.0.1/"])

    assert status == "success" and router.get_extract_provider("fake").calls == [["http://10.0.0.1/"]]


@pytest.mark.parametrize(
    ("args", "says"),
    [
        ({"query": "q", "max_results": 50}, "web_search did not run: `max_results`: extra inputs are not permitted."),
        ({}, "web_search did not run: `query`: field required."),
    ],
)
def test_input_that_breaks_the_schema_is_an_error_message_naming_it_and_nothing_is_sent(
    args: dict[str, object], says: str
) -> None:
    status, content = _content(WebRouterSearch(router=WebRouter(), provider="noisy"), **args)

    assert (status, content) == ("error", says)


def test_a_failure_is_cut_to_the_tools_result_chars() -> None:
    _, content = _content(WebRouterSearch(router=WebRouter(), provider="noisy", result_chars=30), query="q")

    assert content.endswith("[truncated]") and len(content) < 80
