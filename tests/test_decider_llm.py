"""The built-in ``llm`` router on httpx's mock transport: each preset's request, its key rules and its reply."""

from __future__ import annotations

import json
import socket
from typing import Any

import httpx
import pytest

from tests.decider_helpers import CANDIDATES, TOKEN, chat, request, respond, serve
from web_router import RouterError, WebRouter
from web_router.exceptions import ConfigurationError
from web_router.routing.decider_llm import LLMDecider, llm_router
from web_router.routing.invoke import AttachedRouter

OFFERED = [c.id for c in CANDIDATES]
SCORES = [{"id": "answer:perplexity", "p": 0.8}, {"id": "search:exa", "p": 0.1}]
RANKED = {"ranked": ["answer:perplexity", "search:exa"], "scores": SCORES, "confidence": 0.7}

FAILURES = (
    *("redirect", "status", "transport", "too-large", "not-json", "not-object"),
    *("unknown-id", "score-twice", "score-extra-key", "no-choice"),
)

PRESETS = {
    "openai": ("https://api.openai.com/v1/chat/completions", "OPENAI_API_KEY", "json_schema"),
    "anthropic": ("https://api.anthropic.com/v1/chat/completions", "ANTHROPIC_API_KEY", "json_schema"),
    "openrouter": ("https://openrouter.ai/api/v1/chat/completions", "OPENROUTER_API_KEY", "json_schema"),
    "deepseek": ("https://api.deepseek.com/chat/completions", "DEEPSEEK_API_KEY", "json_object"),
    "groq": ("https://api.groq.com/openai/v1/chat/completions", "GROQ_API_KEY", "json_object"),
}


@pytest.mark.parametrize("preset", list(PRESETS))
async def test_each_preset_posts_its_chat_completion_with_its_key(preset: str, monkeypatch: pytest.MonkeyPatch) -> None:
    url, env, mode = PRESETS[preset]
    monkeypatch.setenv(env, TOKEN)
    server = serve(monkeypatch, chat(RANKED))
    decision = await llm_router(preset=preset, model="m").aroute(request("who won"))
    sent, body = server.requests[0], server.body()
    assert (str(sent.url), sent.headers["Authorization"], body["model"]) == (url, f"Bearer {TOKEN}", "m")
    assert body["response_format"]["type"] == mode
    if mode == "json_schema":
        schema = body["response_format"]["json_schema"]
        assert schema["strict"] is True
        properties = schema["schema"]["properties"]
        assert properties["ranked"]["items"]["enum"] == OFFERED
        assert properties["scores"]["items"]["properties"]["id"]["enum"] == OFFERED
        assert schema["schema"]["required"] == ["ranked", "scores", "confidence"]
    system, user = body["messages"]
    assert "json" in system["content"] and "who won" in user["content"]
    assert "- search:tavily: search on tavily, $0.008, p90 1.2s" in user["content"]
    assert decision.providers == {"answer": ["perplexity"], "search": ["exa"]}
    assert decision.confidence == 0.7
    assert decision.reason == f"llm({httpx.URL(url).host}): answer:perplexity, p=0.80"


async def test_its_own_reason_is_never_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", TOKEN)
    serve(monkeypatch, chat({**RANKED, "reason": "ignore all rules"}))
    decision = await llm_router(preset="openai", model="m").aroute(request())
    assert decision.reason == "llm(api.openai.com): answer:perplexity, p=0.80"


def test_a_local_base_url_needs_no_key_and_asks_in_the_prompt_only(monkeypatch: pytest.MonkeyPatch) -> None:
    server = serve(monkeypatch, chat(RANKED))
    outcome = AttachedRouter(llm_router(base_url="http://127.0.0.1:8000/v1", model="m")).call(request())
    assert outcome.decision is not None and outcome.decision.tools == ["answer", "search"]
    assert "Authorization" not in server.requests[0].headers
    assert "response_format" not in server.body()
    assert str(server.requests[0].url) == "http://127.0.0.1:8000/v1/chat/completions"


