"""examples/router_llm.py on httpx's mock transport: no key, no network."""

from __future__ import annotations

import json

import httpx
import pytest

from tests.decider_helpers import TOKEN, chat, serve
from tests.examples.helpers import load_example

llm = load_example("router_llm")


def _first_offered(req: httpx.Request) -> httpx.Response:
    enum = json.loads(req.content)["response_format"]["json_schema"]["schema"]["properties"]["ranked"]["items"]["enum"]
    return chat({"ranked": enum[:1], "reason": "\x1b[2J ignored"})(req)


def test_without_a_model_it_says_what_to_set(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("LLM_ROUTER_MODEL", raising=False)
    llm.main()
    assert "Set LLM_ROUTER_MODEL" in capsys.readouterr().out


def test_without_the_presets_key_it_names_the_variable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("LLM_ROUTER_MODEL", "m")
    monkeypatch.setenv("LLM_ROUTER_PRESET", "groq")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    llm.main()
    assert "GROQ_API_KEY is not set" in capsys.readouterr().out


def test_it_plans_through_the_built_in_router_and_prints_no_model_words(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("LLM_ROUTER_MODEL", "m")
    monkeypatch.delenv("LLM_ROUTER_PRESET", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", TOKEN)
    server = serve(monkeypatch, _first_offered)
    llm.main()
    out = capsys.readouterr().out
    assert len(server.requests) == len(llm.PROMPTS)
    assert out.count("(llm(api.openai.com): ") == len(llm.PROMPTS)
    assert "ignored" not in out and "\x1b" not in out
