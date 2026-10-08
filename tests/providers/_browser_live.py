"""What the live browser tests share: waiting for a backend to report a release.

A release is a request the backend acts on, not always a state it reports at
once, so a live test asks again for a bounded while rather than once. Only the
backend is asked: a handle answers ``released`` from memory once it has
released, which would prove nothing.
"""

from __future__ import annotations

import asyncio
import time

from web_router.providers.base import BrowserProvider
from web_router.types import BrowserState

RELEASE_DEADLINE = 10.0
RELEASE_INTERVAL = 0.5


def state_after_release(provider: BrowserProvider, session_id: str) -> BrowserState:
    """The state the backend reports once it says ``released``, or the last one it said by the deadline."""
    deadline = time.monotonic() + RELEASE_DEADLINE
    while (state := provider.status(session_id)) != "released" and time.monotonic() < deadline:
        time.sleep(RELEASE_INTERVAL)
    return state


async def astate_after_release(provider: BrowserProvider, session_id: str) -> BrowserState:
    """Async counterpart of :func:`state_after_release`."""
    deadline = time.monotonic() + RELEASE_DEADLINE
    while (state := await provider.astatus(session_id)) != "released" and time.monotonic() < deadline:
        await asyncio.sleep(RELEASE_INTERVAL)
    return state
