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
  labelled an estimate.

Inside a shared budget (:mod:`.pool`), a call runs here even with no
``budget=`` of its own: its budget is the tighter of the two, each attempt it
admits is reserved in the pool, and what it reserved is settled when it ends.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import Generic, TypeVar

from pydantic import BaseModel

from web_router.constants import PROVIDER_AUTO
from web_router.constants_budget import SKIP_NO_TIME, SKIP_ROUTE_REFUSED
from web_router.constants_routing import CANDIDATE_ID_SEPARATOR
from web_router.exceptions import WebRouterError
from web_router.providers._deadline import call_deadline, current_deadline, job_deadline
from web_router.routing.attempts import Attempt, CallRoute, admitted, call_route, current_route
from web_router.routing.contract import Budget, ToolName
from web_router.routing.ledger import Ledger
from web_router.routing.pool import current_pool
from web_router.types import DecisionRecord, NormalizedRequest

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
        return response.model_copy(update={"decision": self.ledger.record(decision)})


@contextmanager
def budget_scope(
    budget: Budget | None, tool: ToolName, backend: _BackendT, request: NormalizedRequest
) -> Iterator[BudgetedCall[_BackendT]]:
    """Hold ``budget`` over ``backend``'s call of ``tool`` with ``request``, made inside the block.

    Raises:
        BudgetExceededError: when the budget skips every candidate, before any starts.
    """
    pool = current_pool()
    if pool is not None:
        budget = pool.limit(budget)
    if budget is None:
        yield BudgetedCall(backend)
        return
    ledger = Ledger(budget, pool)
    try:
        with _held(ledger, tool, backend, request) as call:
            yield call
    finally:
        if pool is not None:
            pool.settle(ledger.spent)


@contextmanager
def _held(
    ledger: Ledger, tool: ToolName, backend: _BackendT, request: NormalizedRequest
) -> Iterator[BudgetedCall[_BackendT]]:
    """The block under ``ledger``: its deadline, its check before each attempt, and its refusal."""
    expires = None if ledger.pool is None else ledger.pool.expires_at()
    with call_deadline(ledger.budget.time_limit_s), job_deadline(expires), call_route(_hooked(ledger.admit)):
        name = getattr(backend, "name", PROVIDER_AUTO)
        deadline = current_deadline()
        if deadline is not None and deadline.left() <= 0:
            # The call's deadline -- its own, the shared clock's, or an enclosing call's -- has passed:
            # say so before `auto` stops at it untried, and before anything is reserved.
            ledger.skipped[f"{tool}{CANDIDATE_ID_SEPARATOR}{name}"] = SKIP_NO_TIME
            raise ledger.refusal()
        if name != PROVIDER_AUTO and not admitted(tool, name, request):
            # Through the same hook auto's members pass, so a route in force can refuse it too;
            # the ledger's admit, last in that hook, reserves it in a shared budget.
            ledger.skipped.setdefault(f"{tool}{CANDIDATE_ID_SEPARATOR}{name}", SKIP_ROUTE_REFUSED)
            raise ledger.refusal()
        try:
            yield BudgetedCall(backend, ledger)
        except WebRouterError as exc:
            # An `auto` chain whose every member the budget skipped ends as one with no
            # backend configured would; the budget is what stopped it, so say so.
            if ledger.skipped and not ledger.tried:
                raise ledger.refusal() from exc
            raise


def _hooked(admit: Callable[[Attempt], bool]) -> CallRoute:
    """The route in force, if any, with ``admit`` after its own hook: a member it refuses is never charged."""
    route = current_route() or CallRoute()
    outer = route.before_attempt
    if outer is None:
        return replace(route, before_attempt=admit)
    return replace(route, before_attempt=lambda attempt: outer(attempt) and admit(attempt))
