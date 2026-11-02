"""The Jev decider: built by name, its extra, and reading a reply, with no network."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from web_router.constants_decisions import ROUTE_QUESTIONS
from web_router.decisions import Answer, JevDecider, create_decider
from web_router.exceptions import ProviderError, ProviderNotInstalledError


def test_a_missing_dependency_is_reported_as_an_extra_to_install(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(sys.modules, "web_router.decisions.jev", raising=False)
    monkeypatch.setitem(sys.modules, "httpx", None)
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    with pytest.raises(ProviderNotInstalledError, match="search"):
        create_decider("jev")


def jev_with_reply(
    payload: Any, monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response] | None = None
) -> JevDecider:
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    jev = JevDecider()
    jev._client = httpx.Client(
        base_url="https://jev.test",
        transport=httpx.MockTransport(handler or (lambda request: httpx.Response(200, json=payload))),
    )
    return jev


def test_jev_decider_reads_a_reply_and_wraps_http_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    probabilities = {"search": 0.1, "extract": 0.8, "research": 0.05, "browse": 0.05}
    reply = {"answers": {"tool": {"choice": "extract", "confidence": 0.7, "probabilities": probabilities}}}
    sent: list[dict[str, Any]] = []

    def answer(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json=reply)

    answers = jev_with_reply(None, monkeypatch, answer).decide({"query": "q"}, ROUTE_QUESTIONS)
    assert answers == {"tool": Answer(choice="extract", confidence=0.7, probabilities=probabilities)}
    assert sent[0]["questions"] == ROUTE_QUESTIONS

    def fail(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="down")

    with pytest.raises(ProviderError, match="HTTP 500: down") as caught:
        jev_with_reply(None, monkeypatch, fail).decide({"query": "q"}, ROUTE_QUESTIONS)
    assert isinstance(caught.value.original, httpx.HTTPStatusError)

    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(ProviderError) as caught:
        jev_with_reply(None, monkeypatch, slow).decide({"query": "q"}, ROUTE_QUESTIONS)
    assert isinstance(caught.value.original, httpx.ReadTimeout)
