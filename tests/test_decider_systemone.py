"""The built-in ``systemone`` router on httpx's mock transport: both presets, the ranking, and its setup checks."""

from __future__ import annotations

import pytest

from tests.decider_helpers import CANDIDATES, TOKEN, request, respond, serve, systemone
from web_router import Budget, RouteRequest, WebRouter
from web_router.exceptions import ConfigurationError
from web_router.routing.decider_systemone import systemone_router
from web_router.routing.invoke import AttachedRouter

PRESETS = {
    "typesafe": ("https://api.typesafe.ai/v1/systemone", "jev-1.14.0", "TYPESAFE_API_KEY"),
    "fireworks": (
        "https://api.fireworks.ai/inference/v1/systemone",
        "accounts/fireworks/models/rune",
        "FIREWORKS_API_KEY",
    ),
}


@pytest.mark.parametrize("preset", list(PRESETS))
async def test_each_preset_asks_one_choice_question_and_ranks_by_probability(
    preset: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    url, model, env = PRESETS[preset]
    monkeypatch.setenv(env, TOKEN)
    server = serve(monkeypatch, systemone("answer:perplexity"))
    decision = await systemone_router(preset=preset).aroute(request("who won"))
    sent, body = server.requests[0], server.body()
    assert (str(sent.url), sent.headers["Authorization"]) == (url, f"Bearer {TOKEN}")
    assert body["model"] == model and body["state"] == {"query": "who won"}
    question = body["questions"]["route"]
    assert question["type"] == "choice"
    assert list(question["criteria"]) == [c.id for c in CANDIDATES]
    assert decision.tools == ["answer", "search", "research"]
    assert decision.providers is not None and decision.providers["search"] == ["tavily", "exa"]
    assert decision.confidence == 0.6
    assert decision.reason == f"systemone({sent.url.host}): answer:perplexity, p=0.70"


def test_the_apis_own_choice_is_not_used(monkeypatch: pytest.MonkeyPatch) -> None:
    probabilities = {"search:exa": 0.6, "search:tavily": 0.4}
    answer = {"choice": "search:tavily", "confidence": 0.5, "probabilities": probabilities}
    serve(monkeypatch, lambda req: respond({"answers": {"route": answer}}))
    router = systemone_router(base_url="http://localhost:9000", model="m")
    outcome = AttachedRouter(router).call(request())
    assert outcome.decision is not None and outcome.decision.providers == {"search": ["exa", "tavily"]}


@pytest.mark.parametrize(
    "answers",
    [
        {"route": {"probabilities": {"search:exa": 1.2}, "confidence": 0.5}},
        {"route": {"probabilities": {"search:nope": 0.5}, "confidence": 0.5}},
        {"route": {"probabilities": {"search:exa": "high"}, "confidence": 0.5}},
        {"route": {"probabilities": {"search:exa": 0.5}, "confidence": 2}},
        {"route": {"probabilities": {"search:exa": 10**400, "search:tavily": 0.5}, "confidence": 0.5}},
        {"route": {"probabilities": ["search:exa"]}},
        {"other": {}},
    ],
    ids=["over-1", "unknown-id", "text", "confidence", "huge-int", "list", "no-route"],
)
def test_a_reply_out_of_shape_is_invalid(answers: object, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, lambda req: respond({"answers": answers}))
    outcome = AttachedRouter(systemone_router(base_url="http://localhost:9000", model="m")).call(request())
    assert outcome.failure == "router_invalid"


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"preset": "typesafe"}, "TYPESAFE_API_KEY is not set"),
        ({"base_url": "https://so.example.com", "model": "m"}, "api_key_env"),
        ({"base_url": "https://so.example.com", "api_key": TOKEN}, "router_config\\['model'\\]"),
        ({"base_url": "https://so.example.com", "model": "m", "api_key_env": "FIREWORKS_API_KEY"}, "sent only to"),
        ({"base_url": "https://so.example.com", "model": "m", "api_key": TOKEN, "path": "/x?k=v"}, "query"),
        ({"preset": "jev"}, "has no preset 'jev'"),
    ],
)
def test_a_bad_setup_is_refused_when_the_router_is_built(
    config: dict[str, str], message: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setenv("FIREWORKS_API_KEY", TOKEN)
    with pytest.raises(ConfigurationError, match=message) as caught:
        systemone_router(**config)
    assert TOKEN not in str(caught.value)


def test_a_key_the_decider_does_not_take_is_refused_by_the_decider() -> None:
    with pytest.raises(TypeError, match="bogus"):
        systemone_router(preset="fireworks", bogus=1)


def test_by_name_it_orders_every_tool_but_search_unless_told(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIREWORKS_API_KEY", TOKEN)
    with WebRouter(router="systemone", router_config={"preset": "fireworks"}) as client:
        assert client._route_tools == {"answer", "extract", "research", "browse"}
    config = {"preset": "fireworks", "route_tools": ("search",)}
    with WebRouter(router="systemone", router_config=config) as client:
        assert client._route_tools == {"search"}


async def test_the_budget_rides_in_the_state(monkeypatch: pytest.MonkeyPatch) -> None:
    server = serve(monkeypatch, systemone("search:exa"))
    budgeted = RouteRequest(prompt="q", candidates=CANDIDATES, deadline_s=1.0, budget=Budget(max_cost_usd=0.05))
    await systemone_router(base_url="http://localhost:9000", model="m").aroute(budgeted)
    assert server.body()["state"] == {"query": "q", "budget": {"max_cost_usd": 0.05, "strict": False}}
