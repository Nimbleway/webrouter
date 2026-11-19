"""What a budgeted call's attempts end up charged: the estimate, a backend's reported cost, or nothing.

Apart from :mod:`.ledger` so each stays small. The ledger charges each
attempt its list-price estimate when it starts. Two things then change that
charge, in the call's own cap and in every shared budget it reserves in:

- :func:`refund_unstarted`: an attempt that failed before the backend did any
  work -- the failures :func:`~web_router.providers._chain.nothing_started`
  names, a 4xx refusal or a connection never made -- is charged nothing;
- :func:`settle_reported`: the attempt whose answer the call returns, when
  the response carries the backend's own reported cost, is charged that
  instead of its estimate.

The decision record keeps the estimate of an answered attempt; a refunded
attempt's estimate there is ``0``, as nothing ran.
"""

from __future__ import annotations

from web_router.constants_cost import COST_REPORTED
from web_router.exceptions import WebRouterError
from web_router.providers._chain import nothing_started
from web_router.routing.ledger import Ledger, _Tried


def refund_unstarted(ledger: Ledger, call_id: str, exc: BaseException | str) -> None:
    """Charge nothing for the call's open attempt, ``call_id``, when ``exc`` proves it never started.

    Only the open attempt in the call's own trail: one an enclosing call
    counted, or one already taken back, is left as it is.
    """
    if not isinstance(exc, WebRouterError) or not nothing_started(exc):
        return
    with ledger._lock:
        opened = ledger.trail.open
        if opened is None or opened.id != call_id or opened.attempt is None:
            return
        tried = ledger.tried[opened.attempt]
        _charge(ledger, tried, 0.0)
        tried.est_cost_usd = 0.0


def settle_reported(ledger: Ledger, response: object, empty: bool) -> None:
    """Charge the attempt whose answer ``response`` is (``empty``: an empty one) its reported cost, if it states one."""
    cost = getattr(response, "cost_usd", None)
    if cost is None or getattr(response, "cost_source", None) != COST_REPORTED:
        return
    with ledger._lock:
        answering = ledger._answered(getattr(response, "provider", None), empty)
        if answering is not None:
            _charge(ledger, ledger.tried[answering], cost)


def _charge(ledger: Ledger, tried: _Tried, cost: float) -> None:
    """Charge ``tried`` ``cost`` in place of what it was charged, in the call's cap and, if its own, the pool's.

    Called under the ledger's lock. A cost past the estimate is reserved without a check: it was billed.
    """
    delta, tried.charged = cost - tried.charged, cost
    ledger.spent += delta
    if ledger.pool is not None and tried.own:
        ledger.pool.release(-delta)
        ledger.reserved += delta
