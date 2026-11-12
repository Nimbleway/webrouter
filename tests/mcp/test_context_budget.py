"""What the server costs a model's context every turn: the tool list's size, the allowlist, compact results.

The tool list is sent to the model on every turn, so it is held to a budget
here: a change that grows it past :data:`TOOL_LIST_BUDGET` fails, and has to
either earn the bytes by raising the budget in review or find them elsewhere.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import jsonschema
import pytest

from mcp import Client
from tests.mcp.conftest import URL, outcome, routed
from web_router.client import AsyncWebRouter
from web_router.constants import MCP_TOOL_ANSWER, MCP_TOOL_EXTRACT, MCP_TOOL_FETCH_RAW, MCP_TOOL_SEARCH
from web_router.constants_mcp import MCP_TOOL_WEB_RUN
from web_router.constants_mcp_context import ENV_MCP_TOOLS, MCP_OUTPUT_SCHEMAS
from web_router.mcp import allowlist
from web_router.mcp.env_credentials import credential_variables
from web_router.mcp.server import build_server, call_tool
from web_router.mcp.shaping import render, shape_research
from web_router.mcp.tools import WebRouterTools
from web_router.types import ResearchResponse

TOOL_LIST_BUDGET = 26_000
"""Bytes of compact JSON the whole tool list may take with every backend configured -- about 6.5k tokens.

It was 31,051 (about 7.8k tokens) before descriptions were cut down and the
provider sentence said once, and is 25,630 with the output schemas.
"""

TOOL_BUDGET = 2_200
"""Bytes any one tool may take: ``search``, the largest, is 2,181."""


def _size(value: Any) -> int:
    return len(json.dumps(value, separators=(",", ":")))


@pytest.fixture
def every_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every credential set, and Playwright taken as installed: the longest tool list there is."""
    for env_var in credential_variables():
        monkeypatch.setenv(env_var, "fake")
    monkeypatch.setattr("web_router.mcp.tools.playwright_installed", lambda: True)
    monkeypatch.delenv(ENV_MCP_TOOLS, raising=False)


def _listed(tools: WebRouterTools) -> list[dict[str, Any]]:
    return [tool.model_dump(mode="json", by_alias=True, exclude_none=True) for tool in tools.list_tools()]


def test_the_default_tool_list_stays_inside_its_budget(every_key: None) -> None:
    listed = _listed(WebRouterTools())

    sizes = {tool["name"]: _size(tool) for tool in listed}
    assert _size(listed) <= TOOL_LIST_BUDGET, f"{_size(listed)} bytes; per tool: {sizes}"
    assert max(sizes.values()) <= TOOL_BUDGET, sizes


def test_the_provider_sentence_is_said_once_not_per_tool(every_key: None) -> None:
    tools = WebRouterTools()

    for tool in _listed(tools):
        provider = tool["inputSchema"]["properties"].get("provider", {})
        assert "description" not in provider, tool["name"]
    assert "omit it and the router picks one" in tools.instructions()


def test_the_allowlist_lists_and_serves_only_the_tools_it_names(every_key: None, monkeypatch: Any) -> None:
    monkeypatch.setenv(ENV_MCP_TOOLS, f" {MCP_TOOL_SEARCH}, browser_* ,{MCP_TOOL_FETCH_RAW}")

    names = [tool["name"] for tool in _listed(WebRouterTools())]

    assert names[0] == MCP_TOOL_SEARCH and MCP_TOOL_FETCH_RAW in names
    assert {name for name in names if name.startswith("browser_")} and MCP_TOOL_ANSWER not in names


async def test_a_tool_left_off_the_allowlist_is_unknown(
    monkeypatch: pytest.MonkeyPatch, fake_answer_provider: Any
) -> None:
    monkeypatch.setenv(ENV_MCP_TOOLS, MCP_TOOL_SEARCH)
    tools = WebRouterTools(AsyncWebRouter("fake"))

    result = await call_tool(tools, MCP_TOOL_ANSWER, {"query": "q"})

    assert result.is_error
    assert result.content[0].text == f"Unknown tool 'answer'. This server serves: {MCP_TOOL_SEARCH}."


def test_an_allowlist_entry_that_names_nothing_is_warned_about(caplog: pytest.LogCaptureFixture) -> None:
    handlers = {MCP_TOOL_SEARCH: 1, MCP_TOOL_EXTRACT: 2}

    with caplog.at_level(logging.WARNING):
        kept = allowlist.allowed(handlers, "serch,extract")

    assert kept == {MCP_TOOL_EXTRACT: 2}
    assert "serch" in caplog.text and ENV_MCP_TOOLS in caplog.text


@pytest.mark.parametrize("value", ["", " , "])
def test_a_blank_allowlist_serves_every_tool(value: str) -> None:
    handlers = {MCP_TOOL_SEARCH: 1, MCP_TOOL_EXTRACT: 2}

    assert allowlist.allowed(handlers, value) == handlers


def test_a_result_is_rendered_compact() -> None:
    assert render({"a": [1, 2], "b": {"c": "d"}}) == '{"a":[1,2],"b":{"c":"d"}}'


async def test_core_results_match_their_published_output_schemas(tools: WebRouterTools) -> None:
    calls = {
        MCP_TOOL_SEARCH: {"query": "q"},
        MCP_TOOL_EXTRACT: {"urls": ["https://example.com"]},
        MCP_TOOL_ANSWER: {"query": "q"},
    }
    listed = {tool.name: tool for tool in tools.list_tools()}

    for name, arguments in calls.items():
        assert listed[name].output_schema == MCP_OUTPUT_SCHEMAS[name]
        result = await call_tool(tools, name, arguments)
        jsonschema.validate(result.structured_content, MCP_OUTPUT_SCHEMAS[name])


async def test_answer_keeps_its_own_arguments_from_the_library(tools: WebRouterTools) -> None:
    result = await call_tool(tools, MCP_TOOL_ANSWER, {"query": "q", "all_sources": True, "snippets": True})

    assert not result.is_error, result.content[0].text
    request = tools.router.get_answer_provider("fake").requests[-1]  # type: ignore[attr-defined]
    assert request.query == "q"


async def test_resource_templates_list_is_empty_not_method_not_found(tools: WebRouterTools) -> None:
    async with Client(build_server(tools)) as client:
        assert (await client.list_resource_templates()).resource_templates == []


async def test_web_run_will_not_run_a_method_the_allowlist_leaves_out(
    members: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV_MCP_TOOLS, f"{MCP_TOOL_WEB_RUN},{MCP_TOOL_SEARCH}")

    failed, text = await outcome(routed(), MCP_TOOL_WEB_RUN, prompt=f"read {URL}")

    assert failed and f"web_run planned {MCP_TOOL_EXTRACT}" in text and ENV_MCP_TOOLS in text
    assert members == [], "nothing was run"


def test_an_empty_structured_answer_is_kept() -> None:
    response = ResearchResponse(provider="fake", query="q", structured=[], answer=None)

    assert shape_research(response, "research-0001", budget=4000)["structured"] == []
