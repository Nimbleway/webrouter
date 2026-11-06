"""The remote example routers, router_llm.py and router_jev.py, on fake clients: no key, no network."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from tests.examples.helpers import load_example, request
from web_router import WebRouter

llm = load_example("router_llm")
jev = load_example("router_jev")


class _Completions:
    """``chat.completions`` that records each request and answers with ``answer(request)``."""

    def __init__(self, answer: Any) -> None:
        self.answer = answer
        self.requests: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        message = SimpleNamespace(content=json.dumps(self.answer(kwargs)))
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _llm(answer: Any) -> tuple[Any, _Completions]:
    completions = _Completions(answer)
    return llm.LLMRouter(client=SimpleNamespace(chat=SimpleNamespace(completions=completions)), model="m"), completions


def _enum(kwargs: dict[str, Any]) -> list[str]:
    return list(kwargs["response_format"]["json_schema"]["schema"]["properties"]["ranked"]["items"]["enum"])


def _searches(kwargs: dict[str, Any]) -> list[str]:
    return [cid for cid in _enum(kwargs) if cid.startswith("search:")]


class TestLLM:
    async def test_the_schema_allows_only_the_candidate_ids(self) -> None:
        router, completions = _llm(lambda _: {"ranked": [], "reason": "r"})
        await router.aroute(request())
        sent = completions.requests[0]
        assert _enum(sent) == ["search:tavily", "search:exa", "answer:perplexity", "research:parallel"]
        assert sent["response_format"]["json_schema"]["strict"] is True
        assert sent["model"] == "m"

    async def test_sends_the_prompt_the_budget_and_the_menu(self) -> None:
        router, completions = _llm(lambda _: {"ranked": [], "reason": "r"})
        await router.aroute(request("who won"))
        user = completions.requests[0]["messages"][-1]["content"]
        assert "who won" in user
        assert "Budget:" in user
        assert "- search:tavily: cost $0.008, p90 1.2s" in user
        assert "- search:exa: cost unknown, p90 unknown" in user

    async def test_the_ranking_becomes_tools_and_providers(self) -> None:
        ranked = ["answer:perplexity", "search:exa", "search:tavily", "answer:unknown"]
        router, _ = _llm(lambda _: {"ranked": ranked, "reason": "a fact"})
        decision = await router.aroute(request())
        assert decision.tools == ["answer", "search"]
        assert decision.providers == {"answer": ["perplexity"], "search": ["exa", "tavily"]}
        assert decision.reason == "a fact"

    def test_declares_its_time_limit(self) -> None:
        assert llm.LLMRouter.timeout_s == 3.0

    def test_the_model_comes_from_the_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_ROUTER_MODEL", "from-env")
        assert llm.LLMRouter(client=object()).model == "from-env"

    def test_plans_through_the_router(self) -> None:
        router, _ = _llm(lambda kwargs: {"ranked": _searches(kwargs)[::-1], "reason": "reversed"})
        with WebRouter(router=router) as client:
            plan = client.plan("who won the 2022 world cup")
        offered = [cid.split(":")[1] for cid in plan.decision.offered or () if cid.startswith("search:")]
        assert plan.tool == "search"
        assert list(plan.providers["search"]) == offered[::-1]
        assert plan.decision.reason == "reversed"

    def test_without_a_key_it_says_what_to_set(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("LLM_ROUTER_MODEL", raising=False)
        llm.main()
        assert "Set OPENAI_API_KEY and LLM_ROUTER_MODEL" in capsys.readouterr().out


def _jev(probabilities: dict[str, float], seen: list[httpx.Request]) -> Any:
    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        answer = {"choice": max(probabilities, key=probabilities.__getitem__), "confidence": 0.6}
        return httpx.Response(200, json={"answers": {"route": {**answer, "probabilities": probabilities}}})

    client = httpx.AsyncClient(base_url=jev.BASE_URL, transport=httpx.MockTransport(handler))
    return jev.JevRouter(client=client)


class TestJev:
    async def test_posts_one_choice_question_over_the_candidates(self) -> None:
        seen: list[httpx.Request] = []
        await _jev({"search:tavily": 1.0}, seen).aroute(request("who won"))
        assert seen[0].url.path == "/v1/systemone"
        body = json.loads(seen[0].content)
        assert body["state"] == {"query": "who won"}
        assert body["model"] == jev.MODEL
        question = body["questions"]["route"]
        assert question["type"] == "choice"
        assert list(question["criteria"]) == [c.id for c in request().candidates]
        assert question["criteria"]["search:tavily"] == "search on tavily, $0.008, p90 1.2s"

    async def test_ranks_by_the_returned_probabilities(self) -> None:
        probabilities = {"research:parallel": 0.1, "search:exa": 0.5, "answer:perplexity": 0.3, "search:tavily": 0.1}
        decision = await _jev(probabilities, []).aroute(request())
        assert decision.tools == ["search", "answer", "research"]
        assert decision.providers["search"] == ["exa", "tavily"]
        assert decision.confidence == 0.6
        assert decision.reason == "jev: search:exa"

    async def test_an_http_error_raises_so_the_static_order_runs(self) -> None:
        client = httpx.AsyncClient(base_url=jev.BASE_URL, transport=httpx.MockTransport(lambda _: httpx.Response(500)))
        with pytest.raises(httpx.HTTPStatusError):
            await jev.JevRouter(client=client).aroute(request())

    def test_the_key_goes_in_the_bearer_header(self) -> None:
        router = jev.JevRouter(api_key="test-token")
        assert router.client.headers["Authorization"] == "Bearer test-token"
        assert str(router.client.base_url) == jev.BASE_URL

    def test_declares_its_time_limit(self) -> None:
        assert jev.JevRouter.timeout_s == 1.0

    def test_plans_through_the_router(self) -> None:
        with WebRouter(router=_jev({"extract:jina": 0.9}, [])) as client:
            plan = client.plan("read https://example.com/a")
        assert plan.tool == "extract"
        assert plan.providers["extract"][0] == "jina"

    def test_without_a_key_it_says_what_to_set(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
        jev.main()
        assert "Set TYPESAFE_API_KEY" in capsys.readouterr().out
