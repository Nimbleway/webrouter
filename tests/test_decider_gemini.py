"""The built-in ``gemini`` router on httpx's mock transport: its two questions, the option limit and the ranking."""

from __future__ import annotations

import json

import httpx
import pytest

from tests.decider_helpers import TOKEN, gemini, request, respond, serve
from web_router import Candidate
from web_router.routing.decider_gemini import GeminiDecider, gemini_router
from web_router.routing.decider_question import question_for
from web_router.routing.invoke import AttachedRouter
from web_router.routing.merge import merge

URL = "https://generativelanguage.googleapis.com/v1beta/decisions"
LOCAL = {"base_url": "http://localhost:9000", "model": "m"}

MODES = (
    Candidate(tool="search", provider="tavily", est_cost_usd=0.008),
    Candidate(tool="search", provider="exa", est_cost_usd=0.005),
    Candidate(tool="search", provider="exa", mode="exa-instant", est_cost_usd=0.005),
    Candidate(tool="search", provider="exa", mode="exa-deep", est_cost_usd=0.015),
)


async def test_one_call_asks_backend_then_mode_with_the_key_in_its_own_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", TOKEN)
    server = serve(monkeypatch, gemini("search:exa", "search:exa:exa-deep"))
    decision = await gemini_router().aroute(request("who won", candidates=MODES))
    assert len(server.requests) == 1
    sent, body = server.requests[0], server.body()
    assert str(sent.url) == URL and sent.headers["x-goog-api-key"] == TOKEN
    assert "Authorization" not in sent.headers and TOKEN not in str(sent.url)
    assert body["model"] == "gemini-decision-1" and body["input"] == "who won"
    assert list(body["questions"]) == ["backend", "mode"]
    assert list(body["questions"]["backend"]["criteria"]) == ["search:tavily", "search:exa"]
    assert list(body["questions"]["mode"]["criteria"]) == ["search:exa:exa-instant", "search:exa:exa-deep"]
    assert decision.attempts == ["search:exa:exa-deep", "search:tavily", "search:exa:exa-instant"]  # 0.49, 0.3, 0.21
    assert decision.reason == f"gemini({sent.url.host}): search:exa:exa-deep, p=0.70"


async def test_without_modes_it_asks_the_backend_question_only(monkeypatch: pytest.MonkeyPatch) -> None:
    server = serve(monkeypatch, gemini("answer:perplexity"))
    decision = await gemini_router(**LOCAL).aroute(request())
    assert list(server.body()["questions"]) == ["backend"]
    assert decision.tools == ["answer", "search", "research"] and decision.confidence == 0.7


async def test_a_lone_option_is_not_asked_about(monkeypatch: pytest.MonkeyPatch) -> None:
    server = serve(monkeypatch, gemini())
    lone = (Candidate(tool="search", provider="exa"),)
    decision = await gemini_router(**LOCAL).aroute(request(candidates=lone))
    assert server.requests == [] and decision.providers == {"search": ["exa"]}


async def test_past_the_option_limit_the_first_255_are_asked_and_the_rest_follow_in_static_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    many = tuple(Candidate(tool="search", provider=f"b{i:03}") for i in range(300))
    server = serve(monkeypatch, gemini("search:b254"))
    req = request(candidates=many)
    decision = await gemini_router(**LOCAL).aroute(req)
    assert list(server.body()["questions"]["backend"]["criteria"]) == [c.id for c in many[:255]]
    assert decision.providers is not None and decision.providers["search"][:2] == ["b254", "b000"]
    assert decision.providers["search"][255:] == [c.provider for c in many[255:]], "scored 0, in the static order"
    walked = merge(req, decision, {"search": [c.provider for c in many]}).providers
    assert walked[:2] == ("b254", "b000") and walked[255:] == tuple(c.provider for c in many[255:])


