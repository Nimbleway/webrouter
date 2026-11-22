"""A ledger's decision record: its attempts, hops and estimated spend, written into a router's record.

Apart from :mod:`.ledger` so it stays small. :meth:`~.ledger.Ledger.record`
takes its snapshot under the ledger's lock and hands it here.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Any, Protocol

from web_router.routing.attempts import Attempt
from web_router.types_decision import DecisionAttempt, DecisionHop, DecisionRecord


class Tried(Protocol):
    """What the record reads of one attempt the ledger kept."""

    id: str
    est_cost_usd: float | None
    started: float
    attempt: Attempt


def written(
    into: DecisionRecord,
    attempts: Sequence[Tried],
    unmeasured: Sequence[str],
    answering: int | None,
    hops: list[DecisionHop] | None,
) -> DecisionRecord:
    """``into`` with the call's ``attempts``, ``hops`` and estimated spend; ``answering`` is the attempt it returned."""
    ends = [tried.started for tried in attempts[1:]] + [time.monotonic()]
    known = [tried.est_cost_usd for tried in attempts if tried.est_cost_usd is not None]
    ran = None if answering is None else attempts[answering].id
    update: dict[str, Any] = {
        "estimated_cost_usd": sum(known) if known else None,
        "cost_is_estimate": True,
        "attempts": [
            DecisionAttempt(
                id=tried.id, mode=tried.attempt.mode, est_cost_usd=tried.est_cost_usd, seconds=end - tried.started
            )
            for tried, end in zip(attempts, ends, strict=True)
        ],
        "hops": hops,
        "ran": into.merged.index(ran) if into.merged and ran in into.merged else into.ran,
        "unmeasured": list(dict.fromkeys([*(into.unmeasured or ()), *unmeasured])) or None,
    }
    return into.model_copy(update=update)
