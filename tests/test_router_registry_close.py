"""Who closes a router: one built by name is closed with the routers, at once if refused; one passed in is not."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any

import pytest

from tests.routing_registry_helpers import NAME, Closable, CloseOnly, registered
from web_router import AsyncWebRouter, RouteDecision, RouteRequest, WebRouter, register_router, unregister_router
from web_router.exceptions import ConfigurationError


@pytest.fixture
def built() -> Iterator[list[Closable]]:
    """NAME registered for the test; every router the factory builds is appended here."""
    with registered() as made:
        yield made


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


def test_the_sync_router_runs_an_async_only_routers_aclose() -> None:
    made: list[str] = []

    class AsyncOnly:
        async def aroute(self, req: RouteRequest) -> RouteDecision:
            return RouteDecision()

        async def aclose(self) -> None:
            made.append("aclosed")

    register_router(NAME, AsyncOnly)
    try:
        WebRouter(router=NAME).close()
    finally:
        unregister_router(NAME)
    assert made == ["aclosed"]


async def test_a_refused_async_only_router_is_closed_before_the_error_with_a_loop_running() -> None:
    made: list[str] = []

    class AsyncOnly:
        timeout_s = -1  # refused when attached

        async def aroute(self, req: RouteRequest) -> RouteDecision:
            return RouteDecision()

        async def aclose(self) -> None:
            await asyncio.sleep(0)
            made.append("aclosed")

    register_router(NAME, AsyncOnly)
    try:
        with pytest.raises(ConfigurationError, match="timeout_s"):
            AsyncWebRouter(router=NAME)
    finally:
        unregister_router(NAME)
    assert made == ["aclosed"], "awaited before the error, not left as a task"


def test_a_refused_router_whose_close_fails_still_raises_the_configuration_error() -> None:
    class BrokenClose:
        def close(self) -> None:
            raise RuntimeError("close failed")

    register_router(NAME, BrokenClose)
    try:
        warned = pytest.warns(ResourceWarning, match=r"did not close \(RuntimeError\)")
        with warned, pytest.raises(ConfigurationError, match="router="):
            WebRouter(router=NAME)
    finally:
        unregister_router(NAME)
