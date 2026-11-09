"""What the remote router's tests share: the reference server from examples/, started on a free loopback port."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from tests.examples.helpers import load_example

reference = load_example("router_remote_server")

KEY = "router-service-key-0123456789"
"""The router service's own bearer key: the one credential a request may carry."""


class Recorder:
    """A ``decide`` that keeps every body it was given and answers with ``answer``, or the reference server's own."""

    def __init__(self, answer: Callable[[dict[str, Any]], dict[str, Any]] | None = None) -> None:
        self.bodies: list[dict[str, Any]] = []
        self.raw: list[str] = []
        self.answer = answer or reference.decide

    def __call__(self, body: dict[str, Any]) -> dict[str, Any]:
        self.bodies.append(body)
        self.raw.append(json.dumps(body))
        return self.answer(body)


@contextmanager
def running(
    decide: Callable[[dict[str, Any]], dict[str, Any]] | None = None, *, api_key: str | None = KEY
) -> Iterator[Any]:
    """The reference server on 127.0.0.1, a free port, answering with ``decide``; shut down on exit."""
    server = reference.serve("127.0.0.1", 0, api_key=api_key)
    if decide is not None:
        server.decide = decide
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


def config(server: Any, **extra: Any) -> dict[str, Any]:
    """``router_config`` for the ``remote`` router pointed at ``server``, with its key."""
    return {"url": server.url, "api_key": KEY, **extra}
