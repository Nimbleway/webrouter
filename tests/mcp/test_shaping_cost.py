"""What a call cost and how long it took reach the MCP client: ``elapsed_s``, ``cost_usd`` and ``cost_source``."""

from __future__ import annotations

from typing import Any

import jsonschema
import pytest

from web_router.constants import MCP_TOOL_ANSWER, MCP_TOOL_SEARCH
from web_router.constants_mcp_context import MCP_OUTPUT_SCHEMAS
from web_router.mcp.server import call_tool
from web_router.mcp.shaping import shape_browse, shape_extract, shape_research, shape_search
from web_router.mcp.shaping_answer import shape_answer
from web_router.mcp.tools import WebRouterTools
from web_router.types import BrowseResponse, ExtractResponse, ResearchResponse, SearchResponse
from web_router.types_answer import AnswerResponse

_COST = 0.0123456789


@pytest.mark.parametrize(("name", "arguments"), [(MCP_TOOL_SEARCH, {"query": "q"}), (MCP_TOOL_ANSWER, {"query": "q"})])
async def test_a_tool_result_says_what_the_call_cost_and_how_long_it_took(
    tools: WebRouterTools, monkeypatch: pytest.MonkeyPatch, name: str, arguments: dict[str, Any]
) -> None:
    monkeypatch.setattr("web_router.routing.metered.estimated_cost", lambda *args: _COST)

    result = await call_tool(tools, name, arguments)

    assert not result.is_error, result.content[0].text
    payload = result.structured_content
    assert payload is not None
    assert payload["cost_usd"] == round(_COST, 6)
    assert payload["cost_source"] == "estimated"
    assert isinstance(payload["elapsed_s"], float)
    assert payload["elapsed_s"] == round(payload["elapsed_s"], 2)
    # The published schemas leave these out to keep the tool list small; extra keys still validate.
    jsonschema.validate(payload, MCP_OUTPUT_SCHEMAS[name])


@pytest.mark.parametrize(
    "response",
    [
        SearchResponse(provider="p", query="q", results=[], elapsed_s=1.23456, cost_usd=0.5, cost_source="reported"),
        ExtractResponse(provider="p", documents=[], elapsed_s=1.23456, cost_usd=0.5, cost_source="reported"),
        AnswerResponse(provider="p", query="q", answer="a", elapsed_s=1.23456, cost_usd=0.5, cost_source="reported"),
        ResearchResponse(provider="p", query="q", answer="a", elapsed_s=1.23456, cost_usd=0.5, cost_source="reported"),
        BrowseResponse(provider="p", task="t", answer="a", elapsed_s=1.23456, cost_usd=0.5, cost_source="reported"),
    ],
)
def test_each_shaper_keeps_the_cost_fields_rounded(response: Any) -> None:
    shaper = {
        SearchResponse: shape_search,
        ExtractResponse: shape_extract,
        AnswerResponse: shape_answer,
        ResearchResponse: shape_research,
        BrowseResponse: shape_browse,
    }[type(response)]

    shaped = shaper(response, "h-0001", budget=4000)

    assert (shaped["elapsed_s"], shaped["cost_usd"], shaped["cost_source"]) == (1.23, 0.5, "reported")


def test_an_unknown_cost_costs_the_result_nothing() -> None:
    shaped = shape_search(SearchResponse(provider="p", query="q", results=[]), "h-0001", budget=4000)

    assert not {"elapsed_s", "cost_usd", "cost_source"} & set(shaped)
