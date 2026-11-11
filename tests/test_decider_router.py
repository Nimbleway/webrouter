"""DeciderRouter: the question it asks, the reply check, the decision, its time limit and closing its decider."""

from __future__ import annotations

import asyncio
import math
from typing import Any

import pytest

from tests.decider_helpers import CANDIDATES, EXTRACT, AFixed, Fixed, reply, request
from web_router import DeciderQuestion, DeciderReply, DeciderRouter, RouteDecision, WebRouter
from web_router.exceptions import ConfigurationError
from web_router.routing.invoke import AttachedRouter

OFFERED = [c.id for c in CANDIDATES]


@pytest.mark.parametrize("decider", [Fixed, AFixed], ids=["sync", "async"])
@pytest.mark.parametrize("from_async", [False, True], ids=["from-sync", "from-async"])
def test_the_ranking_becomes_tools_and_providers(decider: Any, from_async: bool) -> None:
    answer = reply("answer:perplexity", "search:exa", "search:tavily", scores={"answer:perplexity": 0.41})
    attached = AttachedRouter(DeciderRouter(decider(answer, timeout_s=1.0)))
    outcome = asyncio.run(attached.acall(request())) if from_async else attached.call(request())
    assert outcome.decision == RouteDecision(
        tools=["answer", "search"],
        providers={"answer": ["perplexity"], "search": ["exa", "tavily"]},
        reason=f"{decider.name}: answer:perplexity, p=0.41",
    )


def test_the_question_offers_each_candidate_described_and_extract_only_with_a_url() -> None:
    decider = Fixed(reply())
    router = DeciderRouter(decider)
    router.route(request("who won", candidates=(*CANDIDATES, EXTRACT)))
    router.route(request("read it", candidates=(*CANDIDATES, EXTRACT), urls=("https://example.com/a",)))
    without, with_url = decider.asked
    assert without.prompt == "who won"
    assert list(without.options) == OFFERED
    assert without.options["search:tavily"] == "search on tavily, $0.008, p90 1.2s"
    assert without.options["search:exa"] == "search on exa, unknown cost, unknown latency"
    assert "extract:jina" in with_url.options


def test_with_nothing_to_offer_the_decider_is_not_asked() -> None:
    decider = Fixed(reply("extract:jina"))
    assert DeciderRouter(decider).route(request(candidates=(EXTRACT,))) == RouteDecision()
    assert decider.asked == []


def test_an_empty_ranking_is_no_opinion() -> None:
    assert DeciderRouter(Fixed(reply(confidence=0.2))).route(request()) == RouteDecision(confidence=0.2)


BAD_REPLIES = {
    "not-a-reply": {"ranked": ["search:exa"]},
    "string-ranking": DeciderReply(ranked="search:exa"),
    "unknown-id": reply("search:nope"),
    "extract-not-offered": reply("extract:jina"),
    "duplicate": reply("search:exa", "search:exa"),
    "str-subclass": reply(type("Id", (str,), {})("search:exa")),
    "non-str-id": DeciderReply(ranked=[1]),  # type: ignore[list-item]
    "score-unranked": reply("search:exa", scores={"search:tavily": 0.5}),
    "score-over-1": reply("search:exa", scores={"search:exa": 1.5}),
    "score-nan": reply("search:exa", scores={"search:exa": math.nan}),
    "score-bool": reply("search:exa", scores={"search:exa": True}),
    "score-text": reply("search:exa", scores={"search:exa": "0.5"}),  # type: ignore[dict-item]
    "scores-list": DeciderReply(ranked=["search:exa"], scores=[0.5]),  # type: ignore[arg-type]
    "confidence-negative": reply("search:exa", confidence=-0.1),
}


@pytest.mark.parametrize("bad", list(BAD_REPLIES.values()), ids=list(BAD_REPLIES))
def test_a_reply_that_does_not_answer_the_question_is_invalid(bad: Any) -> None:
    outcome = AttachedRouter(DeciderRouter(Fixed(bad))).call(request())
    assert outcome.failure == "router_invalid"


def test_the_invalid_reply_is_never_echoed() -> None:
    secret = "search:" + "x" * 20
    with pytest.raises(Exception) as caught:
        DeciderRouter(Fixed(reply(secret))).route(request())
    assert secret not in str(caught.value)


class _First:
    """An async decider that ranks the first option it is offered, after ``sleep_s``."""

    name = "first"

    def __init__(self, sleep_s: float, timeout_s: float) -> None:
        self.sleep_s, self.timeout_s = sleep_s, timeout_s

    async def adecide(self, question: DeciderQuestion) -> DeciderReply:
        await asyncio.sleep(self.sleep_s)
        return reply(next(iter(question.options)))


def test_a_decider_slower_than_50_ms_within_its_timeout_is_counted() -> None:
    with WebRouter(router=DeciderRouter(_First(sleep_s=0.2, timeout_s=2.0))) as client:
        plan = client.plan("who won the 2022 world cup")
    assert plan.decision.reason is not None and plan.decision.reason.startswith("first: ")


def test_one_past_its_timeout_leaves_the_static_order() -> None:
    with WebRouter(router=DeciderRouter(_First(sleep_s=0.3, timeout_s=0.05))) as client:
        plan = client.plan("who won the 2022 world cup")
    assert plan.decision.router_timeout is True


def test_the_router_declares_the_deciders_name_timeout_and_route_tools() -> None:
    decider = Fixed(reply(), timeout_s=1.5)
    decider.route_tools = ("research",)  # type: ignore[attr-defined]
    router = DeciderRouter(decider)
    assert (router.router_name, router.timeout_s, router.route_tools) == ("fixed", 1.5, ("research",))
    assert not hasattr(DeciderRouter(AFixed(reply())), "route")  # async only: no sync side to call


def test_a_decider_with_neither_method_is_refused() -> None:
    with pytest.raises(ConfigurationError, match="decide"):
        DeciderRouter(object())  # type: ignore[arg-type]


def test_close_reaches_a_decider_it_owns_and_never_one_it_does_not() -> None:
    owned, kept = Fixed(reply()), Fixed(reply())
    DeciderRouter(owned).close()
    DeciderRouter(kept, owns_decider=False).close()
    assert (owned.closed, kept.closed) == (1, 0)


async def test_aclose_reaches_an_async_decider_and_close_runs_it_from_sync_code() -> None:
    owned = AFixed(reply())
    await DeciderRouter(owned).aclose()
    await asyncio.to_thread(DeciderRouter(owned).close)
    assert owned.aclosed == 2


class _Decorated:
    """A decider whose ``decide`` looks sync but answers with a coroutine, as an async one behind a decorator."""

    name = "decorated"
    timeout_s = 1.0

    def decide(self, question: DeciderQuestion) -> Any:
        async def answer() -> DeciderReply:
            return reply("search:exa")

        return answer()


@pytest.mark.parametrize("from_async", [False, True], ids=["from-sync", "from-async"])
def test_a_decide_that_answers_with_a_coroutine_is_awaited(from_async: bool) -> None:
    attached = AttachedRouter(DeciderRouter(_Decorated()))
    outcome = asyncio.run(attached.acall(request())) if from_async else attached.call(request())
    assert outcome.decision is not None and outcome.decision.reason == "decorated: search:exa"
