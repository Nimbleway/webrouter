"""The MCP server itself: the tools, one resource, over stdio.

Built on the SDK's low-level :class:`~mcp.server.Server` rather than FastMCP,
because the input schemas are not knowable until the server starts: the
``provider`` enum is generated from the backends this machine is configured
for, which is the difference between offering a model a choice it can make and
offering it fourteen names that will throw.

The handlers are ordinary functions taking the session's
:class:`~web_router.mcp.tools.WebRouterTools`, so a test drives them directly
-- there is nothing here that needs a subprocess to exercise.

The server is also the owner of every browser session a model opened, so
:func:`serve` runs the idle reaper beside the protocol loop and, however the
loop ends -- the client closing stdin, a signal, a crash -- releases every
session before the process exits.

Because the schemas are generated from what is configured, ``load_env``
changes them, so the server advertises ``tools.listChanged`` and sends
``notifications/tools/list_changed`` after a load that configured a backend.
That reaches a client on the handshake protocol versions every client speaks
today; on the 2026-07-28 wire such notifications ride a
``subscriptions/listen`` stream this server does not serve, and the SDK drops
them, so a client there sees the new schemas when it next lists the tools.
"""

from __future__ import annotations

import base64
import json
import os
import signal
from collections.abc import Mapping
from functools import partial
from typing import Any

import anyio
from mcp.server import NotificationOptions, Server, ServerRequestContext
from mcp.server.stdio import stdio_server
from mcp.shared.exceptions import MCPError
from mcp_types import (
    CallToolRequestParams,
    CallToolResult,
    ContentBlock,
    ImageContent,
    ListResourcesResult,
    ListToolsResult,
    PaginatedRequestParams,
    ReadResourceRequestParams,
    ReadResourceResult,
    Resource,
    TextContent,
    TextResourceContents,
)

from web_router._version import __version__
from web_router.constants import (
    MCP_BROWSER_SHUTDOWN_TIMEOUT,
    MCP_MIME_JSON,
    MCP_MIME_PNG,
    MCP_MSG_UNKNOWN_RESOURCE,
    MCP_RESOURCE_DESCRIPTION,
    MCP_RESOURCE_NAME,
    MCP_RESOURCE_TITLE,
    MCP_RESOURCE_URI,
    MCP_SERVER_NAME,
    MCP_SHUTDOWN_SIGNALS,
    MCP_SIGNAL_EXIT_BASE,
)
from web_router.constants_mcp import MCP_RESOURCE_NOT_FOUND
from web_router.exceptions import WebRouterError
from web_router.mcp.env import EnvSources
from web_router.mcp.shaping import ToolOutput, redact, redaction_note, render
from web_router.mcp.tools import WebRouterTools


def list_tools(tools: WebRouterTools) -> ListToolsResult:
    """The tool list, generated against what this install is configured for."""
    return ListToolsResult(tools=tools.list_tools())


async def call_tool(tools: WebRouterTools, name: str, arguments: Mapping[str, Any]) -> CallToolResult:
    """Run one tool, reporting a failure as a tool error rather than a transport one.

    A missing key or an unmappable parameter is something the model can act on
    -- the library's message already names the environment variable to set --
    so it comes back as content the model reads, not as a JSON-RPC error it
    never sees.
    """
    try:
        payload = await tools.call(name, arguments)
    except (WebRouterError, ValueError) as exc:
        # ValueError covers this layer's own refusals and pydantic's request
        # validation, both of which are bad arguments rather than bugs.
        # Every tool's error is redacted, here rather than in each handler, so
        # no failure path can miss it: a browse backend's own error can quote
        # the session URL, and a vendor's HTTP error body rides on any method's
        # message. An ordinary message passes through untouched.
        message, count = redact(str(exc))
        message += redaction_note(count)
        return CallToolResult(content=[TextContent(text=message)], is_error=True)
    output = payload if isinstance(payload, ToolOutput) else ToolOutput(payload=payload)
    text = render(output.payload)
    content: list[ContentBlock] = [TextContent(text=text)]
    if output.text is not None:
        content.append(TextContent(text=output.text))
    if output.png is not None:
        content.append(ImageContent(data=base64.b64encode(output.png).decode("ascii"), mime_type=MCP_MIME_PNG))
    # The same object as the first text block, for a client that reads structured results; parsed back from the
    # text so it holds exactly what the model read, every value JSON.
    return CallToolResult(content=content, structured_content=json.loads(text))


def list_resources() -> ListResourcesResult:
    """The one resource: what this install can actually do."""
    return ListResourcesResult(
        resources=[
            Resource(
                uri=MCP_RESOURCE_URI,
                name=MCP_RESOURCE_NAME,
                title=MCP_RESOURCE_TITLE,
                description=MCP_RESOURCE_DESCRIPTION,
                mime_type=MCP_MIME_JSON,
            )
        ]
    )


