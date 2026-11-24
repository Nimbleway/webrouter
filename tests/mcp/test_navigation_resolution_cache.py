"""A name the navigation guard judges is resolved once per short window, not on every request it routes.

The guard memoizes each host:port answer -- the allowed one and the refusal
alike -- briefly, so a page's many subresources to one host share one lookup
and one executor hop, rather than stalling every session's routes once per
request. The answer expires because a name can be rebound after the browser
has made its own lookup, and the cache is capped so a busy page cannot grow it
without bound. No network: ``socket.getaddrinfo`` is answered from a table.
"""

from __future__ import annotations

import asyncio
import socket
import threading
from typing import Any

import pytest

from tests.mcp.test_navigation import FakeRequest, FakeRoute, _installed
from web_router.mcp import navigation, navigation_dns
from web_router.mcp.navigation_dns import MCP_BROWSER_DNS_CACHE_MAX


def _counted(monkeypatch: pytest.MonkeyPatch, answers: dict[str, list[str]]) -> list[tuple[str, Any]]:
    """Patch ``getaddrinfo`` to answer from ``answers`` (public by default) and log every call."""
    asked: list[tuple[str, Any]] = []

    def resolve(host: str, port: Any, *args: Any, **kwargs: Any) -> list[tuple[Any, ...]]:
        asked.append((host, port))
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port or 0))
            for address in answers.get(host, ["93.184.215.14"])
        ]

    monkeypatch.setattr(navigation.socket, "getaddrinfo", resolve)
    return asked


async def test_a_repeated_host_is_resolved_once(monkeypatch: pytest.MonkeyPatch) -> None:
    guard, _ = await _installed()
    asked = _counted(monkeypatch, {})

    first = await guard._resolved_refusal("https://example.com/a.js")
    second = await guard._resolved_refusal("https://example.com/b.css")
    third = await guard._resolved_refusal("https://example.com/c.png")

    assert first is second is third is None
    assert asked == [("example.com", 443)]


async def test_each_distinct_host_is_resolved_once(monkeypatch: pytest.MonkeyPatch) -> None:
    guard, _ = await _installed()
    asked = _counted(monkeypatch, {})

    await guard._resolved_refusal("https://a.test/")
    await guard._resolved_refusal("https://b.test/")
    await guard._resolved_refusal("https://a.test/other")

    assert asked == [("a.test", 443), ("b.test", 443)]


async def test_a_refusal_is_cached_like_an_allowed_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    guard, _ = await _installed()
    asked = _counted(monkeypatch, {"md.attacker.test": ["169.254.169.254"]})

    first = await guard._resolved_refusal("http://md.attacker.test/latest/meta-data/")
    second = await guard._resolved_refusal("http://md.attacker.test/v2/credentials")

    assert first is not None and "cloud metadata endpoint" in first
    assert second == first
    assert asked == [("md.attacker.test", 80)]


async def test_a_page_with_many_subresources_resolves_its_host_once(monkeypatch: pytest.MonkeyPatch) -> None:
    _, page = await _installed()
    asked = _counted(monkeypatch, {})

    for path in ("/a.js", "/b.css", "/c.png"):
        await page.context.routes["http"](FakeRoute(FakeRequest(f"https://example.com{path}", navigation=False)))

    assert asked == [("example.com", 443)]


async def test_a_rebound_name_is_not_served_a_stale_allow(monkeypatch: pytest.MonkeyPatch) -> None:
    guard, _ = await _installed()
    monkeypatch.setattr(navigation_dns, "MCP_BROWSER_DNS_CACHE_TTL_S", 0)
    answers = {"example.com": ["93.184.215.14"]}
    asked = _counted(monkeypatch, answers)

    first = await guard._resolved_refusal("https://example.com/a")
    answers["example.com"] = ["169.254.169.254"]
    second = await guard._resolved_refusal("https://example.com/b")

    assert first is None
    assert second is not None
    assert "cloud metadata endpoint" in second
    assert asked == [("example.com", 443), ("example.com", 443)]


async def test_concurrent_misses_share_one_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    guard, _ = await _installed()
    gate = threading.Event()
    asked: list[tuple[str, Any]] = []

    def resolve(host: str, port: int, *args: Any, **kwargs: Any) -> list[Any]:
        asked.append((host, port))
        gate.wait(timeout=5)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", port))]

    monkeypatch.setattr(navigation.socket, "getaddrinfo", resolve)
    calls = [asyncio.create_task(guard._resolved_refusal(f"https://example.com/{index}")) for index in range(5)]
    await asyncio.sleep(0)
    gate.set()

    assert await asyncio.gather(*calls) == [None] * 5
    assert asked == [("example.com", 443)]


async def test_the_answer_cache_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    guard, _ = await _installed()
    _counted(monkeypatch, {})

    for index in range(MCP_BROWSER_DNS_CACHE_MAX + 10):
        await guard._resolved_refusal(f"https://host{index}.example/")

    assert len(guard._dns._answers) == MCP_BROWSER_DNS_CACHE_MAX
