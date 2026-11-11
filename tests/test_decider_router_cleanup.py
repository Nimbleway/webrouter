"""DeciderRouter drops what a decider answers once the call gives up: no coroutine unawaited, no task left running."""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import replace
from typing import Any

import pytest

from tests.decider_helpers import Fixed, reply, request
from web_router import DeciderQuestion, DeciderReply, DeciderRouter
from web_router.routing._runner import _close
from web_router.routing.invoke import AttachedRouter


class _LateDecorated:
    """A decider whose ``decide`` looks sync and answers a coroutine, only after the call's deadline has passed."""

    name = "late"
    timeout_s = 1.0

    def decide(self, question: DeciderQuestion) -> Any:
        time.sleep(0.2)

        async def answer() -> DeciderReply:
            return reply("search:exa")

        self.answered = answer()
        return self.answered


@pytest.mark.parametrize("from_async", [False, True], ids=["from-sync", "from-async"])
def test_a_coroutine_answered_after_the_deadline_is_closed_unawaited(from_async: bool) -> None:
    decider = _LateDecorated()
    attached = AttachedRouter(DeciderRouter(decider))
    req = replace(request(), deadline_s=0.05)
    outcome = asyncio.run(attached.acall(req)) if from_async else attached.call(req)
    assert outcome.failure == "router_timeout"
    for _ in range(100):  # the late answer is closed once the worker thread hands it back
        if getattr(decider, "answered", None) is not None and decider.answered.cr_frame is None:
            break
        time.sleep(0.02)
    assert decider.answered.cr_frame is None  # closed, so never reported as not awaited


async def test_a_task_answered_by_decide_is_cancelled_when_the_call_drops_it() -> None:
    task = asyncio.ensure_future(asyncio.sleep(10))
    decider = Fixed(task)
    pending = DeciderRouter(decider).route(request())
    _close(pending)
    await asyncio.sleep(0)  # the cancel is scheduled on the task's own loop
    with pytest.raises(asyncio.CancelledError):
        await task


def test_a_task_of_another_loop_is_cancelled_when_it_cannot_be_awaited() -> None:
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    task = asyncio.run_coroutine_threadsafe(_spawn(), loop).result()
    outcome = AttachedRouter(DeciderRouter(Fixed(task, timeout_s=1.0))).call(request())
    assert outcome.failure == "router_error"
    for _ in range(100):
        if task.done():
            break
        time.sleep(0.01)
    assert task.cancelled()
    loop.call_soon_threadsafe(loop.stop)


async def _spawn() -> asyncio.Task[None]:
    return asyncio.ensure_future(asyncio.sleep(10))
