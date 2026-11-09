"""The remote router's connection pool: one, on the loop that first used it, and closed with the router."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest

from tests.routing_remote_helpers import KEY, config, running
from web_router.routing import remote
from web_router.routing.remote import RemoteRouter, RemoteRouterError
from web_router.routing.wire import request_from_wire

SLOW_CLOSE_S = 5.0
"""How long a stuck pool takes to close: far past any wait a test allows."""

SHORT_WAIT_S = 0.05
"""The close wait a test sets, so a stuck close is given up on at once."""


def _request() -> Any:
    return request_from_wire(
        {"schema": 1, "prompt": "solar news", "candidates": [{"tool": "search", "provider": "exa"}], "deadline_s": 1.0}
    )


@contextmanager
def loop_thread() -> Iterator[asyncio.AbstractEventLoop]:
    """An event loop running on a thread of its own, as web-router's private loop does."""
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    try:
        yield loop
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(5)
        loop.close()


def _on(loop: asyncio.AbstractEventLoop, router: RemoteRouter) -> Any:
    return asyncio.run_coroutine_threadsafe(router.aroute(_request()), loop).result(5)


def _stuck(pool: Any, delay_s: float = SLOW_CLOSE_S) -> list[str]:
    """Make ``pool`` take ``delay_s`` to close; what it did is recorded."""
    done: list[str] = []
    real = pool.aclose

    async def slow() -> None:
        try:
            await asyncio.sleep(delay_s)
        except asyncio.CancelledError:
            done.append("cancelled")
            raise
        await real()
        done.append("closed")

    pool.aclose = slow
    return done


def test_closing_from_another_thread_closes_the_pool_on_its_own_loop() -> None:
    with loop_thread() as loop, running() as server:
        router = RemoteRouter(**config(server))
        _on(loop, router)
        pool = router._pool
        assert pool is not None and not pool.is_closed
        router.close()
        assert pool.is_closed and router._pool is None


def test_a_call_on_another_loop_gets_a_client_of_its_own_closed_after_it(monkeypatch: pytest.MonkeyPatch) -> None:
    with loop_thread() as loop, running() as server:
        router = RemoteRouter(**config(server))
        _on(loop, router)
        pool = router._pool
        made: list[Any] = []
        new_client = router._new_client
        monkeypatch.setattr(router, "_new_client", lambda: made.append(new_client()) or made[-1])
        assert asyncio.run(router.aroute(_request())).tools == ["search"], "a call on another, live loop"
        assert router._pool is pool and pool is not None and not pool.is_closed, "the owner's pool is left alone"
        (own,) = made
        assert own.is_closed, "the other loop's client closed when its call ended"
        assert own.headers["Authorization"] == f"Bearer {KEY}" and not own.follow_redirects and not own._trust_env
        router.close()
        assert pool.is_closed


def test_the_pool_moves_to_the_next_loop_when_its_own_is_gone() -> None:
    with running() as server:
        router = RemoteRouter(**config(server))
        asyncio.run(router.aroute(_request()))
        first = router._pool
        asyncio.run(router.aroute(_request()))
        assert router._pool is not None and router._pool is not first, "a closed loop's pool is not reused"
        router.close()  # its loop is gone, and its connections with it: nothing to wait for
        assert router._pool is None


@pytest.mark.parametrize("used", [True, False], ids=["used", "unused"])
def test_a_closed_router_refuses_calls_and_opens_nothing(used: bool) -> None:
    with loop_thread() as loop, running() as server:
        router = RemoteRouter(**config(server))
        if used:
            _on(loop, router)
        router.close()
        with pytest.raises(RemoteRouterError, match="this router is closed"):
            _on(loop, router)
        with pytest.raises(RemoteRouterError, match="this router is closed"):
            asyncio.run(router.aroute(_request()))
        assert router._pool is None


