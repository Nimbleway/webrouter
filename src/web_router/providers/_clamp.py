"""Transport timeouts under a per-call deadline: a client's own limits, clamped to the time left."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

from web_router.providers._deadline import call_budget, current_deadline

if TYPE_CHECKING:
    import httpx


def clamped(timeout: float | httpx.Timeout | None) -> dict[str, Any]:
    """A one-call request's ``timeout`` keyword under a deadline: ``timeout``, clamped to the time left.

    Empty with no deadline in force, so the client's timeout applies as it
    always has. A job's requests clamp their own longer default instead.
    """
    if current_deadline() is None:
        return {}
    return {"timeout": clamp_timeout(timeout)}


def clamp_timeout(configured: float | httpx.Timeout | None) -> float | httpx.Timeout:
    """``configured`` with every limit clamped to the time the deadline in force leaves; never raised.

    For a transport whose timeout is its own -- an ``httpx`` client's, an
    SDK's -- rather than a job's: a part left unlimited is limited to the time
    left. Only meaningful under a deadline, where every part comes back finite.
    """
    import httpx  # here, not at the top: importing the deadline needs no HTTP client

    if isinstance(configured, httpx.Timeout):
        return httpx.Timeout(
            connect=_clamp(configured.connect),
            read=_clamp(configured.read),
            write=_clamp(configured.write),
            pool=_clamp(configured.pool),
        )
    return _clamp(configured)


def _clamp(limit: float | None) -> float:
    return call_budget(math.inf if limit is None else limit)