def read_resource(tools: WebRouterTools, uri: str) -> ReadResourceResult:
    """The configured providers per method, so a client learns them without a round trip."""
    if uri != MCP_RESOURCE_URI:
        # The spec's resource-not-found code: anything else reaches the client as an internal error.
        raise MCPError(MCP_RESOURCE_NOT_FOUND, MCP_MSG_UNKNOWN_RESOURCE.format(uri=uri, known=MCP_RESOURCE_URI))
    return ReadResourceResult(
        contents=[
            TextResourceContents(uri=uri, mime_type=MCP_MIME_JSON, text=render(tools.capabilities())),
        ]
    )


def build_server(tools: WebRouterTools) -> Server[object]:
    """Wire the handlers onto a low-level server for one session's tools."""

    async def on_list_tools(
        context: ServerRequestContext[object],
        params: PaginatedRequestParams | None,
    ) -> ListToolsResult:
        return list_tools(tools)

    async def on_call_tool(
        context: ServerRequestContext[object],
        params: CallToolRequestParams,
    ) -> CallToolResult:
        result = await call_tool(tools, params.name, params.arguments or {})
        if tools.tools_changed():
            await context.session.send_tool_list_changed()
        return result

    async def on_list_resources(
        context: ServerRequestContext[object],
        params: PaginatedRequestParams | None,
    ) -> ListResourcesResult:
        return list_resources()

    async def on_read_resource(
        context: ServerRequestContext[object],
        params: ReadResourceRequestParams,
    ) -> ReadResourceResult:
        return read_resource(tools, params.uri)

    return Server(
        MCP_SERVER_NAME,
        version=__version__,
        instructions=tools.instructions(),
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
        on_list_resources=on_list_resources,
        on_read_resource=on_read_resource,
    )


async def serve(tools: WebRouterTools | None = None, *, env: EnvSources | None = None) -> None:
    """Serve one stdio session, then release every browser session and close every provider it built.

    The protocol loop ends when the client closes stdin, and the release then
    runs shielded from any cancellation and bounded by
    :data:`~web_router.constants.MCP_BROWSER_SHUTDOWN_TIMEOUT`. A signal in
    :data:`~web_router.constants.MCP_SHUTDOWN_SIGNALS` runs the same release
    and then exits the process -- see :func:`_exit_on_signal`.

    ``env`` is what the entry point's startup lookup found, for the tools this
    builds when ``tools`` is not given.
    """
    tools = tools if tools is not None else WebRouterTools(env=env)
    try:
        async with anyio.create_task_group() as group:
            group.start_soon(tools.browsers.run_reaper)
            group.start_soon(_exit_on_signal, tools)
            async with stdio_server() as (read_stream, write_stream):
                server = build_server(tools)
                # `load_env` changes what the schemas offer, so the client is told to list them again.
                options = server.create_initialization_options(NotificationOptions(tools_changed=True))
                await server.run(read_stream, write_stream, options)
            group.cancel_scope.cancel()
    finally:
        await _shutdown(tools)


async def _shutdown(tools: WebRouterTools) -> None:
    with anyio.move_on_after(MCP_BROWSER_SHUTDOWN_TIMEOUT, shield=True):
        await tools.aclose()


async def _exit_on_signal(tools: WebRouterTools) -> None:
    """On the first shutdown signal, release every session and exit, so a killed server bills nothing.

    Without a handler SIGTERM ends the process where it stands, and every
    browser it held keeps running. Cancelling the protocol loop instead is not
    enough: the SDK reads stdin on a worker thread that cancellation waits for,
    and that read returns only when the client writes or hangs up. So the
    release runs here, and the process then exits with the conventional
    128 + signal number status rather than waiting on a read that may never end.
    """
    signals = [getattr(signal, name) for name in MCP_SHUTDOWN_SIGNALS if hasattr(signal, name)]
    try:
        with anyio.open_signal_receiver(*signals) as received:
            async for signum in received:
                # Still inside the receiver, so a second signal cannot cut the release short.
                await _shutdown(tools)
                os._exit(MCP_SIGNAL_EXIT_BASE + int(signum))
    except NotImplementedError:
        # Windows' event loop cannot receive signals; stdin closing still ends the session.
        return


def main() -> None:
    """Load this machine's provider credentials, then run the stdio server until the client closes the pipe.

    With the ``load_env`` tool, the one place outside the tests and the
    benchmark scripts that reads a dotfile -- see :mod:`web_router.mcp.env`
    for where it looks and what it takes. A variable already exported wins,
    and finding no file is a no-op, so a server with no key anywhere starts on
    the keyless tier.
    """
    anyio.run(partial(serve, env=EnvSources.at_startup()))
