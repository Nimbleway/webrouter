"""The server over a real stdio pipe, in a real subprocess.

Every other test in this directory calls the handlers directly, which is fast
and covers what they do. It cannot catch the things that only exist between two
processes: a schema that will not serialize, a handler whose signature the SDK
does not call the way we think, a server that raises on startup. This module is
the one that crosses the boundary, so those stay covered by CI rather than by
somebody having run it once by hand.

The protocol tests here touch no network. They strip every credential from the
child's environment -- which also pins the claim the docs lead with, that the
server is useful before you have signed up for anything. The one test that does
reach the web is marked ``integration`` like every other live test here.
"""

from __future__ import annotations

import json
import sys
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from mcp.client.stdio import stdio_client

from mcp import ClientSession, StdioServerParameters
from tests.mcp.conftest import isolated_environment
from web_router.constants import (
    MCP_BROWSER_TOOLS,
    MCP_RESOURCE_URI,
    MCP_TOOL_BROWSE,
    MCP_TOOL_BROWSE_STATUS,
    MCP_TOOL_EXTRACT,
    MCP_TOOL_FETCH_RAW,
    MCP_TOOL_LOAD_ENV,
    MCP_TOOL_RESEARCH,
    MCP_TOOL_RESEARCH_STATUS,
    MCP_TOOL_RUN_TEMPLATE,
    MCP_TOOL_SEARCH,
    MCP_TOOL_TEMPLATE,
    MCP_TOOL_TEMPLATES,
    PROVIDER_AUTO,
    PROVIDER_EXA_MCP,
    PROVIDER_JINA,
    PROVIDER_MAX,
    PROVIDER_PARALLEL_MCP,
    PROVIDER_YOU_MCP,
)

EVERY_TOOL = [
    MCP_TOOL_SEARCH,
    MCP_TOOL_EXTRACT,
    MCP_TOOL_TEMPLATES,
    MCP_TOOL_TEMPLATE,
    MCP_TOOL_RUN_TEMPLATE,
    MCP_TOOL_RESEARCH,
    MCP_TOOL_RESEARCH_STATUS,
    MCP_TOOL_BROWSE,
    MCP_TOOL_BROWSE_STATUS,
    MCP_TOOL_FETCH_RAW,
    MCP_TOOL_LOAD_ENV,
    *MCP_BROWSER_TOOLS,
]


@asynccontextmanager
async def _session(env: dict[str, str] | None = None) -> AsyncIterator[ClientSession]:
    """A client talking to the installed server over its own stdio pipe.

    Launched as ``python -m web_router.mcp`` rather than through the console
    script, so the test exercises this checkout instead of whatever happens to
    be on PATH -- and from an empty working directory with a home directory of
    its own, so the entry point's ``.env`` lookup cannot hand the child this
    machine's keys back after the environment stripped them.
    """
    with tempfile.TemporaryDirectory() as scratch:
        cwd, home = Path(scratch) / "work", Path(scratch) / "home"
        cwd.mkdir()
        home.mkdir()
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "web_router.mcp"],
            env=env if env is not None else isolated_environment(home),
            cwd=str(cwd),
        )
        async with stdio_client(parameters) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            yield session


def _provider_enum(schema: dict[str, Any]) -> list[str] | None:
    return (schema.get("properties", {}).get("provider") or {}).get("enum")


async def test_the_server_starts_and_offers_every_tool() -> None:
    """The handshake itself -- the one thing no in-process test can prove."""
    async with _session() as session:
        listed = await session.list_tools()

    assert [tool.name for tool in listed.tools] == EVERY_TOOL
    assert all(tool.description for tool in listed.tools), "a tool with no description cannot be chosen well"


async def test_the_schemas_describe_an_install_with_no_credentials() -> None:
    """With no key anywhere, the tools offer exactly the backends that need none.

    Serialized through the protocol rather than read off the objects, because a
    schema that survives construction and not JSON is the failure this module
    exists to catch.
    """
    async with _session() as session:
        listed = await session.list_tools()

    schemas = {tool.name: tool.input_schema for tool in listed.tools}
    assert _provider_enum(schemas[MCP_TOOL_SEARCH]) == [
        PROVIDER_AUTO,
        PROVIDER_EXA_MCP,
        PROVIDER_PARALLEL_MCP,
        PROVIDER_YOU_MCP,
    ]
    assert _provider_enum(schemas[MCP_TOOL_EXTRACT]) == [PROVIDER_AUTO, PROVIDER_JINA, PROVIDER_MAX]
    # Every research backend bills, so with no key there is nothing but the
    # chooser -- and its description has to say so rather than offering a tool
    # that can only fail.
    assert _provider_enum(schemas[MCP_TOOL_RESEARCH]) == [PROVIDER_AUTO]


async def test_a_failure_arrives_as_a_tool_error_and_not_a_broken_pipe() -> None:
    """A refusal must reach the model as something it can read and retry.

    Crossing the process boundary is the point: an exception that escapes the
    handler would tear down the session instead, and no in-process test of the
    handler can tell the difference.
    """
    async with _session() as session:
        result = await session.call_tool(MCP_TOOL_FETCH_RAW, {"handle": "never-issued"})

    assert result.is_error
    assert "never-issued" in result.content[0].text


async def test_the_capabilities_resource_is_readable() -> None:
    async with _session() as session:
        listed = await session.list_resources()
        contents = await session.read_resource(MCP_RESOURCE_URI)

    assert [str(resource.uri) for resource in listed.resources] == [MCP_RESOURCE_URI]
    assert PROVIDER_AUTO in contents.contents[0].text


@pytest.mark.integration
async def test_live_search_answers_with_no_credentials_at_all() -> None:
    """The claim the documentation leads with, proved end to end.

    Marked ``integration`` because it reaches the web -- but unlike every other
    live test here it needs no credential, which is the whole point: the child
    is started with every key stripped and still answers.
    """
    async with _session() as session:
        result = await session.call_tool(MCP_TOOL_SEARCH, {"query": "what is retrieval augmented generation"})

    assert not result.is_error, result.content[0].text
    # Read from the text block rather than `structured_content`: that is where
    # a client actually finds the answer, and a change that emptied it would
    # otherwise pass here while breaking every real caller.
    payload = json.loads(result.content[0].text)
    assert payload.get("result_count"), "the keyless tier answered with nothing"
    assert payload.get("provider") not in (None, PROVIDER_AUTO), "must name the backend that served it"
    assert "raw" not in payload, "raw belongs behind a handle, never in the context window"
