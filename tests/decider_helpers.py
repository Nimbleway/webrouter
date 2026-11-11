"""What the decider tests share: a request over a few candidates, fixed deciders, and httpx on a mock transport."""

from __future__ import annotations

import asyncio
import functools
import json
import time
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from tests.examples.helpers import CANDIDATES, EXTRACT, request
from web_router import DeciderQuestion, DeciderReply

__all__ = ["CANDIDATES", "EXTRACT", "request"]

TOKEN = "test-token-not-real"
"""A key the tests set in the environment: never a real one."""


class Fixed:
    """A sync decider that answers ``reply`` and keeps the questions it was asked."""

    name = "fixed"

    def __init__(self, reply: Any, *, sleep_s: float = 0.0, timeout_s: float | None = None) -> None:
        self.reply = reply
        self.sleep_s = sleep_s
        self.asked: list[DeciderQuestion] = []
        self.closed = 0
        if timeout_s is not None:
            self.timeout_s = timeout_s

    def decide(self, question: DeciderQuestion) -> Any:
        self.asked.append(question)
        time.sleep(self.sleep_s)
        return self.reply

    def close(self) -> None:
        self.closed += 1


class AFixed:
    """An async decider that answers ``reply``, closed with ``aclose()``."""

    name = "afixed"

    def __init__(self, reply: Any, *, sleep_s: float = 0.0, timeout_s: float | None = None) -> None:
        self.reply = reply
        self.sleep_s = sleep_s
        self.asked: list[DeciderQuestion] = []
        self.aclosed = 0
        if timeout_s is not None:
            self.timeout_s = timeout_s

    async def adecide(self, question: DeciderQuestion) -> Any:
        self.asked.append(question)
        await asyncio.sleep(self.sleep_s)
        return self.reply

    async def aclose(self) -> None:
        self.aclosed += 1


def reply(*ranked: str, scores: dict[str, float] | None = None, confidence: float | None = None) -> DeciderReply:
    return DeciderReply(ranked=list(ranked), scores=scores, confidence=confidence)


class Server:
    """httpx's ``AsyncClient`` swapped for one on a mock transport: the requests it saw and how each client was made."""

    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self.handler = handler
        self.requests: list[httpx.Request] = []
        self.clients: list[dict[str, Any]] = []

    def respond(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        return self.handler(req)

    def client(self, real: Any, **kwargs: Any) -> Any:
        self.clients.append(kwargs)
        return real(transport=httpx.MockTransport(self.respond), **kwargs)

    def body(self, index: int = -1) -> Any:
        return json.loads(self.requests[index].content)


def serve(monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]) -> Server:
    """Answer every decider request with ``handler``; nothing reaches the network."""
    server = Server(handler)
    monkeypatch.setattr(httpx, "AsyncClient", functools.partial(server.client, httpx.AsyncClient))
    return server


def respond(data: Any = None, *, status: int = 200, raw: bytes | None = None, **headers: str) -> httpx.Response:
    """A streamed answer, as a server sends one: JSON of ``data``, or ``raw`` bytes, read once by the router."""
    body = json.dumps(data).encode() if raw is None else raw
    return httpx.Response(
        status, headers={"Content-Type": "application/json", **headers}, stream=httpx.ByteStream(body)
    )


def chat(answer: Any) -> Callable[[httpx.Request], httpx.Response]:
    """A chat-completions server whose one message's content is ``answer`` as JSON (a string is sent as is)."""
    content = answer if isinstance(answer, str) else json.dumps(answer)
    return lambda req: respond({"choices": [{"message": {"role": "assistant", "content": content}}]})


def systemone(prefer: str, confidence: float = 0.6) -> Callable[[httpx.Request], httpx.Response]:
    """A System One server that gives ``prefer`` 0.7 and splits the rest over the other criteria asked."""

    def handler(req: httpx.Request) -> httpx.Response:
        asked = list(json.loads(req.content)["questions"]["route"]["criteria"])
        rest = 0.3 / max(len(asked) - 1, 1)
        probabilities = {cid: 0.7 if cid == prefer else rest for cid in asked}
        answer = {"type": "choice", "choice": prefer, "confidence": confidence, "probabilities": probabilities}
        return respond({"answers": {"route": answer}})

    return handler
