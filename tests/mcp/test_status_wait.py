"""``research_status`` and ``browse_status`` wait on their job, as ``research`` and ``browse`` do.

Answering at once left the pacing to the model, which polled in a tight loop or spent its turns on polls. Each
status call now waits up to the starting tool's wait, answers the moment the job stops, and otherwise says it is
still going. Jobs here are polled every hundredth of a second, so the waits run in a test's time.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.conftest import FakeBrowseProvider, FakeResearchProvider
from tests.mcp.conftest import FAKE
from web_router import AsyncWebRouter
from web_router.constants import (
    MCP_REDACTED,
    MCP_TOOL_BROWSE,
    MCP_TOOL_BROWSE_STATUS,
    MCP_TOOL_RESEARCH,
    MCP_TOOL_RESEARCH_STATUS,
)
from web_router.exceptions import ProviderError
from web_router.mcp.server import call_tool, list_tools
from web_router.mcp.tools import WebRouterTools
from web_router.providers.base import Job
from web_router.registry import (
    register_browse_provider,
    register_research_provider,
    unregister_browse_provider,
    unregister_research_provider,
)


@pytest.fixture(autouse=True)
def quick_polls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Job, "poll_interval", 0.01)
    monkeypatch.setattr(Job, "poll_max_interval", 0.01)


def _payload(result: Any) -> dict[str, Any]:
    assert not result.is_error, result.content[0].text
    return dict(json.loads(result.content[0].text))


async def _started(tools: WebRouterTools, wait: float) -> str:
    """A research run left going by ``research`` (the fixture's zero wait), then the status tool's wait set."""
    pending = _payload(await call_tool(tools, MCP_TOOL_RESEARCH, {"query": "why"}))
    assert pending["state"] == "running"
    tools.research_wait = wait
    return str(pending["handle"])


async def test_a_run_that_finishes_inside_the_wait_is_answered_by_one_status_call(tools: WebRouterTools) -> None:
    class SlowResearchProvider(FakeResearchProvider):
        polls_before_done = 5

    register_research_provider(FAKE, SlowResearchProvider)
    try:
        handle = await _started(tools, 5.0)

        answered = _payload(await call_tool(tools, MCP_TOOL_RESEARCH_STATUS, {"handle": handle}))

        assert answered["state"] == "completed"
        assert answered["answer"] == "the answer"
        assert tools.router.get_research_provider(FAKE).polls == 5  # type: ignore[attr-defined]
    finally:
        unregister_research_provider(FAKE)


async def test_a_run_still_going_after_the_wait_says_to_call_again(tools: WebRouterTools) -> None:
    class SlowResearchProvider(FakeResearchProvider):
        polls_before_done = 1_000

    register_research_provider(FAKE, SlowResearchProvider)
    try:
        handle = await _started(tools, 0.05)

        payload = _payload(await call_tool(tools, MCP_TOOL_RESEARCH_STATUS, {"handle": handle}))

        assert payload["state"] == "running"
        assert "answer" not in payload
        assert MCP_TOOL_RESEARCH_STATUS in payload["note"] and repr(handle) in payload["note"]
        assert tools.router.get_research_provider(FAKE).polls > 1  # type: ignore[attr-defined]
    finally:
        unregister_research_provider(FAKE)


async def test_a_run_that_fails_during_the_wait_is_reported_as_its_state(tools: WebRouterTools) -> None:
    class FailingResearchProvider(FakeResearchProvider):
        polls_before_done = 1_000

        def _fetch(self, request: Any, job_id: str) -> Any:
            if self.polls >= 2:
                self.fails_with = "the backend gave up"
            return super()._fetch(request, job_id)

    register_research_provider(FAKE, FailingResearchProvider)
    try:
        handle = await _started(tools, 5.0)

        payload = _payload(await call_tool(tools, MCP_TOOL_RESEARCH_STATUS, {"handle": handle}))

        assert payload["state"] == "failed"
        assert payload["detail"] == "the backend gave up"
        assert "note" not in payload
    finally:
        unregister_research_provider(FAKE)


async def test_a_poll_that_fails_while_the_run_is_going_is_a_tool_error(tools: WebRouterTools) -> None:
    class RefusingResearchProvider(FakeResearchProvider):
        polls_before_done = 1_000
        refusing = False

        def _fetch(self, request: Any, job_id: str) -> Any:
            if self.refusing:
                raise ProviderError(self.name, "the key was revoked")
            return super()._fetch(request, job_id)

    register_research_provider(FAKE, RefusingResearchProvider)
    try:
        handle = await _started(tools, 5.0)
        RefusingResearchProvider.refusing = True

        result = await call_tool(tools, MCP_TOOL_RESEARCH_STATUS, {"handle": handle})

        assert result.is_error
        assert "the key was revoked" in result.content[0].text
    finally:
        unregister_research_provider(FAKE)


async def test_browse_status_waits_for_a_task_that_finishes_inside_the_wait(tools: WebRouterTools) -> None:
    class SlowBrowseProvider(FakeBrowseProvider):
        polls_before_done = 5

    register_browse_provider(FAKE, SlowBrowseProvider)
    try:
        pending = _payload(await call_tool(tools, MCP_TOOL_BROWSE, {"task": "find the price"}))
        tools.browse_wait = 5.0

        answered = _payload(await call_tool(tools, MCP_TOOL_BROWSE_STATUS, {"handle": pending["handle"]}))

        assert answered["state"] == "completed"
        assert answered["answer"] == "the answer"
    finally:
        unregister_browse_provider(FAKE)


async def test_a_task_still_going_keeps_its_note_when_its_detail_is_redacted(tools: WebRouterTools) -> None:
    class LeakyRunningBrowseProvider(FakeBrowseProvider):
        polls_before_done = 1_000

        def _progress(self, request: Any) -> Any:
            progress = super()._progress(request)
            return progress.model_copy(update={"detail": "live at wss://connect.example.test/?token=SEKRIT"})

    register_browse_provider(FAKE, LeakyRunningBrowseProvider)
    try:
        pending = _payload(await call_tool(tools, MCP_TOOL_BROWSE, {"task": "find the price"}))
        tools.browse_wait = 0.05

        payload = _payload(await call_tool(tools, MCP_TOOL_BROWSE_STATUS, {"handle": pending["handle"]}))

        assert payload["state"] == "running"
        assert "SEKRIT" not in json.dumps(payload)
        # Both said: call again, and what was taken out.
        assert MCP_TOOL_BROWSE_STATUS in payload["note"] and repr(pending["handle"]) in payload["note"]
        assert MCP_REDACTED in payload["note"]
    finally:
        unregister_browse_provider(FAKE)


def test_each_status_tool_says_how_long_it_waits(all_runnable: None) -> None:
    tools = WebRouterTools(AsyncWebRouter(), research_wait=12.0, browse_wait=7.0)
    described = {tool.name: tool.description for tool in list_tools(tools).tools}

    assert "12s" in described[MCP_TOOL_RESEARCH_STATUS]
    assert "7s" in described[MCP_TOOL_BROWSE_STATUS]
