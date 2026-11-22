"""What the attempts tests share: a fake Exa API that answers in order, a fake Tavily, and a router naming attempts.

Offline: Exa's HTTP client and Tavily's search are replaced; nothing is sent, and no key is real.
"""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from tests.candidates_helpers import Shown, clear_keys, set_keys
from web_router import RouteDecision, WebRouter
from web_router.providers._pinning import pinned_request
from web_router.providers.exa import ExaProvider
from web_router.providers.tavily import TavilyProvider
from web_router.registry import get_provider_class
from web_router.routing.attempts import Attempt
from web_router.routing.pricing import attempt_cost
from web_router.types import SearchRequest, SearchResponse, SearchResult

CHAIN = {"auto": {"chain": {"search": ["exa", "tavily"]}}}
INSTANT, DEEP, TAVILY = "search:exa:exa-instant", "search:exa:exa-deep", "search:tavily"
HIT = {"results": [{"url": "https://a.example.com/", "title": "A"}]}

Reply = int | dict[str, Any] | Exception
"""What the fake Exa API answers one call with: a status with an error body, a 200 body, or an error raised."""


class Calls:
    """What each fake backend was sent: Exa's ``type`` per call, and Tavily's queries."""

    def __init__(self) -> None:
        self.exa: list[str | None] = []
        self.tavily: list[SearchRequest] = []


def fake_backends(monkeypatch: pytest.MonkeyPatch, *replies: Reply) -> Calls:
    """Key Exa and Tavily, and fake both: Exa answers ``replies`` in order, then hits; Tavily always hits."""
    set_keys(clear_keys(monkeypatch), "exa", "tavily")
    calls, queue = Calls(), list(replies)

    def request(method: str, path: str, **kwargs: Any) -> httpx.Response:
        calls.exa.append((kwargs.get("json") or {}).get("type"))
        reply = queue.pop(0) if queue else HIT
        sent = httpx.Request(method, "https://api.exa.ai" + path)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, int):
            return httpx.Response(reply, json={"error": "refused"}, request=sent)
        return httpx.Response(200, json=reply, request=sent)

    async def arequest(method: str, path: str, **kwargs: Any) -> httpx.Response:
        return request(method, path, **kwargs)

    def tavily(self: Any, req: SearchRequest) -> SearchResponse:
        calls.tavily.append(req)
        return SearchResponse(provider="tavily", query=req.query, results=[SearchResult(url="https://t.example/")])

    async def atavily(self: Any, req: SearchRequest) -> SearchResponse:
        return tavily(self, req)

    monkeypatch.setattr(ExaProvider, "_client", property(lambda self: SimpleNamespace(request=request)))
    monkeypatch.setattr(ExaProvider, "_async_client", property(lambda self: SimpleNamespace(request=arequest)))
    monkeypatch.setattr(TavilyProvider, "search", tavily)
    monkeypatch.setattr(TavilyProvider, "asearch", atavily)
    return calls


def routed(*attempts: str, cls: type[Any] = WebRouter, **decision: Any) -> Any:
    """A ``cls`` router over Exa then Tavily, shown modes, whose router answers ``attempts`` and any other
    ``decision``."""
    router = Shown(RouteDecision(attempts=list(attempts), **decision), route_modes=True)
    return cls(provider_config=CHAIN, router=router)


def price(mode: str, **options: Any) -> float | None:
    """What Exa in ``mode`` is estimated to cost for a search of ``q`` with ``options``."""
    exa = get_provider_class("exa")()
    request = pinned_request(exa, SearchRequest(query="q", **options), mode)
    return attempt_cost(Attempt(tool="search", provider="exa", request=request, mode=mode))


def hooked(seen: list[str]) -> dict[str, Callable[..., Any]]:
    """A route's hooks that note each attempt's id and each failure's member, as ``CallRoute`` takes them."""

    def before(attempt: Attempt) -> bool:
        seen.append(attempt.id)
        return True

    def failed(tool: str, provider: str, exc: BaseException) -> None:
        seen.append(f"failed {tool}:{provider}")

    return {"before_attempt": before, "on_failure": failed}
