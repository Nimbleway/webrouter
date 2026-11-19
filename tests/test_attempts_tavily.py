"""A Tavily mode the SDK refuses reads as refused: its SDK's errors carry no status, so a bad request stands for 400.

Offline: Tavily's SDK client is faked; nothing is sent, and no key is real.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("tavily", reason="tavily extra not installed")

from tavily import TavilyClient
from tavily.errors import BadRequestError, UsageLimitExceededError

from tests.candidates_helpers import Shown, clear_keys, set_keys
from web_router import AllProvidersFailedError, Budget, RouteDecision, WebRouter

FAST, ULTRA = "search:tavily:tavily-fast", "search:tavily:tavily-ultra-fast"


def _tavily(monkeypatch: pytest.MonkeyPatch, *replies: Exception) -> list[Any]:
    """Key Tavily and fake its SDK: each search raises the next of ``replies``, then answers; the depths sent."""
    set_keys(clear_keys(monkeypatch), "tavily")
    sent: list[Any] = []
    queue = list(replies)

    def search(self: Any, **fields: Any) -> dict[str, Any]:
        sent.append(fields.get("search_depth"))
        if queue:
            raise queue.pop(0)
        return {"results": [{"url": "https://t.example/", "title": "T", "content": "c"}]}

    monkeypatch.setattr(TavilyClient, "search", search)
    return sent


def _routed() -> WebRouter:
    router = Shown(RouteDecision(attempts=[FAST, ULTRA]), route_modes=True)
    return WebRouter(provider_config={"auto": {"chain": {"search": ["tavily"]}}}, router=router)


def test_a_mode_the_sdk_refuses_is_taken_back_and_the_next_mode_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = _tavily(monkeypatch, BadRequestError("bad mode"))
    with _routed() as wr:
        response = wr.search("q", budget=Budget(max_cost_usd=1.0))
    record = response.decision
    assert sent == ["fast", "ultra-fast"] and record is not None
    assert [(hop.id, hop.error) for hop in record.hops or ()] == [(FAST, "400")], "refused, not backend_failed"
    assert record.estimated_cost_usd == pytest.approx(response.cost_usd or 0), "only the mode that ran is charged"


def test_a_usage_limit_still_stops_the_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = _tavily(monkeypatch, UsageLimitExceededError("Too many requests"))
    with _routed() as wr, pytest.raises(AllProvidersFailedError):
        wr.search("q")
    assert sent == ["fast"]
