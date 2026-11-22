"""A name the navigation guard judges is resolved once, not on every request it routes.

The guard memoizes each host:port answer -- the allowed one and the refusal
alike -- for its lifetime, so a page's many subresources to one host cost one
lookup and share the single executor hop, rather than stalling every session's
routes once per request. No network: ``socket.getaddrinfo`` is answered from a
table.
"""

from __future__ import annotations

import socket
from typing import Any

import pytest

from tests.mcp.test_navigation import FakeRequest, FakeRoute, _installed
from web_router.mcp import navigation


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

    first = await guard._resolved_refusal("https://example.com/a.js")  # noqa: SLF001
    second = await guard._resolved_refusal("https://example.com/b.css")  # noqa: SLF001
    third = await guard._resolved_refusal("https://example.com/c.png")  # noqa: SLF001

    assert first is second is third is None
    assert asked == [("example.com", 443)]


async def test_each_distinct_host_is_resolved_once(monkeypatch: pytest.MonkeyPatch) -> None:
    guard, _ = await _installed()
    asked = _counted(monkeypatch, {})

    await guard._resolved_refusal("https://a.test/")  # noqa: SLF001
    await guard._resolved_refusal("https://b.test/")  # noqa: SLF001
    await guard._resolved_refusal("https://a.test/other")  # noqa: SLF001

    assert asked == [("a.test", 443), ("b.test", 443)]


async def test_a_refusal_is_cached_like_an_allowed_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    guard, _ = await _installed()
    asked = _counted(monkeypatch, {"md.attacker.test": ["169.254.169.254"]})

    first = await guard._resolved_refusal("http://md.attacker.test/latest/meta-data/")  # noqa: SLF001
    second = await guard._resolved_refusal("http://md.attacker.test/v2/credentials")  # noqa: SLF001

    assert first is not None and "cloud metadata endpoint" in first
    assert second == first
    assert asked == [("md.attacker.test", 80)]


async def test_a_page_with_many_subresources_resolves_its_host_once(monkeypatch: pytest.MonkeyPatch) -> None:
    _, page = await _installed()
    asked = _counted(monkeypatch, {})

    for path in ("/a.js", "/b.css", "/c.png"):
        await page.context.routes["http"](FakeRoute(FakeRequest(f"https://example.com{path}", navigation=False)))

    assert asked == [("example.com", 443)]
