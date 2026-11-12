"""``readOnlyHint`` means safe to run unasked: a tool that bills is not read-only.

A client that auto-approves read-only tools must stop before a call that
spends money. ``answer`` and ``live_data`` bill every call; ``search`` and
``extract`` bill once a paid backend is configured, since ``auto`` tries it first.
"""

from __future__ import annotations

import pytest

from web_router.constants import (
    ENV_EXA_API_KEY,
    ENV_TAVILY_API_KEY,
    MCP_TOOL_ANSWER,
    MCP_TOOL_EXTRACT,
    MCP_TOOL_FETCH_RAW,
    MCP_TOOL_LIVE_DATA,
    MCP_TOOL_SEARCH,
)
from web_router.mcp.schema import tool_annotations


@pytest.mark.parametrize("tool", [MCP_TOOL_ANSWER, MCP_TOOL_LIVE_DATA])
def test_a_tool_that_bills_every_call_is_never_read_only(tool: str, no_keys: None) -> None:
    hints = tool_annotations(tool)

    assert hints.read_only_hint is False
    assert hints.idempotent_hint is False, "running it again bills again"


@pytest.mark.parametrize("tool", [MCP_TOOL_SEARCH, MCP_TOOL_EXTRACT])
def test_search_and_extract_are_read_only_only_on_the_keyless_tier(
    tool: str, no_keys: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert tool_annotations(tool).read_only_hint is True, "free with no key, so a client may run it unasked"

    monkeypatch.setenv(ENV_TAVILY_API_KEY, "x")  # a paid backend for both methods
    monkeypatch.setenv(ENV_EXA_API_KEY, "x")

    hints = tool_annotations(tool)
    assert hints.read_only_hint is False, "auto now tries a keyed backend first, and each call bills"
    assert hints.idempotent_hint is False


def test_a_tool_that_calls_nothing_stays_read_only(no_keys: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_TAVILY_API_KEY, "x")

    assert tool_annotations(MCP_TOOL_FETCH_RAW).read_only_hint is True
