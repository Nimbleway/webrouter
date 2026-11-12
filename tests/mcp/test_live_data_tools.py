"""The three live data tools: list a vendor's sources, describe one, run one."""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.mcp.conftest import FAKE
from web_router.constants import ENV_NIMBLE_API_KEY, MCP_RESOURCE_URI, MCP_TOOL_FETCH_RAW
from web_router.constants_live_data import (
    MCP_METHOD_LIVE_DATA,
    MCP_TOOL_LIVE_DATA,
    MCP_TOOL_LIVE_DATA_SOURCE,
    MCP_TOOL_LIVE_DATA_SOURCES,
)
from web_router.mcp.server import call_tool, list_tools, read_resource
from web_router.mcp.tools import WebRouterTools


def _tool(session: WebRouterTools, name: str) -> Any:
    return next(tool for tool in list_tools(session).tools if tool.name == name)


def _payload(result: Any) -> dict[str, Any]:
    assert not result.is_error, result.content[0].text
    parsed: dict[str, Any] = json.loads(result.content[0].text)
    return parsed


async def test_live_data_sources_searches_by_query_and_says_how(tools: WebRouterTools) -> None:
    found = _payload(await call_tool(tools, MCP_TOOL_LIVE_DATA_SOURCES, {"query": "jobs", "provider": FAKE}))

    assert [row["name"] for row in found["sources"]] == ["jobs_listing"]
    assert found["query"] == "jobs" and found["search"] == "local"
    assert "query" in _tool(tools, MCP_TOOL_LIVE_DATA_SOURCES).input_schema["properties"]
    # The handle holds the listing the vendor answered, so the search is traceable to it.
    raw = _payload(await call_tool(tools, MCP_TOOL_FETCH_RAW, {"handle": found["handle"]}))
    assert raw["raw"] == [{"items": ["books_pdp", "jobs_listing"]}]


async def test_live_data_sources_lists_the_catalogue_and_live_data_source_describes_one(tools: WebRouterTools) -> None:
    listed = _payload(await call_tool(tools, MCP_TOOL_LIVE_DATA_SOURCES, {"limit": 10, "provider": FAKE}))

    assert listed["provider"] == FAKE
    assert [row["name"] for row in listed["sources"]] == ["books_pdp", "jobs_listing"]
    assert listed["total"] == 2 and listed["limit"] == 10
    # A listing is the few words a source is picked by, plus what a run must pass; the schemas are the describe tool's.
    assert "input_schema" not in listed["sources"][0]
    assert listed["sources"][0]["required_inputs"] == ["url"]
    assert listed["sources"][0]["sample_inputs"] == {"url": "https://example.com/p/1"}
    assert listed["handle"].startswith(MCP_TOOL_LIVE_DATA_SOURCES)

    described = _payload(await call_tool(tools, MCP_TOOL_LIVE_DATA_SOURCE, {"name": "books_pdp", "provider": FAKE}))

    assert described["source"]["name"] == "books_pdp"
    assert described["source"]["input_schema"]["required"] == ["url"]
    assert described["source"]["output_schema"]["properties"]["title"] == {"type": "string"}
    assert described["source"]["samples"] == [{"input": {"url": "https://example.com/p/1"}, "output": {"title": "A"}}]
    assert tools.router.get_live_data_provider(FAKE).listed == [(10, None)]  # type: ignore[attr-defined]


async def test_live_data_passes_the_inputs_through_and_returns_the_records(tools: WebRouterTools) -> None:
    inputs = {"url": "https://example.com/page"}
    shaped = _payload(
        await call_tool(tools, MCP_TOOL_LIVE_DATA, {"source": "books_pdp", "inputs": inputs, "provider": FAKE})
    )

    assert shaped["provider"] == FAKE
    assert shaped["source"] == "books_pdp"
    assert shaped["records"] == [{"title": "Example", "inputs": inputs}]
    assert shaped["record_count"] == 1
    assert shaped["document"] is None, "no format asked for means no page beside the record"
    assert "raw" not in shaped and "inputs" not in shaped

    request = tools.router.get_live_data_provider(FAKE).requests[-1]  # type: ignore[attr-defined]
    assert request.inputs == inputs and request.format is None

    raw = _payload(await call_tool(tools, MCP_TOOL_FETCH_RAW, {"handle": shaped["handle"]}))
    assert raw["raw"] == {"source": "books_pdp", "inputs": inputs}


