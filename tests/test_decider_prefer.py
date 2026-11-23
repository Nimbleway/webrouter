"""``prefer`` and the deciders: the question carries it, and each built-in model router sends it on.

The question's instructions name the preference, which every decider reads;
``llm`` also lists it beside the budget, and ``systemone`` puts it in the
state. Unset, each sends exactly what it sent before ``prefer`` existed.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from tests.decider_helpers import AFixed, Fixed, chat, gemini, reply, request, serve, systemone
from web_router import Candidate, DeciderRouter
from web_router.constants_decider import DECIDER_INSTRUCTIONS, DECIDER_PREFER
from web_router.exceptions import ConfigurationError
from web_router.routing.accuracy import shipped_scores
from web_router.routing.decider_gemini import gemini_router
from web_router.routing.decider_llm import llm_router
from web_router.routing.decider_question import question_for
from web_router.routing.decider_systemone import systemone_router
from web_router.routing.registry import resolve_router

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


SCORES = {"search": {"exa": 0.161}, "answer": {"perplexity": 0.953}}


def test_a_router_built_with_a_preference_asks_it_and_answers_with_it() -> None:
    decider = Fixed(reply("search:exa"))
    decision = DeciderRouter(decider, prefer="fast").route(replace(request(), prefer="cheap"))
    assert [question.prefer for question in decider.asked] == ["fast"], "the router's own wins over the request's"
    assert decision.prefer == "fast"


def test_with_scores_each_option_carries_its_accuracy_and_without_them_reads_as_before() -> None:
    plain = Fixed(reply("search:exa"))
    DeciderRouter(plain).route(request())
    scored = Fixed(reply("search:exa"))
    DeciderRouter(scored, prefer="accurate", accuracy=SCORES).route(request())
    before, after = plain.asked[0].options, scored.asked[0].options
    assert after["search:exa"] == before["search:exa"] + ", accuracy 0.161"
    assert after["search:tavily"] == before["search:tavily"] + ", accuracy unknown"
    assert not any("accuracy" in text for text in before.values())
    assert (
        "prefers accurate" in scored.asked[0].instructions
        and "higher measured accuracy first" in scored.asked[0].instructions
    )


@pytest.mark.parametrize(
    "build",
    [lambda **kw: DeciderRouter(Fixed(reply()), **kw), lambda **kw: llm_router(**LOCAL, **kw)],
    ids=["decider", "llm"],
)
def test_accurate_holds_the_shipped_scores_unless_given_its_own(build: Any) -> None:
    assert build(prefer="accurate").scores == shipped_scores()
    with pytest.raises(ConfigurationError, match="holds none"):
        build(prefer="accurate", accuracy={})


async def test_built_by_name_the_llm_router_takes_its_preference_and_scores(monkeypatch: pytest.MonkeyPatch) -> None:
    server = serve(monkeypatch, chat(RANKED))
    router, _ = resolve_router("llm", {**LOCAL, "prefer": "accurate", "accuracy": SCORES})
    await router.aroute(request())  # type: ignore[union-attr]
    system, user = (message["content"] for message in server.body()["messages"])
    assert DECIDER_PREFER["accurate"] in system and "accuracy 0.161" in user


@pytest.mark.parametrize("decider", [Fixed, AFixed], ids=["sync", "async"])
async def test_unset_a_router_asked_accurate_by_a_service_shows_the_shipped_scores(decider: Any) -> None:
    fixed = decider(reply())
    router = DeciderRouter(fixed)
    if isinstance(fixed, AFixed):
        await router.aroute(replace(request(), prefer="accurate"))
        await router.aroute(request())
    else:
        router.route(replace(request(), prefer="accurate"))
        router.route(request())
    shipped = shipped_scores()
    for tool, provider in [("search", "tavily"), ("search", "exa"), ("answer", "perplexity")]:
        assert fixed.asked[0].options[f"{tool}:{provider}"].endswith(f", accuracy {shipped[tool][provider]:g}")
    assert not any("accuracy" in text for text in fixed.asked[1].options.values())
