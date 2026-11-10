"""examples/router_llm.py on a fake client: no key, no network."""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace
from typing import Any

import pytest

from tests.examples.helpers import CANDIDATES, EXTRACT, load_example, request
from web_router import RouteDecision, WebRouter

llm = load_example("router_llm")


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

    def test_the_default_client_never_retries(self, monkeypatch: pytest.MonkeyPatch) -> None:
        built: dict[str, Any] = {}
        fake_openai = SimpleNamespace(AsyncOpenAI=lambda **kwargs: built.update(kwargs) or object())
        monkeypatch.setitem(sys.modules, "openai", fake_openai)
        llm.LLMRouter(model="m")
        assert built == {"max_retries": 0}

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

    def test_it_orders_research_and_browse_only_unless_the_caller_says_otherwise(self) -> None:
        router, _ = _llm(lambda kwargs: {"ranked": [], "reason": ""})
        with WebRouter(router=router) as client:
            assert client._route_tools == {"research", "browse"}
        with WebRouter(router=router, route_tools=("search",)) as client:
            assert client._route_tools == {"search"}

    def test_without_a_key_it_says_what_to_set(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("LLM_ROUTER_MODEL", raising=False)
        llm.main()
        assert "Set OPENAI_API_KEY and LLM_ROUTER_MODEL" in capsys.readouterr().out

    async def test_offers_extract_only_when_the_prompt_names_a_url(self) -> None:
        router, completions = _llm(lambda _: {"ranked": ["extract:jina"], "reason": "r"})
        no_url = await router.aroute(request(candidates=(*CANDIDATES, EXTRACT)))
        assert "extract:jina" not in _enum(completions.requests[0])
        assert no_url.tools == []  # the one id it named is not on offer: no opinion
        with_url = await router.aroute(request(candidates=(*CANDIDATES, EXTRACT), urls=("https://example.com/a",)))
        assert "extract:jina" in _enum(completions.requests[1])
        assert with_url.tools == ["extract"]

    async def test_with_nothing_to_rank_it_asks_nobody(self) -> None:
        router, completions = _llm(lambda _: {"ranked": [], "reason": "r"})
        assert await router.aroute(request(candidates=(EXTRACT,))) == RouteDecision()
        assert completions.requests == []

    def test_prints_the_model_reason_without_control_characters(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        completions = _Completions(lambda kwargs: {"ranked": _searches(kwargs), "reason": "ok\x1b[2J\x07 done"})
        client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(AsyncOpenAI=lambda **_: client))
        monkeypatch.setenv("OPENAI_API_KEY", "test-token")
        monkeypatch.setenv("LLM_ROUTER_MODEL", "m")
        llm.main()
        out = capsys.readouterr().out
        assert "(ok[2J done)" in out
        assert "\x1b" not in out and "\x07" not in out

    async def test_aclose_closes_the_client_it_opened_and_never_yours(self, monkeypatch: pytest.MonkeyPatch) -> None:
        closed: list[str] = []

        class _Client:
            def __init__(self, name: str) -> None:
                self.name = name

            async def close(self) -> None:
                closed.append(self.name)

        monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(AsyncOpenAI=lambda **_: _Client("opened")))
        await llm.LLMRouter(model="m").aclose()
        await llm.LLMRouter(client=_Client("yours"), model="m").aclose()
        assert closed == ["opened"]
