"""A hop flagged while :meth:`NavigationGuard.settle` waits on DNS answers is still refused by that settle."""

from __future__ import annotations

import socket
from typing import Any

import pytest

from tests.mcp.test_navigation import FakeRequest, _installed
from web_router.constants import MCP_BROWSER_BLANK_URL
from web_router.mcp import navigation


async def test_a_hop_flagged_while_names_resolve_is_still_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    guard, page = await _installed()

    def resolve(host: str, port: Any, *args: Any, **kwargs: Any) -> list[tuple[Any, ...]]:
        # The page redirects to the metadata service while the guard is busy checking another name.
        page.context.listeners["request"](FakeRequest("http://169.254.169.254/latest/meta-data/"))
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", port or 0))]

    monkeypatch.setattr(navigation.socket, "getaddrinfo", resolve)
    page.context.listeners["request"](FakeRequest("http://slow.example/"))
    page.url = "https://example.com/"

    reason = await guard.settle()

    assert reason is not None and "cloud metadata endpoint" in reason
    assert page.visited == [MCP_BROWSER_BLANK_URL]
