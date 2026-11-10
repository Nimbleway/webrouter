"""What the router-registry tests share: a registered name, and routers that count how they were closed."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

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


@pytest.fixture(name="built")
def built_fixture() -> Iterator[list[Closable]]:
    """NAME registered for the test; every router the factory builds is appended here."""
    made: list[Closable] = []

    def factory(**config: Any) -> Closable:
        made.append(Closable(**config))
        return made[-1]

    register_router(NAME, factory)
    yield made
    unregister_router(NAME)
