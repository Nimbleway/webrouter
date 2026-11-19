"""The hooks a budgeted call's ledger adds to the route in force: its answer note and its refund.

Experimental. Apart from :mod:`.budget` so each stays small. :data:`LEDGERS`
holds the ledgers of the budgeted calls in flight; :func:`check_own` says
whether a ledger is the call's own where an attempt runs, the one that
reserves it in the shared budget and so releases it when it is taken back.
"""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar

from web_router.routing.attempts import AnswerHook, Attempt, RefundHook
from web_router.routing.contract import ToolName
from web_router.routing.ledger import Ledger

LEDGERS: ContextVar[tuple[Ledger, ...]] = ContextVar("web_router_budget_ledgers", default=())
"""The ledgers of the budgeted calls in flight here, outermost first: each one checks every attempt made inside it."""


def check_own(ledger: Ledger) -> Callable[[], bool]:
    """Whether ``ledger``, read when called, is the call's own where an attempt runs, and not an enclosing one."""

    def own() -> bool:
        in_force = LEDGERS.get()  # a ledger not in it is a recorded call's own, never an enclosing one
        return not any(held is ledger for held in in_force) or in_force[-1] is ledger

    return own


def answered(outer: AnswerHook | None, ledger: Ledger) -> AnswerHook:
    """The answer hook: ``outer``'s note, then ``ledger``'s."""

    def note(tool: ToolName, provider: str) -> None:
        if outer is not None:
            outer(tool, provider)
        ledger.contributed(tool, provider)

    return note


def refunds(outer: RefundHook | None, ledger: Ledger, own: Callable[[], bool]) -> RefundHook:
    """The refund hook: ``outer``'s, then ``ledger`` takes the attempt back, releasing it in the pool if its own."""

    def refund(attempt: Attempt) -> None:
        if outer is not None:
            outer(attempt)
        ledger.undo(attempt, release=own())

    return refund
