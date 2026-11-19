"""What a budgeted call's attempts end up charged: the estimate, a backend's reported cost, or nothing.

The policy; the ledger's own steps are in :mod:`.ledger_settle`. The ledger charges each
attempt its list-price estimate when it starts. Two things then change that
charge, in the call's own cap and in every shared budget it reserves in:

- :func:`refund_unstarted`: an attempt that failed before the backend did any
  work -- the failures :func:`~web_router.providers._chain.nothing_started`
  names, a 4xx refusal or a connection never made -- is charged nothing. A 404
  or 410 stays charged: a fetch backend may bill a page it found gone;
- :func:`settle_reported`: the attempt whose answer the call returns, when
  the response states its cost -- reported, or a keyless call's $0 -- is
  charged that instead of its estimate, and the attempts that read part of
  the same answer nothing, as the stated cost is all of theirs.

Only the ledger of the call the attempt was made in settles it; the calls it
is nested in, which counted it toward their own caps, are settled to match.
The decision record keeps the estimate of an answered attempt; a refunded
attempt's estimate there is ``0``, as nothing ran.
"""

from __future__ import annotations

from web_router.constants_cost import COST_STATED
from web_router.exceptions import WebRouterError
from web_router.providers._chain import nothing_started
from web_router.routing.attempts import Attempt
from web_router.routing.budget_hooks import LEDGERS, check_own
from web_router.routing.ledger import Ledger
from web_router.types_errors import TARGET_GONE_STATUSES


def refund_unstarted(ledger: Ledger, call_id: str, exc: BaseException | str) -> None:
    """Charge nothing for the call's open attempt, ``call_id``, when ``exc`` proves it never started.

    Only the open attempt in the call's own trail, and only by the ledger of
    the call it was made in: an enclosing call's ledger, told of a nested
    call's failure, leaves its own open attempt as it is.
    """
    if not isinstance(exc, WebRouterError) or not nothing_started(exc) or not check_own(ledger)():
        return
    if getattr(exc, "status_code", None) in TARGET_GONE_STATUSES:
        return  # the page is gone, which a fetch backend may have billed finding out
    attempt = ledger.refund_open(call_id)
    if attempt is not None:
        _match_enclosing(ledger, attempt, 0.0, refunded=True)


def settle_reported(ledger: Ledger, response: object, empty: bool) -> None:
    """Charge the attempt whose answer ``response`` is (``empty``: an empty one) the cost it states: reported, or a
    keyless call's $0. The attempts that read part of the same answer (an extract's retry) settle to none."""
    cost = getattr(response, "cost_usd", None)
    if cost is None or getattr(response, "cost_source", None) not in COST_STATED:
        return
    for attempt, charge in ledger.settle_answer(getattr(response, "provider", None), empty, cost):
        _match_enclosing(ledger, attempt, charge)


def _match_enclosing(ledger: Ledger, attempt: Attempt, cost: float, *, refunded: bool = False) -> None:
    """Charge ``cost`` for ``attempt`` in every enclosing call's ledger that counted it too; ``refunded`` lists it at
    $0 in their records as well, as nothing ran."""
    for enclosing in LEDGERS.get():
        if enclosing is not ledger:
            enclosing.settle_counted(attempt, cost, refunded=refunded)
