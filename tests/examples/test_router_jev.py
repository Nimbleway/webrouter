"""examples/router_jev.py on httpx's mock transport: no key, no network."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from tests.examples.helpers import CANDIDATES, EXTRACT, load_example, request
from web_router import WebRouter

jev = load_example("router_jev")


def _replying(prefer: str, seen: list[httpx.Request], *, drop: str | None = None) -> Any:
    """A Jev that gives ``prefer`` 0.7 and the other choices it was asked the rest, leaving out ``drop``."""

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        asked = list(json.loads(req.content)["questions"]["route"]["criteria"])
        rest = 0.3 / max(len(asked) - 1, 1)
        probabilities = {cid: 0.7 if cid == prefer else rest for cid in asked if cid != drop}
        answer = {"choice": prefer, "confidence": 0.6, "probabilities": probabilities}
        return httpx.Response(200, json={"answers": {"route": answer}})

    return handler


def _jev(prefer: str, seen: list[httpx.Request] | None = None, *, drop: str | None = None) -> Any:
    transport = httpx.MockTransport(_replying(prefer, [] if seen is None else seen, drop=drop))
    return jev.JevRouter(client=httpx.AsyncClient(base_url=jev.BASE_URL, transport=transport))


class TestJev:
    async def test_posts_one_choice_question_over_the_candidates(self) -> None:
        seen: list[httpx.Request] = []
        await _jev("search:tavily", seen).aroute(request("who won"))
        assert seen[0].url.path == "/v1/systemone"
        body = json.loads(seen[0].content)
        assert body["state"] == {"query": "who won"}
        assert body["model"] == jev.MODEL
        question = body["questions"]["route"]
        assert question["type"] == "choice"
        assert list(question["criteria"]) == [c.id for c in CANDIDATES]
        assert question["criteria"]["search:tavily"] == "search on tavily, $0.008, p90 1.2s"

    async def test_ranks_by_the_returned_probabilities(self) -> None:
        decision = await _jev("answer:perplexity").aroute(request())
        assert decision.tools == ["answer", "search", "research"]
        assert decision.providers["search"] == ["tavily", "exa"]
        assert decision.confidence == 0.6
        assert decision.reason == "jev: answer:perplexity"

    async def test_offers_extract_only_when_the_prompt_names_a_url(self) -> None:
        seen: list[httpx.Request] = []
        router = _jev("extract:jina", seen)
        with pytest.raises(ValueError):  # Jev named a choice it was not asked
            await router.aroute(request(candidates=(*CANDIDATES, EXTRACT)))
        assert "extract:jina" not in json.loads(seen[0].content)["questions"]["route"]["criteria"]
        decision = await router.aroute(request(candidates=(*CANDIDATES, EXTRACT), urls=("https://example.com/a",)))
        assert decision.tools[0] == "extract"

    async def test_with_nothing_to_rank_it_asks_nobody(self) -> None:
        seen: list[httpx.Request] = []
        assert (await _jev("extract:jina", seen).aroute(request(candidates=(EXTRACT,)))).tools is None
        assert seen == []

    async def test_a_reply_missing_a_probability_raises(self) -> None:
        with pytest.raises(ValueError, match="incomplete"):
            await _jev("search:tavily", drop="search:exa").aroute(request())

    @pytest.mark.parametrize(
        ("bad", "confidence"),
        [(float("nan"), 0.6), (float("inf"), 0.6), (1.5, 0.6), (-0.1, 0.6), (0.5, float("nan")), (0.5, 2.0)],
    )
    def test_a_number_outside_0_to_1_raises(self, bad: float, confidence: float) -> None:
        choices = {"search:tavily": "", "search:exa": ""}
        answer = {"choice": "search:tavily", "confidence": confidence}
        with pytest.raises(ValueError, match="outside 0 to 1"):
            jev.probabilities({**answer, "probabilities": {"search:tavily": 0.5, "search:exa": bad}}, choices)

    async def test_an_out_of_range_reply_through_the_router_raises(self) -> None:
        reply = {"choice": "search:tavily", "confidence": 0.6, "probabilities": {c.id: 7.0 for c in CANDIDATES}}
        transport = httpx.MockTransport(lambda _: httpx.Response(200, json={"answers": {"route": reply}}))
        router = jev.JevRouter(client=httpx.AsyncClient(base_url=jev.BASE_URL, transport=transport))
        with pytest.raises(ValueError):
            await router.aroute(request())

    def test_an_incomplete_reply_leaves_the_static_order(self) -> None:
        with WebRouter(router=_jev("search:keenable", drop="search:keenable")) as client:
            plan = client.plan("who won the 2022 world cup")
        assert plan.decision.reason == "router_error"
        assert plan.decision.ranked is None

    async def test_an_http_error_raises_so_the_static_order_runs(self) -> None:
        client = httpx.AsyncClient(base_url=jev.BASE_URL, transport=httpx.MockTransport(lambda _: httpx.Response(500)))
        with pytest.raises(httpx.HTTPStatusError):
            await jev.JevRouter(client=client).aroute(request())

    async def test_its_own_client_carries_the_key_and_is_closed_after_each_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        opened: list[httpx.AsyncClient] = []
        real = httpx.AsyncClient

        def client(**kwargs: Any) -> httpx.AsyncClient:
            opened.append(real(transport=httpx.MockTransport(_replying("search:exa", [])), **kwargs))
            return opened[-1]

        monkeypatch.setattr(jev.httpx, "AsyncClient", client)
        router = jev.JevRouter(api_key="test-token")
        await router.aroute(request())
        await router.aroute(request())
        assert len(opened) == 2
        assert all(c.is_closed for c in opened)
        assert opened[0].headers["Authorization"] == "Bearer test-token"
        assert str(opened[0].base_url).rstrip("/") == jev.BASE_URL

    async def test_a_client_passed_in_is_left_open(self) -> None:
        router = _jev("search:exa")
        await router.aroute(request())
        assert not router.client.is_closed
        await router.client.aclose()

    def test_declares_its_time_limit(self) -> None:
        assert jev.JevRouter.timeout_s == 1.0

    def test_plans_through_the_router(self) -> None:
        with WebRouter(router=_jev("extract:jina")) as client:
            plan = client.plan("read https://example.com/a")
        assert plan.tool == "extract"
        assert plan.providers["extract"][0] == "jina"

    def test_without_a_key_it_says_what_to_set(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
        jev.main()
        assert "Set TYPESAFE_API_KEY" in capsys.readouterr().out
