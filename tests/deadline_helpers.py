"""What the per-call deadline tests share: a fake clock, backends over the HTTP base that hang, and a call per method.

There is no public parameter for a deadline yet; the tests set one the way the
router will, with :func:`~web_router.providers._deadline.call_deadline`. The
sync tests run on a fake clock -- a backend that "hangs" moves it on by the
timeout its request was given, as a clamped read timeout would -- and the async
ones on a deadline of a few hundredths of a second, so nothing waits for real.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from web_router.providers import _deadline, base
from web_router.providers._http import HttpAnswerProvider, HttpExtractProvider, HttpSearchProvider
from web_router.types import ExtractedDocument, ExtractRequest, ExtractResponse, SearchRequest, SearchResponse
from web_router.types_answer import AnswerRequest, AnswerResponse

DEADLINE = 3.0
TINY = 0.05
"""An async deadline: long enough to start a call, short enough that nobody waits for it."""

URLS = ["https://a.example", "https://b.example", "https://c.example"]
SLOW = "https://b.example"


class _Clock:
    """``time`` as the router reads it, moved on only by a backend that hangs or a poll that sleeps."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def install_clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    """A fake clock in place of the real one, for the ``clock`` fixture in ``tests/conftest.py``."""
    fake = _Clock()
    monkeypatch.setattr(base, "time", fake)
    monkeypatch.setattr(_deadline, "time", fake)
    return fake


# -- Backends over the shared HTTP base ---------------------------------------


class _Acme(HttpSearchProvider):
    name = "acme"
    base_url = "https://acme.example"
    search_path = "/search"

    def _build_payload(self, request: SearchRequest) -> dict[str, Any]:
        return {"q": request.query}

    def _to_response(self, request: SearchRequest, payload: dict[str, Any]) -> SearchResponse:
        return SearchResponse(provider=self.name, query=request.query, results=[])


class _AcmeAnswer(HttpAnswerProvider):
    name = "acme"
    base_url = "https://acme.example"
    answer_path = "/answer"

    def _build_payload(self, request: AnswerRequest) -> dict[str, Any]:
        return {"q": request.query}

    def _to_response(self, request: AnswerRequest, payload: dict[str, Any]) -> AnswerResponse:
        return AnswerResponse(provider=self.name, query=request.query, answer="yes")


class _AcmeExtract(HttpExtractProvider):
    name = "acme"
    base_url = "https://acme.example"
    extract_path = "/extract"
    supported_formats = frozenset({"markdown"})
    default_format = "markdown"

    def _build_payload(self, request: ExtractRequest, urls: list[str]) -> dict[str, Any]:
        return {"url": urls[0]}

    def _to_response(self, request: ExtractRequest, urls: list[str], payload: dict[str, Any]) -> ExtractResponse:
        document = ExtractedDocument(url=urls[0], content="hi", format="markdown")
        return ExtractResponse(provider=self.name, documents=[document])


def _hangs_until_timeout(clock: _Clock, seen: list[float], *, slow: str | None = None) -> httpx.MockTransport:
    """A backend that stalls until each request's read timeout runs out -- every request, or only ``slow``'s."""

    def handle(request: httpx.Request) -> httpx.Response:
        read = request.extensions["timeout"]["read"]
        seen.append(read)
        if slow is None or slow in request.content.decode():
            clock.now += read
            raise httpx.ReadTimeout("read timed out", request=request)
        return httpx.Response(200, json={})

    return httpx.MockTransport(handle)


def _hangs(*, slow: str | None = None) -> httpx.MockTransport:
    """Async: a backend that never answers -- every request, or only ``slow``'s."""

    async def handle(request: httpx.Request) -> httpx.Response:
        if slow is None or slow in request.content.decode():
            await asyncio.sleep(30)
        return httpx.Response(200, json={})

    return httpx.MockTransport(handle)


def _call(method: str, transport: httpx.MockTransport) -> Any:
    if method == "search":
        return _Acme(transport=transport).search(SearchRequest(query="q"))
    if method == "answer":
        return _AcmeAnswer(transport=transport).answer(AnswerRequest(query="q"))
    return _AcmeExtract(transport=transport).extract(ExtractRequest(urls=URLS[:1]))


async def _acall(method: str, transport: httpx.MockTransport) -> Any:
    if method == "search":
        return await _Acme(transport=transport).asearch(SearchRequest(query="q"))
    if method == "answer":
        return await _AcmeAnswer(transport=transport).aanswer(AnswerRequest(query="q"))
    return await _AcmeExtract(transport=transport).aextract(ExtractRequest(urls=URLS[:1]))