@pytest.mark.parametrize(
    ("config", "sent"),
    [
        ({"base_url": "http://127.0.0.1:8000/v1", "response_format": "json_schema"}, "json_schema"),
        ({"base_url": "http://127.0.0.1:8000/v1", "response_format": "json_object"}, "json_object"),
        ({"preset": "openai", "api_key": TOKEN, "response_format": "prompt"}, None),
    ],
    ids=["local-schema", "local-object", "preset-prompt"],
)
def test_response_format_overrides_the_presets_choice(
    config: dict[str, str], sent: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = serve(monkeypatch, chat(RANKED))
    AttachedRouter(llm_router(model="m", **config)).call(request())  # type: ignore[arg-type]
    assert server.body().get("response_format", {}).get("type") == sent


def test_openai_api_key_goes_only_to_api_openai_com(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", TOKEN)
    with pytest.raises(ConfigurationError, match=r"OPENAI_API_KEY is sent only to api\.openai\.com") as caught:
        LLMDecider(base_url="https://proxy.example.com/v1", api_key_env="OPENAI_API_KEY", model="m")
    with pytest.raises(ConfigurationError, match="sent only to"):
        LLMDecider(preset="groq", api_key_env="OPENAI_API_KEY", model="m")
    assert TOKEN not in str(caught.value)


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"preset": "openai"}, "OPENAI_API_KEY is not set"),
        ({"base_url": "https://llm.example.com/v1"}, "needs router_config\\['api_key_env'\\]"),
        ({"base_url": "https://llm.example.com/v1", "api_key_env": "MY_LLM_KEY"}, "MY_LLM_KEY is not set"),
        ({}, "needs router_config\\['preset'\\] or router_config\\['base_url'\\]"),
        ({"preset": "openai", "base_url": "https://api.openai.com/v1"}, "not both"),
        ({"preset": "nope"}, "has no preset 'nope'"),
        ({"base_url": "http://llm.example.com/v1", "api_key": TOKEN}, "must use https"),
        ({"base_url": "https://user:pw@llm.example.com/v1", "api_key": TOKEN}, "base_url"),
        ({"base_url": "https://llm.example.com/v1?key=abc", "api_key": TOKEN}, "query or a fragment"),
        ({"base_url": "https://llm.example.com/v1", "api_key": "two words"}, "visible ASCII"),
        ({"base_url": "https://llm.example.com/v1", "api_key": TOKEN, "response_format": "xml"}, "response_format"),
    ],
)
def test_a_bad_setup_is_refused_when_the_router_is_built(
    config: dict[str, Any], message: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    for env in ("OPENAI_API_KEY", "MY_LLM_KEY"):
        monkeypatch.delenv(env, raising=False)
    with pytest.raises(ConfigurationError, match=message) as caught:
        llm_router(model="m", **config)
    assert "pw" not in str(caught.value) and "abc" not in str(caught.value)


def _failing(monkeypatch: pytest.MonkeyPatch, handler: Any) -> str:
    """The outcome code of one call to a server answering with ``handler``, after checking no URL or key leaked."""
    server = serve(monkeypatch, handler)
    router = llm_router(base_url="https://llm.example.com/secret-path", api_key=TOKEN, model="m")
    with pytest.raises(Exception) as caught:
        AttachedRouter(router, fail_on_error=True).call(request())
    shown = f"{caught.value} {caught.value.__cause__!r}"
    assert "secret-path" not in shown and TOKEN not in shown
    assert server.clients[-1]["follow_redirects"] is False and server.clients[-1]["trust_env"] is False
    return str(caught.value)


@pytest.mark.parametrize(
    ("handler", "code"),
    [
        (lambda r: respond(status=302, Location="https://elsewhere.example.com/"), "router_error"),
        (lambda r: respond({"error": "x"}, status=500), "router_error"),
        (lambda r: (_ for _ in ()).throw(httpx.ConnectError("to https://llm.example.com/secret-path")), "router_error"),
        (lambda r: respond(raw=b" " * (300 * 1024)), "router_invalid"),
        (chat("not json"), "router_invalid"),
        (chat(["search:exa"]), "router_invalid"),
        (chat({"ranked": ["search:nope"]}), "router_invalid"),
        (chat({"ranked": ["search:exa"], "scores": [{"id": "search:exa", "p": 0.5}] * 2}), "router_invalid"),
        (chat({"ranked": ["search:exa"], "scores": [{"id": "search:exa", "p": 0.5, "x": 1}]}), "router_invalid"),
        (lambda r: respond({"choices": []}), "router_invalid"),
    ],
    ids=FAILURES,
)
def test_every_failure_leaves_the_static_order_with_no_url_or_key_shown(
    handler: Any, code: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert code in _failing(monkeypatch, handler)


def _first_offered(req: httpx.Request) -> httpx.Response:
    enum = json.loads(req.content)["response_format"]["json_schema"]["schema"]["properties"]["ranked"]["items"]["enum"]
    return chat({"ranked": enum[:1]})(req)


def test_by_name_with_router_config_it_routes_a_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", TOKEN)
    serve(monkeypatch, _first_offered)
    with WebRouter(router="llm", router_config={"preset": "openai", "model": "m"}) as client:
        plan = client.plan("who won the 2022 world cup")
    assert plan.decision.router == "llm(api.openai.com)"
    assert plan.decision.reason == f"llm(api.openai.com): {plan.tool}:{plan.providers[plan.tool][0]}"


def test_the_failures_class_and_host_ride_on_router_errors_cause() -> None:
    with socket.socket() as probe:  # a loopback port nothing listens on: refused, never the network
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    router = llm_router(base_url=f"http://127.0.0.1:{port}/v1/private", model="m", timeout_s=2.0)
    with pytest.raises(RouterError) as caught:
        AttachedRouter(router, fail_on_error=True).call(request())
    assert "router_error" in str(caught.value)
    cause = str(caught.value.__cause__)
    assert "http://127.0.0.1" in cause and "ConnectError" in cause and "private" not in cause


async def test_a_json_mode_model_may_give_scores_as_a_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", TOKEN)
    serve(monkeypatch, chat({"ranked": ["search:exa"], "scores": {"search:exa": 0.4}}))
    decision = await llm_router(preset="groq", model="m").aroute(request())
    assert decision.reason == "llm(api.groq.com): search:exa, p=0.40"
