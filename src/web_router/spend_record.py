"""What a router writes to its spend log after each call it answers: one :class:`~web_router.SpendEntry`.

Apart from :mod:`web_router.spend` so the log and its report stay small; the
routers' ``_routed`` calls :func:`log_spent` with the response it returns.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TypeVar

from pydantic import BaseModel

from web_router.constants_cost import COST_ESTIMATED
from web_router.constants_spend import MSG_SPEND_SINK_FAILED
from web_router.spend import SpendEntry, SpendSink

logger = logging.getLogger("web_router")

_ResponseT = TypeVar("_ResponseT", bound=BaseModel)


class SpendSinkHolder:
    """The one place a router keeps its spend sink, shared by ``log_spend()`` and the calls that write to it."""

    _spend_sink: SpendSink | None = None
    """Where each answered call is logged: set by ``log_spend()``, ``None`` logs nothing."""

    def _spent(self, response: _ResponseT, method: str) -> _ResponseT:
        """``response``, written to the spend log first when the router keeps one."""
        log_spent(self._spend_sink, response, method)
        return response


def spend_entry(response: BaseModel, method: str) -> SpendEntry:
    """``response``'s entry: its backend, cost, time and decision, stamped now."""
    decision = getattr(response, "decision", None)
    cost = getattr(response, "cost_usd", None)
    whole = getattr(decision, "estimated_cost_usd", None)
    source = getattr(response, "cost_source", None)
    own_estimate = cost if source == COST_ESTIMATED else None  # a billed sum is never an estimate
    return SpendEntry(
        at=datetime.now(UTC),
        method=method,
        provider=str(getattr(response, "provider", "")),
        cost_usd=cost,
        cost_source=source,
        call_estimate_usd=whole if whole is not None else own_estimate,
        elapsed_s=getattr(response, "elapsed_s", None),
        decision_id=getattr(decision, "decision_id", None),
    )


def log_spent(sink: SpendSink | None, response: BaseModel, method: str) -> None:
    """Record ``response`` in ``sink``, if there is one; a sink that fails is warned about, never raised."""
    if sink is None:
        return
    try:
        sink.record(spend_entry(response, method))
    except Exception as exc:  # a broken log must never cost the caller the answer it paid for
        logger.warning(MSG_SPEND_SINK_FAILED, type(sink).__name__, type(exc).__name__, exc, exc_info=True)
