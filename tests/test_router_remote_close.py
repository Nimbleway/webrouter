"""The remote router's connection pool: one, on the loop that first used it, and closed from another loop."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from tests.routing_remote_helpers import (
    KEY,
    Canned,
    ask,
    canned,
    config,
    loop_thread,
    on_loop,
    running,
    sample_request,
    stuck,
)
from web_router.routing.remote import RemoteRouter, RemoteRouterError


def test_closing_from_another_thread_closes_the_pool_on_its_own_loop() -> None:
    with loop_thread() as loop, running() as server:
        router = RemoteRouter(**config(server))
        on_loop(loop, router)
        pool = router._pool
        assert pool is not None and not pool.is_closed
        router.close()
        assert pool.is_closed and router._pool is None


def test_a_call_on_another_loop_gets_a_client_of_its_own_closed_after_it(monkeypatch: pytest.MonkeyPatch) -> None:
    with loop_thread() as loop, running() as server:
        router = RemoteRouter(**config(server))
        on_loop(loop, router)
        pool = router._pool
        made: list[Any] = []
        new_client = router._new_client
        monkeypatch.setattr(router, "_new_client", lambda: made.append(new_client()) or made[-1])
        assert asyncio.run(router.aroute(sample_request())).tools == ["search"], "a call on another, live loop"
        assert router._pool is pool and pool is not None and not pool.is_closed, "the owner's pool is left alone"
        (own,) = made
        assert own.is_closed, "the other loop's client closed when its call ended"
        assert own.headers["Authorization"] == f"Bearer {KEY}" and not own.follow_redirects and not own._trust_env
        router.close()
        assert pool.is_closed


def test_a_pool_whose_loop_ended_is_replaced_and_dropped_with_a_warning() -> None:
    with canned(Canned(body=b'{"schema": 1, "tools": ["search"]}', protocol="HTTP/1.1")) as base:
        router = RemoteRouter(url=base + "/route")
        ask(router)
        first = router._pool
        with pytest.warns(ResourceWarning, match="its event loop is not running"):
            ask(router)  # the first loop ended with its keep-alive connection open
        assert router._pool is not None and router._pool is not first, "a closed loop's pool is not reused"
        with pytest.warns(ResourceWarning, match="its event loop is not running"):
            router.close()
        assert router._pool is None


@pytest.mark.parametrize("used", [True, False], ids=["used", "unused"])
def test_a_closed_router_refuses_calls_and_opens_nothing(used: bool) -> None:
    with loop_thread() as loop, running() as server:
        router = RemoteRouter(**config(server))
        if used:
            on_loop(loop, router)
        router.close()
        with pytest.raises(RemoteRouterError, match="this router is closed"):
            on_loop(loop, router)
        with pytest.raises(RemoteRouterError, match="this router is closed"):
            asyncio.run(router.aroute(sample_request()))
        assert router._pool is None


async def test_aclose_of_another_loops_pool_never_blocks_this_loop() -> None:
    delay_s = 0.3
    with loop_thread() as loop, running() as server:
        router = RemoteRouter(**config(server))
        on_loop(loop, router)
        pool = router._pool
        done = stuck(pool, delay_s)
        ticks = 0

        async def tick() -> None:
            nonlocal ticks
            while True:
                await asyncio.sleep(delay_s / 30)
                ticks += 1

        ticker = asyncio.create_task(tick())
        await router.aclose()
        ticker.cancel()
        assert done == ["closed"] and pool is not None and pool.is_closed
        assert ticks >= 10, "this loop ran while the pool closed on its own"
