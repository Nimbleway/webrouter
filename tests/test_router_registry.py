"""Routers by name: register_router / unregister_router, router_config, the record's name, and who closes the router."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from web_router import AsyncWebRouter, RouteDecision, RouteRequest, WebRouter, register_router, unregister_router
from web_router.exceptions import ConfigurationError
from web_router.routing.registry import available_routers

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


@pytest.fixture
def built() -> Iterator[list[Closable]]:
    """Register NAME for the test; every router the factory builds is appended here."""
    made: list[Closable] = []

    def factory(**config: Any) -> Closable:
        made.append(Closable(**config))
        return made[-1]

    register_router(NAME, factory)
    yield made
    unregister_router(NAME)


def test_a_registered_name_builds_its_router_with_router_config(built: list[Closable]) -> None:
    with WebRouter(router=NAME, router_config={"threshold": 0.5}) as wr:
        plan = wr.plan("who won the 2022 world cup")
    assert [router.config for router in built] == [{"threshold": 0.5}]
    assert plan.decision.reason == "registered"


def test_a_registered_name_is_listed_and_unregistering_it_refuses_it_again(built: list[Closable]) -> None:
    assert NAME in available_routers() and "remote" in available_routers()
    unregister_router(NAME)
    assert NAME not in available_routers()
    with pytest.raises(ConfigurationError, match="names no registered router"):
        WebRouter(router=NAME)
    unregister_router(NAME)  # a no-op the second time


def test_registering_remote_overrides_the_built_in_until_unregistered() -> None:
    register_router("remote", Closable)
    try:
        assert isinstance(WebRouter(router="remote")._owned_router, Closable)
    finally:
        unregister_router("remote")
    with pytest.raises(ConfigurationError, match="needs router_config"):
        WebRouter(router="remote")


@pytest.mark.parametrize("name", ["", "   ", 7])
def test_a_name_must_be_a_non_empty_string(name: Any) -> None:
    with pytest.raises(ConfigurationError, match="non-empty string"):
        register_router(name, Closable)


def test_a_factory_must_be_callable() -> None:
    with pytest.raises(ConfigurationError, match="takes a callable"):
        register_router(NAME, "not a factory")  # type: ignore[arg-type]


@pytest.mark.parametrize("cls", [WebRouter, AsyncWebRouter])
def test_router_config_without_a_name_is_refused(cls: type) -> None:
    with pytest.raises(ConfigurationError, match="router_config="):
        cls(router=Closable().route, router_config={"url": "https://example.com"})


def test_a_built_router_the_attach_refuses_is_closed_before_the_error() -> None:
    made: list[Any] = []

    class NotARouter:
        def close(self) -> None:
            made.append("closed")

    register_router(NAME, NotARouter)
    try:
        with pytest.raises(ConfigurationError, match="router="):
            WebRouter(router=NAME)
    finally:
        unregister_router(NAME)
    assert made == ["closed"]


# -- the record's name ----------------------------------------------------------------


def test_a_router_names_itself_in_the_record_with_router_name() -> None:
    class Named(Closable):
        router_name = "my-service(eu)"

    wr = WebRouter(router=Named())
    assert wr.plan("solar news").decision.router == "my-service(eu)"


def test_without_router_name_the_record_keeps_the_qualified_name() -> None:
    def ranker(req: RouteRequest) -> RouteDecision:
        return RouteDecision()

    assert WebRouter(router=ranker).plan("solar news").decision.router == ranker.__qualname__


# -- who closes it ---------------------------------------------------------------------


def test_the_sync_router_closes_a_router_it_built_by_name(built: list[Closable]) -> None:
    WebRouter(router=NAME).close()
    assert (built[0].closed, built[0].aclosed) == (1, 0)


async def test_the_async_router_acloses_a_router_it_built_by_name(built: list[Closable]) -> None:
    await AsyncWebRouter(router=NAME).aclose()
    assert (built[0].closed, built[0].aclosed) == (0, 1)


async def test_the_async_router_falls_back_to_close_when_there_is_no_aclose() -> None:
    made: list[CloseOnly] = []
    register_router(NAME, lambda: made.append(CloseOnly()) or made[-1])
    try:
        async with AsyncWebRouter(router=NAME):
            pass
    finally:
        unregister_router(NAME)
    assert made[0].closed == 1


async def test_a_router_passed_in_is_the_callers_to_close() -> None:
    mine = Closable()
    with WebRouter(router=mine):
        pass
    async with AsyncWebRouter(router=mine):
        pass
    assert (mine.closed, mine.aclosed) == (0, 0)


@pytest.mark.parametrize(
    "kwargs",
    [{"provider": "no-such-backend"}, {"provider_config": {"exa": {"async_client": object()}}}],
    ids=["unknown-provider", "sync-router-async-client"],
)
def test_a_router_that_fails_to_construct_closes_the_router_it_built(built: list[Closable], kwargs: Any) -> None:
    with pytest.raises(Exception, match=r"no-such-backend|async_client"):
        WebRouter(router=NAME, **kwargs)
    assert all(router.closed == 1 for router in built), "built only after the provider checks, or closed by them"


def test_a_refused_async_only_router_is_closed_with_aclose() -> None:
    made: list[Any] = []

    class AsyncOnly:
        timeout_s = -1  # refused when attached

        async def aroute(self, req: RouteRequest) -> RouteDecision:
            return RouteDecision()

        async def aclose(self) -> None:
            made.append("aclosed")

    register_router(NAME, AsyncOnly)
    try:
        with pytest.raises(ConfigurationError, match="timeout_s"):
            WebRouter(router=NAME)
    finally:
        unregister_router(NAME)
    assert made == ["aclosed"]
