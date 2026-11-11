"""examples/router_openai_decisions.py on httpx's mock transport: no key, no network."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from tests.examples.helpers import CANDIDATES, EXTRACT, load_example, request
from web_router import DeciderRouter
from web_router.routing.decider_reply import DeciderReplyError

decisions = load_example("router_openai_decisions")
TOKEN = "test-token-not-real"


def _router(answer: Any, seen: list[httpx.Request]) -> DeciderRouter:
    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"model": decisions.MODEL, "answers": [answer]})

    return DeciderRouter(decisions.OpenAIDecisions(api_key=TOKEN, transport=httpx.MockTransport(handler)))


def _choice(probabilities: dict[str, float]) -> dict[str, Any]:
    spread = [{"value": cid, "probability": p} for cid, p in probabilities.items()]
    return {"type": "choice", "name": "route", "choice": "x", "probabilities": spread, "confidence": 0.4}


async def test_it_asks_one_choice_question_over_the_options() -> None:
    seen: list[httpx.Request] = []
    await _router(_choice({"search:exa": 1.0}), seen).aroute(request("who won"))
    assert str(seen[0].url) == decisions.URL and seen[0].headers["authorization"] == f"Bearer {TOKEN}"
    body = json.loads(seen[0].content)
    assert (body["model"], body["input"]) == (decisions.MODEL, "who won")
    (question,) = body["questions"]
    assert question["type"] == "choice"
    assert [c["value"] for c in question["choices"]] == [c.id for c in CANDIDATES]
    assert all(c["description"] for c in question["choices"])


async def test_it_ranks_by_the_probabilities() -> None:
    answer = _choice({"search:tavily": 0.2, "answer:perplexity": 0.7, "search:exa": 0.1})
    decision = await _router(answer, []).aroute(request(candidates=(*CANDIDATES, EXTRACT)))
    assert decision.tools == ["answer", "search"] and decision.providers["search"] == ["tavily", "exa"]
    assert (decision.confidence, decision.reason) == (0.4, "openai-decisions: answer:perplexity, p=0.70")


async def test_a_refusal_ranks_nothing_so_the_static_order_runs() -> None:
    assert (await _router({"type": "refusal", "name": "route"}, []).aroute(request())).tools is None


async def test_an_id_it_was_not_asked_about_is_refused_by_the_reply_check() -> None:
    with pytest.raises(DeciderReplyError):
        await _router(_choice({"search:nope": 0.9}), []).aroute(request())


def test_main_says_what_to_set_without_a_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    decisions.main()
    assert "OPENAI_API_KEY" in capsys.readouterr().out
