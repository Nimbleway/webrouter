"""WebRouter.classify() and execute() over a decider, with no network."""

from __future__ import annotations

import json
from typing import Any

import httpx

from web_router.decisions import Answer
from web_router.exceptions import ProviderError

SURE = 0.9


def answers(**choices: str) -> dict[str, Answer]:
    return {name: Answer(choice=choice, confidence=SURE) for name, choice in choices.items()}


class FakeDecider:
    """Answers every question with fixed labels, or fails; records what it was asked and whether it was closed."""

    def __init__(self, labels: dict[str, Answer] | None = None, fail: bool = False) -> None:
        self.labels = labels or {}
        self.fail = fail
        self.calls = 0
        self.closed = False

    def decide(self, state: Any, questions: dict[str, Any]) -> dict[str, Answer]:
        self.calls += 1
        if self.fail:
            raise ProviderError("fake", "down")
        return self.labels

    def close(self) -> None:
        self.closed = True


class StubApi:
    """One backend's API behind its real client: records each request and answers from a path table."""

    def __init__(self, replies: dict[tuple[str, str], tuple[int, Any]]) -> None:
        self.replies = replies
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        status, body = self.replies[(request.method, request.url.path)]
        return httpx.Response(status, json=body)

    def bodies(self) -> list[Any]:
        return [json.loads(r.content) for r in self.requests if r.content]