async def test_live_data_with_a_format_returns_the_page_beside_the_records(tools: WebRouterTools) -> None:
    shaped = _payload(
        await call_tool(
            tools,
            MCP_TOOL_LIVE_DATA,
            {"source": "books_pdp", "inputs": {}, "format": "markdown", "provider": FAKE},
        )
    )

    assert shaped["document"]["format"] == "markdown"
    assert shaped["document"]["content"] == "# Page"


async def test_live_data_without_the_source_names_it(tools: WebRouterTools) -> None:
    result = await call_tool(tools, MCP_TOOL_LIVE_DATA, {"inputs": {"url": "https://example.com"}, "provider": FAKE})

    assert result.is_error
    assert "'source'" in result.content[0].text


async def test_the_live_data_tools_take_the_provider_from_each_listed_row(tools: WebRouterTools, no_keys: None) -> None:
    """Unnamed, the listing asks every backend; each row names its vendor, which a run passes back."""
    listing = _payload(await call_tool(tools, MCP_TOOL_LIVE_DATA_SOURCES, {"query": "books"}))

    assert listing["provider"] == "auto"
    row = listing["sources"][0]
    assert row["provider"] == FAKE and row["name"] == "books_pdp"

    inputs = {"url": "https://example.com/p/1"}
    shaped = _payload(
        await call_tool(
            tools, MCP_TOOL_LIVE_DATA, {"source": row["name"], "inputs": inputs, "provider": row["provider"]}
        )
    )
    assert shaped["provider"] == FAKE and shaped["records"] == [{"title": "Example", "inputs": inputs}]

    # With one backend configured, a bare name can only be that backend's.
    described = _payload(await call_tool(tools, MCP_TOOL_LIVE_DATA_SOURCE, {"name": "books_pdp"}))
    assert described["provider"] == FAKE

    for name in (MCP_TOOL_LIVE_DATA_SOURCES, MCP_TOOL_LIVE_DATA_SOURCE, MCP_TOOL_LIVE_DATA):
        assert "provider" not in _tool(tools, name).input_schema["required"]


def test_the_live_data_tools_say_so_when_nothing_can_serve_them(
    tools: WebRouterTools, no_keys: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A live data run fetches on the backend's account, so with no key the
    # three tools are dead and say so, naming the variable that would fix it.
    for name in (MCP_TOOL_LIVE_DATA_SOURCES, MCP_TOOL_LIVE_DATA_SOURCE, MCP_TOOL_LIVE_DATA):
        assert "NOTHING IS CONFIGURED" in _tool(tools, name).description
        assert ENV_NIMBLE_API_KEY in _tool(tools, name).description
        schema = _tool(tools, name).input_schema
        assert "provider" not in schema["properties"] and "provider" not in schema["required"]

    monkeypatch.setenv(ENV_NIMBLE_API_KEY, "x")
    for name in (MCP_TOOL_LIVE_DATA_SOURCES, MCP_TOOL_LIVE_DATA_SOURCE, MCP_TOOL_LIVE_DATA):
        assert "NOTHING IS CONFIGURED" not in _tool(tools, name).description
        assert _tool(tools, name).input_schema["properties"]["provider"]["enum"] == ["nimble"]


def test_the_capabilities_key_the_live_data_tools_method_once(
    tools: WebRouterTools, no_keys: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = json.loads(read_resource(tools, MCP_RESOURCE_URI).contents[0].text)

    assert body[MCP_METHOD_LIVE_DATA] == [], "no keyless tier and no auto"
    assert MCP_TOOL_LIVE_DATA_SOURCES not in body and MCP_TOOL_LIVE_DATA_SOURCE not in body
    assert body["unlock"][MCP_METHOD_LIVE_DATA] == {
        "apify": ["APIFY_TOKEN"],
        "brightdata": ["BRIGHTDATA_API_TOKEN"],
        "nimble": [ENV_NIMBLE_API_KEY],
        "oxylabs": ["OXYLABS_WEB_API_KEY"],
    }

    monkeypatch.setenv(ENV_NIMBLE_API_KEY, "x")
    assert "nimble" not in tools.capabilities()["unlock"][MCP_METHOD_LIVE_DATA]
