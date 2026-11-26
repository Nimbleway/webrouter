"""How much of a finished research run's report reaches the model: all of a typical one, and a marked cut past that."""

from __future__ import annotations

import pytest

from web_router.constants import ENV_MCP_MAX_CHARS, MCP_TOOL_RESEARCH, MCP_TOOL_SEARCH, MCP_TRUNCATION_MARKER
from web_router.mcp.shaping import render, result_budget, shape_research
from web_router.types import Citation, ResearchResponse

_PROSE = "alpha beta gamma delta " * 200


def _research_response(report_chars: int, citations: int = 40) -> ResearchResponse:
    return ResearchResponse(
        provider="fake",
        query="q",
        answer=_PROSE * (report_chars // len(_PROSE) + 1),
        citations=[
            Citation(url=f"https://example.com/{i}", title=f"Source {i}", snippet=_PROSE[:600])
            for i in range(citations)
        ],
    )


def test_a_research_result_gets_the_documented_budget_and_scales_with_the_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The sizes docs/mcp-server.mdx promises: 40,000 for a finished research run, 8,000 for any other result.
    assert result_budget(MCP_TOOL_RESEARCH) == 40_000
    assert result_budget(MCP_TOOL_SEARCH) == 8_000

    monkeypatch.setenv(ENV_MCP_MAX_CHARS, "1000")
    assert result_budget(MCP_TOOL_RESEARCH) == 5000


def test_a_typical_research_report_reaches_the_model_whole() -> None:
    response = _research_response(25_000)
    budget = result_budget(MCP_TOOL_RESEARCH)

    payload = shape_research(response, "research-0001", budget=budget)

    assert payload["answer"] == response.answer
    assert payload["truncated"] is False
    assert len(payload["citations"]) == 40 and payload["citations"][0]["url"] == "https://example.com/0"
    assert len(render(payload)) <= budget


def test_a_research_report_past_its_share_is_cut_and_names_the_handle() -> None:
    response = _research_response(60_000)
    budget = result_budget(MCP_TOOL_RESEARCH)

    payload = shape_research(response, "research-0001", budget=budget)

    assert payload["answer"].endswith(MCP_TRUNCATION_MARKER)
    assert len(payload["answer"]) > budget // 2
    assert payload["truncated"] is True and payload["handle"] == "research-0001" and "fetch_raw" in payload["note"]
    assert len(render(payload)) <= budget