def test_a_pool_that_fails_to_close_leaves_the_router_closed_with_a_warning() -> None:
    with loop_thread() as loop, running() as server:
        router = RemoteRouter(**config(server))
        _on(loop, router)
        pool = router._pool

        async def broken() -> None:
            raise OSError(f"closing {server.url} with {KEY}")

        pool.aclose = broken  # type: ignore[union-attr,method-assign]
        with pytest.warns(ResourceWarning, match=r"did not close \(OSError\)") as caught:
            router.close()
        assert router._pool is None and router._owner is None, "no pool is left on the router"
        assert not any(KEY in str(w.message) or server.url in str(w.message) for w in caught)
        with pytest.raises(RemoteRouterError, match="closed"):
            _on(loop, router)
        assert router._pool is None


def test_a_close_past_its_wait_is_cancelled_and_warned(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(remote, "REMOTE_CLOSE_WAIT_S", SHORT_WAIT_S)
    with loop_thread() as loop, running() as server:
        router = RemoteRouter(**config(server))
        _on(loop, router)
        done = _stuck(router._pool)
        started = time.monotonic()
        with pytest.warns(ResourceWarning, match="not closed within"):
            router.close()
        assert time.monotonic() - started < SLOW_CLOSE_S / 2
        assert router._pool is None
        asyncio.run_coroutine_threadsafe(asyncio.sleep(0), loop).result(5)
        assert done == ["cancelled"]


async def test_aclose_of_another_loops_pool_never_blocks_this_loop() -> None:
    delay_s = 0.3
    with loop_thread() as loop, running() as server:
        router = RemoteRouter(**config(server))
        _on(loop, router)
        pool = router._pool
        done = _stuck(pool, delay_s)
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


async def test_aclose_past_its_wait_returns_and_warns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(remote, "REMOTE_CLOSE_WAIT_S", SHORT_WAIT_S)
    with loop_thread() as loop, running() as server:
        router = RemoteRouter(**config(server))
        _on(loop, router)
        done = _stuck(router._pool)
        with pytest.warns(ResourceWarning, match="not closed within"):
            await router.aclose()
        assert router._pool is None
        await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(asyncio.sleep(0), loop))
        assert done == ["cancelled"]


async def test_aclose_on_the_pools_own_loop_awaits_it() -> None:
    with running() as server:
        router = RemoteRouter(**config(server))
        await router.aroute(_request())
        pool = router._pool
        await router.aclose()
        assert pool is not None and pool.is_closed and router._pool is None


async def test_close_on_the_pools_own_loop_closes_it_there_as_a_task() -> None:
    with running() as server:
        router = RemoteRouter(**config(server))
        await router.aroute(_request())
        pool = router._pool
        router.close()
        assert router._pool is None
        await asyncio.gather(*remote._CLOSING)
        assert pool is not None and pool.is_closed and not remote._CLOSING


async def test_close_on_the_pools_own_loop_is_bounded_too(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(remote, "REMOTE_CLOSE_WAIT_S", SHORT_WAIT_S)
    with running() as server:
        router = RemoteRouter(**config(server))
        await router.aroute(_request())
        done = _stuck(router._pool)
        router.close()
        with pytest.warns(ResourceWarning, match="not closed within"):
            await asyncio.gather(*remote._CLOSING, return_exceptions=True)
            await asyncio.sleep(0)  # the task's done callback, which warns
        assert done == ["cancelled"] and not remote._CLOSING


async def test_aclose_on_the_pools_own_loop_is_bounded_too(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(remote, "REMOTE_CLOSE_WAIT_S", SHORT_WAIT_S)
    with running() as server:
        router = RemoteRouter(**config(server))
        await router.aroute(_request())
        done = _stuck(router._pool)
        with pytest.warns(ResourceWarning, match="not closed within"):
            await router.aclose()
        assert done == ["cancelled"] and router._pool is None


async def test_a_cancelled_aclose_leaves_the_router_closed_and_says_so() -> None:
    with loop_thread() as loop, running() as server:
        router = RemoteRouter(**config(server))
        _on(loop, router)
        _stuck(router._pool)
        closing = asyncio.create_task(router.aclose())
        await asyncio.sleep(SHORT_WAIT_S)
        with pytest.warns(ResourceWarning, match="CancelledError"):
            closing.cancel()
            with pytest.raises(asyncio.CancelledError):
                await closing
        assert router._pool is None
        with pytest.raises(RemoteRouterError, match="closed"):
            await router.aroute(_request())
