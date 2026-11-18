"""What a router writes to its spend log after each call: :class:`~web_router.SpendEntry` lines.

Apart from :mod:`web_router.spend` so the log and its report stay small; the
routers' ``_routed`` calls :func:`log_spent` with the response it returns, one
line for the answer and one for each attempt its decision record moved past;
``_spending`` logs a call that raised a backend's failure or a timeout instead.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import TypeVar

from pydantic import BaseModel

from web_router.constants_cost import COST_ESTIMATED
from web_router.constants_spend import MSG_SPEND_SINK_FAILED
from web_router.exceptions import BrowseTimeoutError, DeadlineExceededError, ProviderError, ResearchTimeoutError
from web_router.spend import SpendEntry, SpendSink
from web_router.types_decision import DecisionRecord

logger = logging.getLogger("web_router")

_ResponseT = TypeVar("_ResponseT", bound=BaseModel)

_MAY_HAVE_BILLED = (ProviderError, DeadlineExceededError, ResearchTimeoutError, BrowseTimeoutError)
"""What a call raises after a backend may have started work, and billed for it; a refused argument never did."""


class SpendSinkHolder:
    """The one place a router keeps its spend sink, shared by ``log_spend()`` and the calls that write to it."""

    _spend_sink: SpendSink | None = None
    """Where each answered call is logged: set by ``log_spend()``, ``None`` logs nothing."""

    def _spent(self, response: _ResponseT, method: str) -> _ResponseT:
        """``response``, written to the spend log first when the router keeps one."""
        log_spent(self._spend_sink, response, method)
        return response

    @contextmanager
    def _spending(self, method: str | Callable[[], str | None]) -> Iterator[None]:
        """Log the call in the block as ``failed`` when it raises after a backend may have billed.

        ``method`` may be a callable, read only on failure, for a call that learns its method on the way.
        """
        started = time.monotonic()
        try:
            yield
        except _MAY_HAVE_BILLED as exc:
            named = method() if callable(method) else method
            _record(self._spend_sink, raised_entries(exc, named or "run", time.monotonic() - started))
            raise


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


def passed_entries(decision: DecisionRecord | None, method: str) -> list[SpendEntry]:
    """An entry per attempt ``decision`` moved past, failed or empty; candidates skipped unstarted have none."""
    tried = list((decision.attempts if decision else None) or [])
    entries = []
    for hop in (decision.hops if decision else None) or []:
        attempt = next((each for each in tried if each.id == hop.id), None)
        if attempt is None:  # skipped before it started: nothing ran, nothing billed
            continue
        tried.remove(attempt)
        entries.append(
            SpendEntry(
                at=datetime.now(UTC),
                method=method,
                provider=hop.id.partition(":")[2] or hop.id,
                elapsed_s=attempt.seconds,
                decision_id=decision.decision_id if decision else None,
                outcome="empty" if hop.error is None else "failed",
                error=hop.error,
            )
        )
    return entries


def raised_entries(exc: Exception, method: str, elapsed_s: float) -> list[SpendEntry]:
    """A ``failed`` entry per backend a call that raised ``exc`` tried, with its HTTP status or error kind.

    A chain whose every backend failed names each one's own error; the call's seconds go on the last line.
    """
    failures = getattr(exc, "failures", None) or {getattr(exc, "provider", ""): exc}
    last = len(failures) - 1
    return [
        SpendEntry(
            at=datetime.now(UTC),
            method=method,
            provider=str(name or ""),
            elapsed_s=elapsed_s if n == last else None,
            outcome="failed",
            error=_error_of(each),
        )
        for n, (name, each) in enumerate(failures.items())
    ]


def _error_of(exc: BaseException) -> str:
    status = getattr(exc, "status_code", None)
    return str(status) if status is not None else getattr(exc, "kind", None) or type(exc).__name__


def log_spent(sink: SpendSink | None, response: BaseModel, method: str) -> None:
    """Record ``response`` in ``sink``, after the attempts it moved past."""
    if sink is None:
        return
    decision = getattr(response, "decision", None)
    passed = passed_entries(decision if isinstance(decision, DecisionRecord) else None, method)
    _record(sink, [*passed, spend_entry(response, method)])


def _record(sink: SpendSink | None, entries: Iterable[SpendEntry]) -> None:
    """Write ``entries`` to ``sink``, if there is one; a sink that fails is warned about, never raised."""
    if sink is None:
        return
    try:
        for entry in entries:
            sink.record(entry)
    except Exception as exc:  # a broken log must never cost the caller the answer it paid for
        logger.warning(MSG_SPEND_SINK_FAILED, type(sink).__name__, type(exc).__name__, exc, exc_info=True)
