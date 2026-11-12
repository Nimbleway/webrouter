"""The sync router's warning when it is called on a thread that is running an event loop.

A :class:`~web_router.WebRouter` call blocks its thread until it returns; on a
thread running an event loop that is every other task on the loop held still
for the length of the call, with nothing to say why. The call still runs: a
script that does this on purpose works as before, and the warning names the fix.
"""

from __future__ import annotations

import asyncio
import warnings

from web_router.constants_production import MSG_SYNC_ROUTER_IN_LOOP

_STACKLEVEL = 4
"""warn_if_in_loop -> SyncRouted._routed -> WebRouter.<method> -> the caller's line."""


def warn_if_in_loop(method: str) -> None:
    """Warn, as a :class:`RuntimeWarning` at the caller's line, when this thread is running an event loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    warnings.warn(MSG_SYNC_ROUTER_IN_LOOP.format(method=method), RuntimeWarning, stacklevel=_STACKLEVEL)
