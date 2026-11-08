"""Holding a call to its :class:`~web_router.Budget`.

Experimental. Every method that takes ``budget=`` runs inside
:func:`budget_scope`. With no budget the scope sets nothing and the call runs
exactly as it would without one. With a budget:

- the call runs under the per-call deadline, set to ``time_limit_s``, so each
  attempt gets the time left; research and browse wait at most
  ``min(timeout, time left)``, and a time cap reached while a job runs raises
  the method's own timeout error, which carries the job's handle. The cap
  limits how long the caller waits, not what the job spends. It bounds when
  web-router stops waiting and starting attempts: a provider call that blocks
  past it returns late. The built-in backends clamp each HTTP request to the
  time left (no lower than half a second for a job's), so theirs do not;
- before each attempt -- each member of an ``auto`` chain, in the order the
  call tries them, or the one backend named -- a :class:`~.ledger.Ledger`
  checks the estimate and the p90 against what is left, and skips a candidate
  that does not fit, recording why;
- a call where the budget skipped every candidate raises
  :class:`~web_router.BudgetExceededError`, naming each skip;
- the response's ``decision`` carries the estimated spend and every attempt,
  labelled an estimate, and every candidate moved past: skipped, failed, or
  answered empty.

:func:`recorded` gives a routed call with no budget the same record of its
attempts, from a ledger that caps nothing.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import Generic, TypeVar

from pydantic import BaseModel

from web_router.constants import PROVIDER_AUTO
from web_router.constants_budget import SKIP_ROUTE_REFUSED
from web_router.exceptions import WebRouterError
from web_router.providers._chain import _empty_response
from web_router.providers._deadline import call_deadline
from web_router.routing.attempts import CallRoute, admitted, call_route, current_route
from web_router.routing.contract import Budget, ToolName, candidate_id
from web_router.routing.ledger import Ledger
from web_router.routing.record import prompt_of, stamp
from web_router.types import NormalizedRequest
from web_router.types_decision import DecisionRecord

_BackendT = TypeVar("_BackendT")
_ResponseT = TypeVar("_ResponseT", bound=BaseModel)


@dataclass(frozen=True)
class BudgetedCall(Generic[_BackendT]):
    """The backend a budgeted call runs on, and what settles its response."""

    backend: _BackendT
    ledger: Ledger | None = None

    def settle(self, response: _ResponseT) -> _ResponseT:
        """``response`` with the call's estimated spend in its decision record; as it came with no budget."""
        if self.ledger is None:
            return response
        decision: DecisionRecord | None = getattr(response, "decision", None)
        answered = getattr(response, "provider", None)
        record = self.ledger.record(decision, answered=answered, empty=_empty_response(response))
        return response.model_copy(update={"decision": record})


@contextmanager
def budget_scope(
    budget: Budget | None, tool: ToolName, backend: _BackendT, request: NormalizedRequest
) -> Iterator[BudgetedCall[_BackendT]]:
    """Hold ``budget`` over ``backend``'s call of ``tool`` with ``request``, made inside the block.

    Raises:
        BudgetExceededError: when the budget skips every candidate, before any starts.
    """
    if budget is None:
        yield BudgetedCall(backend)
        return
    ledger = Ledger(budget, stamp=stamp(prompt_of(tool, request)[0]))
    with call_deadline(budget.time_limit_s), call_route(_hooked(ledger)):
        name = getattr(backend, "name", PROVIDER_AUTO)
        if name != PROVIDER_AUTO and not admitted(tool, name, request):
            # Through the same hook auto's members pass, so a route in force can refuse it too.
            ledger.skipped.setdefault(candidate_id(tool, name), SKIP_ROUTE_REFUSED)
            raise ledger.refusal()
        try:
            yield BudgetedCall(backend, ledger)
        except WebRouterError:
            # An `auto` chain whose every member the budget skipped ends as one with no
            # backend configured would. Nothing ran, so that error is no cause: the budget is.
            if ledger.skipped and not ledger.tried:
                raise ledger.refusal() from None
            raise


@contextmanager
def recorded(scope: BudgetedCall[_BackendT]) -> Iterator[BudgetedCall[_BackendT]]:
    """``scope``; or, for a call with no budget, one whose ledger caps nothing, so its record holds what it tried."""
    if scope.ledger is not None:
        yield scope
        return
    ledger = Ledger(Budget())
    with call_route(_hooked(ledger)):
        yield replace(scope, ledger=ledger)


def _hooked(ledger: Ledger) -> CallRoute:
    """The route in force, if any, with the ledger's hooks after its own: a member it refuses is never charged."""
    route = current_route() or CallRoute()
    outer, outer_failed = route.before_attempt, route.on_failure
    admit = ledger.admit if outer is None else (lambda attempt: outer(attempt) and ledger.admit(attempt))

    def note(tool: ToolName, provider: str, exc: BaseException) -> None:
        if outer_failed is not None:
            outer_failed(tool, provider, exc)
        ledger.failed(tool, provider, exc)

    return replace(route, before_attempt=admit, on_failure=note)
