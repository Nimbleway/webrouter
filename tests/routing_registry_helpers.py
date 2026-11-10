"""What the router-registry tests share: a registered name, and routers that count how they were closed."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from web_router import RouteDecision, RouteRequest, register_router, unregister_router

NAME = "test-registry-router"


class Closable:
    """A router that counts how it was closed, built with whatever config it was given."""

    def __init__(self, **config: Any) -> None:
        self.config = config
        self.closed = 0
        self.aclosed = 0

    def route(self, req: RouteRequest) -> RouteDecision:
        return RouteDecision(tools=["search"], reason="registered")

    def close(self) -> None:
        self.closed += 1

    async def aclose(self) -> None:
        self.aclosed += 1


class CloseOnly(Closable):
    aclose = None  # type: ignore[assignment]


@contextmanager
def registered() -> Iterator[list[Closable]]:
    """Register NAME while open; every router the factory builds is appended to the list it yields."""
    made: list[Closable] = []

    def factory(**config: Any) -> Closable:
        made.append(Closable(**config))
        return made[-1]

    register_router(NAME, factory)
    yield made
    unregister_router(NAME)
