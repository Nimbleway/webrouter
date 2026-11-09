"""Search's shared options beyond the original six: ``recency`` and ``effort``, on every surface that lists them."""

from __future__ import annotations

from typing import Any, get_args

import pytest

from web_router import AsyncWebRouter, WebRouter
from web_router.constants import MCP_DESC_SEARCH, MCP_SEARCH_PROPERTIES
from web_router.integrations.anthropic import WebRouterTools
from web_router.types import NORMALIZED_PARAMETERS, Recency, ResearchEffort, SearchRequest
from web_router.types_answer import AnswerRequest


def test_recency_and_effort_are_shared_search_options() -> None:
    assert {"recency", "effort"} <= NORMALIZED_PARAMETERS
    request = SearchRequest(query="q", recency="week", effort="low")
    assert request.specified_parameters() == {"recency", "effort"}


def test_recency_means_the_same_on_search_and_answer() -> None:
    search = SearchRequest.model_fields["recency"]
    answer = AnswerRequest.model_fields["recency"]
    assert search.annotation == answer.annotation
    assert search.description == answer.description
    assert "dates" in (search.description or "")


def test_both_routers_send_recency_and_effort_as_given(fake_provider: Any) -> None:
    router = WebRouter("fake")
    router.search("q", recency="month", effort="max")

    request = router.get_provider().requests[0]
    assert (request.recency, request.effort) == ("month", "max")


async def test_the_async_router_sends_them_too(fake_provider: Any) -> None:
    async with AsyncWebRouter("fake") as router:
        await router.search("q", recency="day", effort="medium")
        request = router.get_provider().requests[0]
    assert (request.recency, request.effort) == ("day", "medium")


def test_the_mcp_schema_offers_every_level_and_says_what_effort_costs() -> None:
    assert MCP_SEARCH_PROPERTIES["recency"]["enum"] == list(get_args(Recency))
    assert MCP_SEARCH_PROPERTIES["effort"]["enum"] == list(get_args(ResearchEffort))
    for text in (MCP_SEARCH_PROPERTIES["effort"]["description"], MCP_DESC_SEARCH):
        assert "costs more" in text
        assert "unset unless needed" in text


def test_the_mcp_schema_lists_every_shared_option() -> None:
    assert set(MCP_SEARCH_PROPERTIES) == NORMALIZED_PARAMETERS | {"query", "preset"}


@pytest.mark.parametrize("name, value", [("recency", "year"), ("effort", "high")])
def test_the_model_tools_send_the_callers_value_on_every_call(fake_provider: Any, name: str, value: str) -> None:
    router = WebRouter("fake")
    tools = WebRouterTools(router, provider="fake", methods=["search"], **{name: value})

    tools.call("web_router_search", {"query": "q"})

    assert getattr(router.get_provider().requests[0], name) == value


def test_the_langchain_tool_sends_them_too(fake_provider: Any) -> None:
    pytest.importorskip("langchain_core")
    from web_router.integrations.langchain import WebRouterSearch

    router = WebRouter("fake")
    WebRouterSearch(router=router, provider="fake", recency="week", effort="low").invoke("q")

    request = router.get_provider().requests[0]
    assert (request.recency, request.effort) == ("week", "low")


def test_search_depth_is_described_by_size_in_one_place() -> None:
    """The request model and the MCP schema say the same thing, so a model and a caller read the same sizes."""
    described = SearchRequest.model_fields["search_depth"].description or ""
    assert MCP_SEARCH_PROPERTIES["search_depth"]["description"] == described
