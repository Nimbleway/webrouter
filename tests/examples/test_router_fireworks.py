"""examples/router_fireworks.py on httpx's mock transport: no key, no network."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from tests.examples.helpers import CANDIDATES, load_example, request
from web_router import WebRouter

fireworks = load_example("router_fireworks")


def _replying(prefer: str, seen: list[httpx.Request], *, confidence: float = 0.6, total: float = 1.0) -> Any:
    """A System One that gives ``prefer`` 0.7 of ``total`` and splits the rest over the other choices asked."""

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        asked = list(json.loads(req.content)["questions"]["route"]["criteria"])
        rest = 0.3 / max(len(asked) - 1, 1)
        probabilities = {cid: total * (0.7 if cid == prefer else rest) for cid in asked}
        answer = {"type": "choice", "choice": prefer, "confidence": confidence, "probabilities": probabilities}
        return httpx.Response(200, json={"answers": {"route": answer}, "usage": {"input_tokens": 1}})

    return handler


def _router(prefer: str, seen: list[httpx.Request] | None = None, **reply: float) -> Any:
    transport = httpx.MockTransport(_replying(prefer, [] if seen is None else seen, **reply))
    return fireworks.FireworksRouter(client=httpx.AsyncClient(base_url=fireworks.BASE_URL, transport=transport))


class TestFireworksRouter:
    async def test_posts_one_choice_question_to_system_one_on_rune(self) -> None:
        seen: list[httpx.Request] = []
        await _router("search:tavily", seen).aroute(request("who won"))
        assert seen[0].url.path == "/inference/v1/systemone"
        body = json.loads(seen[0].content)
        assert body["model"] == "accounts/fireworks/models/rune"
        assert body["state"] == {"query": "who won"}
        assert list(body["questions"]["route"]["criteria"]) == [c.id for c in CANDIDATES]

    async def test_ranks_by_the_returned_probabilities(self) -> None:
        decision = await _router("answer:perplexity").aroute(request())
        assert decision.tools == ["answer", "search", "research"]
        assert decision.providers["search"] == ["tavily", "exa"]
        assert decision.reason == "fireworks: answer:perplexity, p=0.70"

    def test_below_the_confidence_floor_the_static_order_runs(self) -> None:
        with WebRouter(router=_router("answer:perplexity", confidence=0.1)) as client:
            plan = client.plan("who won the 2022 world cup")
        assert plan.tool == "search", "an unsure router changed the method"
        assert plan.decision.reason == "router_empty"

    async def test_probabilities_that_do_not_sum_to_1_raise(self) -> None:
        with pytest.raises(ValueError, match="sum to 1"):
            await _router("search:tavily", total=0.5).aroute(request())

    def test_a_bad_reply_leaves_the_static_order(self) -> None:
        with WebRouter(router=_router("search:tavily", total=0.5)) as client:
            plan = client.plan("who won the 2022 world cup")
        assert plan.decision.reason == "router_error"

    async def test_its_own_client_carries_the_key_and_is_closed_after_the_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        opened: list[httpx.AsyncClient] = []
        real = httpx.AsyncClient

        def client(**kwargs: Any) -> httpx.AsyncClient:
            opened.append(real(transport=httpx.MockTransport(_replying("search:exa", [])), **kwargs))
            return opened[-1]

        monkeypatch.setattr(fireworks.httpx, "AsyncClient", client)
        await fireworks.FireworksRouter(api_key="test-token").aroute(request())
        assert opened[0].is_closed
        assert opened[0].headers["Authorization"] == "Bearer test-token"

    def test_route_answers_synchronously_for_route_eval(self, monkeypatch: pytest.MonkeyPatch) -> None:
        real = httpx.AsyncClient
        transport = httpx.MockTransport(_replying("answer:perplexity", []))
        monkeypatch.setattr(fireworks.httpx, "AsyncClient", lambda **kw: real(transport=transport, **kw))
        monkeypatch.setenv("FIREWORKS_API_KEY", "test-token")
        assert fireworks.route(request()).tools == ["answer", "search", "research"]

    def test_without_a_key_it_says_what_to_set(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.delenv("FIREWORKS_API_KEY", raising=False)
        fireworks.main()
        assert "Set FIREWORKS_API_KEY" in capsys.readouterr().out
