"""``fetch_raw`` in pages: a client that cuts long tool results can still read a whole payload, a page per call."""

from __future__ import annotations

from typing import Any

import pytest

from tests.mcp.conftest import ok_on, outcome
from tests.mcp.env_helpers import KEY
from web_router.client import AsyncWebRouter
from web_router.constants import ENV_BRAVE_API_KEY, ENV_MCP_MAX_CHARS, MCP_TOOL_FETCH_RAW, MCP_TOOL_RESEARCH
from web_router.constants_mcp_instructions import MCP_UNTRUSTED_MARK
from web_router.constants_vendors import VENDOR_MASK
from web_router.mcp.shaping import render
from web_router.mcp.tools import WebRouterTools

REPORT = {"answer": " ".join(f"finding {i} with its source" for i in range(400)), "citations": [{"n": 1}]}


@pytest.fixture
def tools(no_keys: None) -> WebRouterTools:
    return WebRouterTools(AsyncWebRouter("auto"))


async def _read_all(tools: WebRouterTools, handle: str, max_chars: int) -> list[dict[str, Any]]:
    pages = [await ok_on(tools, MCP_TOOL_FETCH_RAW, handle=handle, offset=0, max_chars=max_chars)]
    while "next_offset" in pages[-1]:
        pages.append(
            await ok_on(tools, MCP_TOOL_FETCH_RAW, handle=handle, offset=pages[-1]["next_offset"], max_chars=max_chars)
        )
    return pages


async def test_pages_join_into_the_whole_payload_and_say_where_the_next_one_starts(tools: WebRouterTools) -> None:
    record = tools.store.add(MCP_TOOL_RESEARCH, raw=REPORT)

    pages = await _read_all(tools, record.handle, max_chars=1000)

    whole = render(REPORT)
    assert "".join(page["page"] for page in pages) == whole
    assert all(len(page["page"]) <= 1000 for page in pages) and len(pages) == -(-len(whole) // 1000)
    assert {page["total_chars"] for page in pages} == {len(whole)}
    assert f"offset {pages[0]['next_offset']}" in pages[0]["note"], "the note says how to ask for the next page"
    assert "next_offset" not in pages[-1] and "next page" not in pages[-1]["note"]
    assert all(page.items() >= MCP_UNTRUSTED_MARK.items() for page in pages), "a page is web text, marked as such"


async def test_without_a_page_argument_the_payload_comes_back_whole_as_before(tools: WebRouterTools) -> None:
    record = tools.store.add(MCP_TOOL_RESEARCH, raw=REPORT)

    assert await ok_on(tools, MCP_TOOL_FETCH_RAW, handle=record.handle) == {
        **MCP_UNTRUSTED_MARK,
        "handle": record.handle,
        "tool": MCP_TOOL_RESEARCH,
        "raw": REPORT,
    }


async def test_an_offset_alone_reads_a_page_of_the_servers_result_size(
    tools: WebRouterTools, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV_MCP_MAX_CHARS, "2500")
    record = tools.store.add(MCP_TOOL_RESEARCH, raw=REPORT)

    page = await ok_on(tools, MCP_TOOL_FETCH_RAW, handle=record.handle, offset=100)

    assert page["page"] == render(REPORT)[100:2600] and page["next_offset"] == 2600


async def test_a_key_is_masked_before_the_payload_is_cut_into_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_BRAVE_API_KEY, KEY)
    tools = WebRouterTools(AsyncWebRouter("auto"))
    raw = {"echo": f"{'x' * 95}{KEY}{'y' * 95}"}
    record = tools.store.add(MCP_TOOL_RESEARCH, raw=raw)

    pages = await _read_all(tools, record.handle, max_chars=len(KEY) // 2)

    joined = "".join(page["page"] for page in pages)
    assert KEY not in joined and VENDOR_MASK in joined, "a page boundary inside the key cannot carry half of it out"


@pytest.mark.parametrize(
    "arguments",
    [{"offset": -1}, {"max_chars": 0}, {"offset": "10"}, {"max_chars": True}, {"offset": 10**9}],
    ids=["negative-offset", "zero-size", "string", "bool", "past-the-end"],
)
async def test_a_page_argument_it_cannot_serve_is_refused_with_a_reason(
    tools: WebRouterTools, arguments: dict[str, Any]
) -> None:
    record = tools.store.add(MCP_TOOL_RESEARCH, raw=REPORT)

    failed, text = await outcome(tools, MCP_TOOL_FETCH_RAW, handle=record.handle, **arguments)

    assert failed and next(iter(arguments)) in text
