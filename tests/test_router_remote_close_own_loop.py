"""Closing the remote router's pool on its own loop, and a close that fails, runs late or loses its loop."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from tests.routing_remote_helpers import (
    KEY,
    SHORT_WAIT_S,
    SLOW_CLOSE_S,
    config,
    loop_thread,
    on_loop,
    running,
    sample_request,
    stuck,
)
from web_router.routing import remote_close
from web_router.routing.remote import RemoteRouter, RemoteRouterError


def test_a_pool_that_fails_to_close_leaves_the_router_closed_with_a_warning() -> None:
    with loop_thread() as loop, running() as server:
        router = RemoteRouter(**config(server))
        on_loop(loop, router)
        pool = router._pool

        async def broken() -> None:
            raise OSError(f"closing {server.url} with {KEY}")

        pool.aclose = broken  # type: ignore[union-attr,method-assign]
        with pytest.warns(ResourceWarning, match=r"did not close \(OSError\)") as caught:
            router.close()
        assert router._pool is None and router._owner is None, "no pool is left on the router"
        assert not any(KEY in str(w.message) or server.url in str(w.message) for w in caught)
        with pytest.raises(RemoteRouterError, match="closed"):
            on_loop(loop, router)
        assert router._pool is None


def test_a_close_past_its_wait_is_cancelled_and_warned(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(remote_close, "REMOTE_CLOSE_WAIT_S", SHORT_WAIT_S)
    with loop_thread() as loop, running() as server:
        router = RemoteRouter(**config(server))
        on_loop(loop, router)
        done = stuck(router._pool)
        started = time.monotonic()
        with pytest.warns(ResourceWarning, match="not closed within"):
            router.close()
        assert time.monotonic() - started < SLOW_CLOSE_S / 2
        assert router._pool is None
        asyncio.run_coroutine_threadsafe(asyncio.sleep(0), loop).result(5)
        assert done == ["cancelled"]


async def test_aclose_past_its_wait_returns_and_warns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(remote_close, "REMOTE_CLOSE_WAIT_S", SHORT_WAIT_S)
    with loop_thread() as loop, running() as server:
        router = RemoteRouter(**config(server))
        on_loop(loop, router)
        done = stuck(router._pool)
        with pytest.warns(ResourceWarning, match="not closed within"):
            await router.aclose()
        assert router._pool is None
        await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(asyncio.sleep(0), loop))
        assert done == ["cancelled"]


async def test_aclose_on_the_pools_own_loop_awaits_it() -> None:
    with running() as server:
        router = RemoteRouter(**config(server))
        await router.aroute(sample_request())
        pool = router._pool
        await router.aclose()
        assert pool is not None and pool.is_closed and router._pool is None


async def test_close_on_the_pools_own_loop_closes_it_there_as_a_task() -> None:
    with running() as server:
        router = RemoteRouter(**config(server))
        await router.aroute(sample_request())
        pool = router._pool
        router.close()
        assert router._pool is None
        await asyncio.gather(*remote_close._CLOSING)
        assert pool is not None and pool.is_closed and not remote_close._CLOSING


async def test_close_on_the_pools_own_loop_is_bounded_too(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(remote_close, "REMOTE_CLOSE_WAIT_S", SHORT_WAIT_S)
    with running() as server:
        router = RemoteRouter(**config(server))
        await router.aroute(sample_request())
        done = stuck(router._pool)
        router.close()
        with pytest.warns(ResourceWarning, match="not closed within"):
            await asyncio.gather(*remote_close._CLOSING, return_exceptions=True)
            await asyncio.sleep(0)  # the task's done callback, which warns
        assert done == ["cancelled"] and not remote_close._CLOSING


async def test_aclose_on_the_pools_own_loop_is_bounded_too(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(remote_close, "REMOTE_CLOSE_WAIT_S", SHORT_WAIT_S)
    with running() as server:
        router = RemoteRouter(**config(server))
        await router.aroute(sample_request())
        done = stuck(router._pool)
        with pytest.warns(ResourceWarning, match="not closed within"):
            await router.aclose()
        assert done == ["cancelled"] and router._pool is None


async def test_a_cancelled_aclose_leaves_the_router_closed_and_says_so() -> None:
    with loop_thread() as loop, running() as server:
        router = RemoteRouter(**config(server))
        on_loop(loop, router)
        stuck(router._pool)
        closing = asyncio.create_task(router.aclose())
        await asyncio.sleep(SHORT_WAIT_S)
        with pytest.warns(ResourceWarning, match="CancelledError"):
            closing.cancel()
            with pytest.raises(asyncio.CancelledError):
                await closing
        assert router._pool is None
        with pytest.raises(RemoteRouterError, match="closed"):
            await router.aroute(sample_request())


@pytest.mark.parametrize("closer", ["close", "aclose"])
def test_a_loop_that_closes_as_the_close_is_handed_over_is_warned_not_raised(
    monkeypatch: pytest.MonkeyPatch, closer: str
) -> None:
    def gone(coro: Any, loop: Any) -> Any:
        raise RuntimeError("Event loop is closed")  # what the hand-over raises once the loop shut in between

    with loop_thread() as loop, running() as server:
        router = RemoteRouter(**config(server))
        on_loop(loop, router)
        monkeypatch.setattr(remote_close.asyncio, "run_coroutine_threadsafe", gone)
        with pytest.warns(ResourceWarning, match="did not close"):
            if closer == "close":
                router.close()
            else:
                asyncio.run(router.aclose())
        monkeypatch.undo()
        assert router._pool is None
