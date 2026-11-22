"""``prefer`` and the deciders: the question carries it, and each built-in model router sends it on.

The question's instructions name the preference, which every decider reads;
``llm`` also lists it beside the budget, and ``systemone`` puts it in the
state. Unset, each sends exactly what it sent before ``prefer`` existed.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from tests.decider_helpers import Fixed, chat, gemini, reply, request, serve, systemone
from web_router import Candidate, DeciderRouter
from web_router.constants_decider import DECIDER_INSTRUCTIONS, DECIDER_PREFER
from web_router.routing.decider_gemini import gemini_router
from web_router.routing.decider_llm import llm_router
from web_router.routing.decider_question import question_for
from web_router.routing.decider_systemone import systemone_router

LOCAL = {"base_url": "http://localhost:9000", "model": "m"}
RANKED = {"ranked": ["search:exa"], "scores": [{"id": "search:exa", "p": 0.9}], "confidence": 0.7}
MODES = (
    Candidate(tool="search", provider="tavily", est_cost_usd=0.008),
    Candidate(tool="search", provider="exa", est_cost_usd=0.005),
    Candidate(tool="search", provider="exa", mode="exa-instant", est_cost_usd=0.005),
    Candidate(tool="search", provider="exa", mode="exa-deep", est_cost_usd=0.015),
)


@pytest.mark.parametrize(("prefer", "ranks_by"), [("cheap", "lower cost first"), ("fast", "lower p90 latency first")])
def test_the_question_carries_the_preference_and_says_it_in_the_instructions(prefer: Any, ranks_by: str) -> None:
    question = question_for(replace(request(), prefer=prefer))
    assert question.prefer == prefer
    added = question.instructions.removeprefix(DECIDER_INSTRUCTIONS)
    assert f"prefers {prefer}" in added and ranks_by in added


def test_unset_the_question_is_the_one_asked_before() -> None:
    question = question_for(request())
    assert (question.prefer, question.instructions) == (None, DECIDER_INSTRUCTIONS)


def test_a_decider_of_your_own_is_asked_with_the_preference() -> None:
    decider = Fixed(reply("search:exa"))
    DeciderRouter(decider).route(replace(request(), prefer="fast"))
    assert [question.prefer for question in decider.asked] == ["fast"]


async def test_the_llm_router_lists_the_preference_and_states_it(monkeypatch: pytest.MonkeyPatch) -> None:
    server = serve(monkeypatch, chat(RANKED))
    await llm_router(**LOCAL).aroute(replace(request(), prefer="cheap"))
    system, user = (message["content"] for message in server.body()["messages"])
    assert DECIDER_PREFER["cheap"] in system
    assert "\nPrefer: cheap\n" in user
    await llm_router(**LOCAL).aroute(request())
    assert "Prefer:" not in server.body()["messages"][1]["content"], "unset: no line"


async def test_the_systemone_router_puts_the_preference_in_the_state(monkeypatch: pytest.MonkeyPatch) -> None:
    server = serve(monkeypatch, systemone("search:exa"))
    await systemone_router(**LOCAL).aroute(replace(request("who won"), prefer="fast"))
    body = server.body()
    assert body["state"] == {"query": "who won", "prefer": "fast"}
    assert body["questions"]["route"]["instructions"].endswith(DECIDER_PREFER["fast"])
    await systemone_router(**LOCAL).aroute(request("who won"))
    assert server.body()["state"] == {"query": "who won"}, "unset: the state it sent before"


async def test_the_gemini_router_states_the_preference_in_both_questions(monkeypatch: pytest.MonkeyPatch) -> None:
    server = serve(monkeypatch, gemini("search:exa", "search:exa:exa-deep"))
    await gemini_router(**LOCAL).aroute(replace(request(candidates=MODES), prefer="cheap"))
    questions = server.body()["questions"]
    assert all(DECIDER_PREFER["cheap"] in questions[key]["instructions"] for key in ("backend", "mode"))
