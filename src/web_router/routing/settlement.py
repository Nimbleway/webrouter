"""What a budgeted call's attempts end up charged: the estimate, a backend's reported cost, or nothing.

Apart from :mod:`.ledger` so each stays small. The ledger charges each
attempt its list-price estimate when it starts. Two things then change that
charge, in the call's own cap and in every shared budget it reserves in:

- :func:`refund_unstarted`: an attempt that failed before the backend did any
  work -- the failures :func:`~web_router.providers._chain.nothing_started`
  names, a 4xx refusal or a connection never made, or a member passed over
  while it cools down or refusing an option itself -- is charged nothing. A 404
  or 410 stays charged: a fetch backend may bill a page it found gone;
- :func:`settle_reported`: the attempt whose answer the call returns, when
  the response carries the backend's own reported cost, is charged that
  instead of its estimate.

Only the ledger of the call the attempt was made in settles it; the calls it
is nested in, which counted it toward their own caps, are settled to match.
The decision record keeps the estimate of an answered attempt; a refunded
attempt's estimate there is ``0``, as nothing ran.
"""

from __future__ import annotations

from web_router.constants_cost import COST_REPORTED
from web_router.exceptions import UnsupportedParameterError, WebRouterError
from web_router.exceptions_typed import InvalidParameterError
from web_router.providers._chain import nothing_started
from web_router.providers._cooldown import CoolingDownError
from web_router.routing.budget_hooks import LEDGERS, check_own
from web_router.routing.ledger import Ledger, _Tried
from web_router.types_errors import TARGET_GONE_STATUSES

_PASSED_OVER_UNSENT = (CoolingDownError, UnsupportedParameterError, InvalidParameterError)
"""Failures ``auto`` passes a member over with before it sends anything: its cooldown, or its own refusal."""


def refund_unstarted(ledger: Ledger, call_id: str, exc: BaseException | str) -> None:
    """Charge nothing for the call's open attempt, ``call_id``, when ``exc`` proves it never started.

    Only the open attempt in the call's own trail, and only by the ledger of
    the call it was made in: an enclosing call's ledger, told of a nested
    call's failure, leaves its own open attempt as it is.
    """
    if not isinstance(exc, WebRouterError) or not _never_sent(exc) or not check_own(ledger)():
        return
    if getattr(exc, "status_code", None) in TARGET_GONE_STATUSES:
        return  # the page is gone, which a fetch backend may have billed finding out
    with ledger._lock:
        opened = ledger.trail.open
        if opened is None or opened.id != call_id or opened.attempt is None:
            return
        tried = ledger.tried[opened.attempt]
        _charge(ledger, tried, 0.0)
        tried.est_cost_usd = 0.0
    _match_enclosing(ledger, tried, 0.0, refunded=True)


def _never_sent(exc: WebRouterError) -> bool:
    """Whether ``exc`` proves no request went out: a failure :func:`nothing_started` names, a member passed over
    while it cools down, or one that refused an option itself before building its request.

    The last two are not in :func:`nothing_started`, which decides whether a job chain may move on, and say
    nothing about billing there; here they are exactly the attempts that never left the process.
    """
    return isinstance(exc, _PASSED_OVER_UNSENT) or nothing_started(exc)


def settle_reported(ledger: Ledger, response: object, empty: bool) -> None:
    """Charge the attempt whose answer ``response`` is (``empty``: an empty one) its reported cost, if it states one."""
    cost = getattr(response, "cost_usd", None)
    if cost is None or getattr(response, "cost_source", None) != COST_REPORTED:
        return
    with ledger._lock:
        answering = ledger._answered(getattr(response, "provider", None), empty)
        if answering is None:
            return
        tried = ledger.tried[answering]
        _charge(ledger, tried, cost)
    _match_enclosing(ledger, tried, cost)


def _match_enclosing(ledger: Ledger, settled: _Tried, cost: float, *, refunded: bool = False) -> None:
    """Charge ``cost`` for ``settled``'s attempt in every enclosing call's ledger that counted it too; ``refunded``
    lists it at $0 in their records as well, as nothing ran."""
    for enclosing in LEDGERS.get():
        if enclosing is ledger:
            continue
        with enclosing._lock:
            counted = next((tried for tried in enclosing.tried if tried.attempt is settled.attempt), None)
            if counted is not None:
                _charge(enclosing, counted, cost)
                counted.est_cost_usd = 0.0 if refunded else counted.est_cost_usd


def _charge(ledger: Ledger, tried: _Tried, cost: float) -> None:
    """Charge ``tried`` ``cost`` in place of what it was charged, in the call's cap and, if its own, the pool's.

    Called under the ledger's lock. A cost past the estimate is reserved without a check: it was billed.
    """
    delta, tried.charged = cost - tried.charged, cost
    ledger.spent += delta
    if ledger.pool is not None and tried.own:
        ledger.pool.release(-delta)
        ledger.reserved += delta