async def test_past_the_option_limit_the_modes_not_asked_still_run_after_the_ranked_ones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    modes = tuple(Candidate(tool="search", provider="exa", mode=f"m{i:03}") for i in range(260))
    backends = (Candidate(tool="search", provider="tavily"), Candidate(tool="search", provider="exa"))
    server = serve(monkeypatch, gemini("search:exa", "search:exa:m001"))
    decision = await gemini_router(**LOCAL).aroute(request(candidates=backends + modes))
    assert len(server.body()["questions"]["mode"]["criteria"]) == 255
    assert decision.attempts is not None and decision.attempts[0] == "search:exa:m001"
    assert decision.attempts[-5:] == [c.id for c in modes[255:]]


async def test_options_the_api_scores_alike_keep_the_static_order(monkeypatch: pytest.MonkeyPatch) -> None:
    tied = {"search:exa": 0.5, "search:tavily": 0.5}  # the reverse of the order asked
    serve(monkeypatch, lambda req: respond({"answers": {"backend": {"probabilities": tied}}}))
    decision = await gemini_router(**LOCAL).aroute(request(candidates=MODES[:2]))
    assert decision.providers == {"search": ["tavily", "exa"]}


async def test_probabilities_that_do_not_sum_to_one_are_normalized_before_the_product(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    serve(monkeypatch, gemini("search:tavily", "search:exa:exa-instant", scale=0.5))
    decider = GeminiDecider(**LOCAL)
    reply = await decider.adecide(question_for(request(candidates=MODES)))
    assert reply.scores is not None
    assert reply.scores["search:tavily"] == pytest.approx(0.7) and reply.scores["search:exa"] == pytest.approx(0.3)
    assert sum(reply.scores[cid] for cid in ("search:exa:exa-instant", "search:exa:exa-deep")) == pytest.approx(1)
    decision = await gemini_router(**LOCAL).aroute(request(candidates=MODES))
    assert decision.attempts == ["search:tavily", "search:exa:exa-instant", "search:exa:exa-deep"]


@pytest.mark.parametrize(
    "answers",
    [
        {"backend": {"probabilities": {"search:nope": 1.0}}},
        {"backend": {"probabilities": {"search:tavily": 0.0, "search:exa": 1.5}}},
        {"backend": {"probabilities": {"search:tavily": 0.5, "search:exa": 0.5}, "confidence": 2}},
        {"mode": {"probabilities": {"search:exa:exa-deep": 1.0}}},
        {"backend": {"probabilities": {"search:exa": 1.0}}},
        {"backend": {"probabilities": {"search:tavily": 0.5, "search:exa": 0.3, "search:nope": 0.2}}},
    ],
    ids=["unknown-id", "over-1", "confidence", "no-backend-answer", "missing-one", "extra-id"],
)
def test_a_reply_out_of_shape_is_invalid(answers: object, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, lambda req: respond({"answers": answers}))
    outcome = AttachedRouter(gemini_router(**LOCAL)).call(request(candidates=MODES[:2]))
    assert outcome.failure == "router_invalid"


def _timeout(req: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("slow", request=req)


@pytest.mark.parametrize(
    ("handler", "failure"),
    [(_timeout, "router_timeout"), (lambda req: respond({}, status=503), "router_error")],
    ids=["timeout", "5xx"],
)
def test_a_timeout_or_server_error_leaves_the_static_order(
    handler: object, failure: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve(monkeypatch, handler)  # type: ignore[arg-type]
    outcome = AttachedRouter(gemini_router(**LOCAL)).call(request())
    assert outcome.failure == failure and outcome.decision is None


async def test_the_confidence_is_the_backend_questions_even_when_it_gives_none(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        sent = gemini("search:exa", "search:exa:exa-deep")(req)
        answer = json.loads(sent.read())
        answer["answers"]["backend"]["confidence"] = None
        return respond(answer)

    serve(monkeypatch, handler)
    decision = await gemini_router(**LOCAL).aroute(request(candidates=MODES))
    assert decision.confidence is None and decision.attempts


def test_a_mode_answer_with_a_confidence_out_of_range_is_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        answer = json.loads(gemini("search:exa", "search:exa:exa-deep")(req).read())
        answer["answers"]["mode"]["confidence"] = 2
        return respond(answer)

    serve(monkeypatch, handler)
    outcome = AttachedRouter(gemini_router(**LOCAL)).call(request(candidates=MODES))
    assert outcome.failure == "router_invalid"
