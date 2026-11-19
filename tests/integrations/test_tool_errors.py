"""What the Claude and OpenAI tools hand a model when its call does not run or fails, and the URLs they refuse."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest

from tests.conftest import FakeExtractProvider, FakeProvider
from web_router import AsyncWebRouter, WebRouter
from web_router.exceptions import ConfigurationError, DeadlineExceededError, ProviderError
from web_router.exceptions_typed import InvalidParameterError
from web_router.integrations._messages import INTEGRATION_TIMEOUT
from web_router.integrations.anthropic import WebRouterTools
from web_router.registry import (
    register_extract_provider,
    register_provider,
    unregister_extract_provider,
    unregister_provider,
)
from web_router.types import ExtractRequest, ExtractResponse, SearchRequest, SearchResponse

VENDOR_NOISE = (
    "Could not resolve host: nope.invalid. " + "Sign up for a free key at https://vendor.example/signup. " * 40
)


class NoisyProvider(FakeProvider):
    name = "noisy"

    def _respond(self, request: SearchRequest) -> SearchResponse:
        raise ProviderError(self.name, VENDOR_NOISE)


class SlowProvider(FakeProvider):
    name = "slow"

    def _respond(self, request: SearchRequest) -> SearchResponse:
        raise DeadlineExceededError(self.name, 1.0)


class DeadExtractProvider(FakeExtractProvider):
    name = "dead"

    def _respond(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
        self.calls.append(list(urls))
        raise ProviderError(self.name, VENDOR_NOISE)


PROVIDERS = (FakeProvider, NoisyProvider, SlowProvider)


@pytest.fixture(autouse=True)
def providers() -> Iterator[None]:
    for provider in PROVIDERS:
        register_provider(provider.name, provider)
    for extractor in (FakeExtractProvider, DeadExtractProvider):
        register_extract_provider(extractor.name, extractor)
    yield
    for provider in PROVIDERS:
        unregister_provider(provider.name)
    for extractor in (FakeExtractProvider, DeadExtractProvider):
        unregister_extract_provider(extractor.name)


@pytest.mark.parametrize(
    ("name", "tool_input", "says"),
    [
        ("web_search", {"query": "q"}, "web_search is the LangChain tool's name, this one is web_router_search"),
        ("web_router_research", {"query": "q"}, "call one of web_router_search, web_router_extract"),
        ("web_router_search", None, "must be a JSON object, got null"),
        ("web_router_search", [1], "must be a JSON object, got an array"),
        ("web_router_search", "", "not valid JSON"),
        ("web_router_search", {}, "it needs `query`, which was missing"),
        ("web_router_search", {"query": "  "}, "got a blank string"),
        ("web_router_search", {"query": "q", "max_results": 50}, "it takes only `query`; leave out `max_results`"),
        ("web_router_extract", {"urls": [1]}, "got a number among them"),
    ],
)
def test_each_input_mistake_is_an_error_result_naming_it_and_nothing_is_sent(
    name: str, tool_input: Any, says: str
) -> None:
    router = WebRouter()
    result = WebRouterTools(router, provider="fake").call(name, tool_input)

    assert result.is_error and result.error is None
    assert result.content.startswith(f"{name} did not run: ") and says in result.content
    assert router.get_provider("fake").requests == []


def test_a_failure_reaches_the_model_from_its_fields_and_the_vendor_text_stays_with_the_caller() -> None:
    result = WebRouterTools(WebRouter(), provider="noisy").call("web_router_search", {"query": "q"})

    assert result.is_error
    assert result.content == (
        "web_router_search failed: noisy: the page's address is the problem, so another backend would fail "
        "the same way (kind=target, retryable=false)."
    )
    assert isinstance(result.error, ProviderError) and "signup" in str(result.error)


def test_a_failure_is_cut_to_result_chars() -> None:
    result = WebRouterTools(WebRouter(), provider="noisy", result_chars=40).call("web_router_search", {"query": "q"})

    assert result.content.endswith("[truncated]") and len(result.content) < 100


def test_a_call_out_of_time_is_a_retryable_timeout_error_result() -> None:
    result = WebRouterTools(WebRouter(), provider="slow").call("web_router_search", {"query": "q"})

    assert result.is_error and "(kind=timeout, retryable=true)" in result.content


def test_every_call_carries_the_default_timeout_and_none_lifts_it(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Any] = []
    router = WebRouter()
    searched = SearchResponse(provider="fake", query="q", results=[])
    monkeypatch.setattr(router, "search", lambda query, **o: seen.append(o["timeout"]) or searched)
    monkeypatch.setattr(router, "extract", lambda urls, **o: seen.append(o["timeout"]) or ExtractResponse(provider="f"))

    for tools in (WebRouterTools(router, provider="fake"), WebRouterTools(router, provider="fake", timeout=None)):
        tools.call("web_router_search", {"query": "q"})
        tools.call("web_router_extract", {"urls": ["https://a.example"]})

    assert seen == [INTEGRATION_TIMEOUT, INTEGRATION_TIMEOUT, None, None]


def test_an_extract_that_read_nothing_has_the_shape_a_partial_one_has() -> None:
    result = WebRouterTools(WebRouter(), extract_provider="dead").call(
        "web_router_extract", {"urls": ["https://a.example", "http://localhost:8080/"]}
    )

    shown = json.loads(result.content)
    assert result.is_error and shown["error"].startswith("web_router_extract failed: dead: ")
    assert [(row["url"], row["kind"]) for row in shown["failures"]] == [
        ("https://a.example", "target"),
        ("http://localhost:8080/", "refused"),
    ]
    assert "signup" not in result.content


@pytest.mark.parametrize(
    "url",
    ["http://169.254.169.254/latest/meta-data", "http://localhost:8080", "http://10.0.0.1/", "file:///etc/passwd"],
)
def test_a_private_or_metadata_url_is_refused_by_default_and_never_sent(url: str) -> None:
    router = WebRouter()
    result = WebRouterTools(router, provider="fake").call("web_router_extract", {"urls": [url]})

    shown = json.loads(result.content)
    assert result.is_error and "did not run" in shown["error"]
    assert shown["failures"][0] | {"url": url, "kind": "refused", "retryable": False} == shown["failures"][0]
    assert router.get_extract_provider("fake").calls == []


def test_a_refused_url_does_not_sink_the_public_ones_beside_it() -> None:
    router = WebRouter()
    urls = ["https://a.example", "http://127.0.0.1/admin"]
    result = WebRouterTools(router, provider="fake").call("web_router_extract", {"urls": urls})

    shown = json.loads(result.content)
    assert not result.is_error
    assert [d["url"] for d in shown["documents"]] == ["https://a.example"]
    assert [row["url"] for row in shown["failures"]] == ["http://127.0.0.1/admin"]
    assert router.get_extract_provider("fake").calls == [["https://a.example"]]


def test_url_policy_none_sends_a_private_url_and_an_unknown_policy_raises() -> None:
    router = WebRouter()
    result = WebRouterTools(router, provider="fake", url_policy=None).call(
        "web_router_extract", {"urls": ["http://10.0.0.1/"]}
    )

    assert not result.is_error and router.get_extract_provider("fake").calls == [["http://10.0.0.1/"]]
    with pytest.raises(ConfigurationError):
        WebRouterTools(router, provider="fake", url_policy="everything")  # type: ignore[arg-type]


async def test_the_async_executor_gives_the_model_the_same_text() -> None:
    sync = WebRouterTools(WebRouter(), provider="noisy").call("web_router_search", {"query": "q"})
    async with AsyncWebRouter() as router:
        result = await WebRouterTools(router, provider="noisy").acall("web_router_search", {"query": "q"})

    assert result.is_error and result.content == sync.content


@pytest.mark.parametrize("timeout", [float("inf"), float("nan"), -1.0])
def test_a_timeout_the_router_would_refuse_is_refused_when_the_tools_are_built(timeout: float) -> None:
    with pytest.raises(InvalidParameterError, match="timeout"):
        WebRouterTools(router=WebRouter(), timeout=timeout)
