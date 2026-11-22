"""Holding a call to its :class:`~web_router.Budget`.

Every method that takes ``budget=`` runs inside
:func:`budget_scope`. With no budget the scope sets nothing and the call runs
exactly as it would without one. With a budget:

- the call runs under the per-call deadline, set to ``time_limit_s``, so each
  attempt gets the time left; research and browse wait at most
  ``min(timeout, time left)``, and a time cap reached while a job runs raises
  the method's own timeout error, which carries the job's handle. The cap
  limits how long the caller waits, not what the job spends; a provider call
  that blocks past it returns late, though the built-in backends clamp each
  HTTP request to the time left (no lower than half a second for a job's);
- before each attempt -- each member of an ``auto`` chain, or the one backend
  named -- a :class:`~.ledger.Ledger` checks the estimate and the p90 against
  what is left, and skips a candidate that does not fit, recording why; an
  attempt that never started is then charged nothing, and an answer with a
  reported cost is charged that (:mod:`.settlement`);
- a call where the budget skipped every candidate raises
  :class:`~web_router.BudgetExceededError`, naming each skip;
- the response's ``decision`` carries the estimated spend, every attempt and
  every candidate moved past: skipped, failed, or answered empty.

:func:`recorded` gives a routed call with no budget the same record, from a ledger that caps nothing.

Inside a shared budget (:mod:`.pool`), a call runs here even with no
``budget=`` of its own: its budget is the tighter of the two, each attempt it
admits is reserved in the pool, and what it reserved is settled when it ends.
A call a backend makes inside another budgeted call runs here too, on a
budget that caps nothing: the enclosing call's check counts it toward that
call's cap, and it reserves its attempts in every shared budget the enclosing
calls spend from. Only the ledger of the call an attempt is made in reserves
and settles it there, so each attempt is reserved once.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import Generic, TypeVar

from pydantic import BaseModel

from web_router.constants import PROVIDER_AUTO
from web_router.constants_budget import SKIP_NO_TIME, SKIP_ROUTE_REFUSED
from web_router.exceptions import DeadlineExceededError, WebRouterError
from web_router.providers._chain import _empty_response
from web_router.providers._deadline import Deadline, call_deadline, current_deadline, job_deadline
from web_router.routing.attempts import Attempt, CallRoute, admitted, call_route, current_route
from web_router.routing.budget_hooks import LEDGERS, answered, check_own, refunds
from web_router.routing.contract import Budget, ToolName, candidate_id
from web_router.routing.ledger import Ledger
from web_router.routing.pool import PoolChain, chain_in_force
from web_router.routing.record import prompt_of, stamp
from web_router.routing.settlement import refund_unstarted, settle_reported
from web_router.types import NormalizedRequest
from web_router.types_decision import DecisionRecord

_BackendT = TypeVar("_BackendT")
_ResponseT = TypeVar("_ResponseT", bound=BaseModel)


@dataclass(frozen=True)
class BudgetedCall(Generic[_BackendT]):
    """The backend a budgeted call runs on, and what settles its response."""

    backend: _BackendT
    ledger: Ledger | None = None

    @property
    def budget(self) -> Budget | None:
        """The budget the call is held to: its own, tightened by a shared budget's remainder; ``None`` with neither."""
        return None if self.ledger is None else self.ledger.budget

    def settle(self, response: _ResponseT) -> _ResponseT:
        """``response`` with its estimated spend recorded and its reported cost charged; as it came with no budget."""
        if self.ledger is None:
            return response
        decision: DecisionRecord | None = getattr(response, "decision", None)
        empty = _empty_response(response)
        settle_reported(self.ledger, response, empty)
        record = self.ledger.record(decision, answered=getattr(response, "provider", None), empty=empty)
        return response.model_copy(update={"decision": record})


@contextmanager
def budget_scope(
    budget: Budget | None,
    tool: ToolName,
    backend: _BackendT,
    request: NormalizedRequest,
    router: object = None,
) -> Iterator[BudgetedCall[_BackendT]]:
    """Hold ``budget`` over ``backend``'s call of ``tool`` with ``request``, made inside the block.

    ``router`` is the router making the call: only a shared budget it opened applies.

    Raises:
        BudgetExceededError: when the budget skips every candidate, before any starts.
    """
    pool = None if router is None else chain_in_force(router)
    if pool is not None:
        budget = pool.limit(budget)
    held = LEDGERS.get()
    if budget is None and held:
        budget = Budget()  # a nested call: it caps nothing of its own, but the calls it is made in check it
    if budget is None:
        yield BudgetedCall(backend)
        return
    ledger = Ledger(budget, _with_enclosing(pool, held), stamp=stamp(prompt_of(tool, request)[0]))
    token = LEDGERS.set((*held, ledger))
    try:
        with _held(ledger, held, tool, backend, request) as call:
            yield call
    finally:
        LEDGERS.reset(token)
        reserved = ledger.close()
        if ledger.pool is not None:
            ledger.pool.settle(reserved)


def _with_enclosing(chain: PoolChain | None, held: tuple[Ledger, ...]) -> PoolChain | None:
    """``chain`` and every pool the enclosing calls in ``held`` spend from, innermost first, once each.

    The call reserves its attempts in all of them: they are made on those calls'
    behalf, whichever router makes them, and the enclosing calls reserve none.
    """
    enclosing = [pool for ledger in reversed(held) if ledger.pool is not None for pool in ledger.pool.pools]
    pools = tuple(dict.fromkeys([*(chain.pools if chain is not None else ()), *enclosing]))
    return PoolChain(pools) if pools else None


@contextmanager
def _held(
    ledger: Ledger, held: tuple[Ledger, ...], tool: ToolName, backend: _BackendT, request: NormalizedRequest
) -> Iterator[BudgetedCall[_BackendT]]:
    """The block under ``ledger``, inside the calls ``held`` holds: its deadline, its check and its refusal."""
    expires = None if ledger.pool is None else ledger.pool.expires_at()
    outside = current_deadline()
    with call_deadline(ledger.budget.time_limit_s), job_deadline(expires), call_route(_hooked(ledger, held)):
        name = getattr(backend, "name", PROVIDER_AUTO)
        _refuse_late(ledger, candidate_id(tool, name), name, outside)
        if name != PROVIDER_AUTO and not admitted(tool, name, request):
            # Through the same hook auto's members pass, so a route in force can refuse it too;
            # the ledger's admit, last in that hook, reserves it in a shared budget.
            ledger.skipped.setdefault(candidate_id(tool, name), SKIP_ROUTE_REFUSED)
            raise ledger.refusal()
        try:
            yield BudgetedCall(backend, ledger)
        except WebRouterError as exc:
            refund_unstarted(ledger, candidate_id(tool, name), exc)  # a named backend's refusal; auto's are noted
            # An `auto` chain whose every member the budget skipped ends as one with no backend
            # configured would. Nothing was sent, so that error is no cause: the budget is.
            if ledger.skipped and not ledger.tried and not ledger.sent:
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


def _refuse_late(ledger: Ledger, call_id: str, name: str, outside: Deadline | None) -> None:
    """Refuse the call before anything is admitted or reserved when its deadline has already passed.

    The budget's own deadline -- its time cap or the shared clock -- raises
    :class:`~web_router.BudgetExceededError`. A deadline the budget did not set,
    an enclosing call's, raises :class:`~web_router.DeadlineExceededError`, as
    the call would with no budget.
    """
    deadline = current_deadline()
    if deadline is None or deadline.left() > 0:
        return
    if deadline is outside:
        raise DeadlineExceededError(name, deadline.seconds)
    ledger.skipped[call_id] = SKIP_NO_TIME
    raise ledger.refusal()


def _hooked(ledger: Ledger, held: tuple[Ledger, ...] = ()) -> CallRoute:
    """The route in force, if any, with ``ledger``'s hooks after its own: a member either refuses is never charged.

    The hook in force runs the check of every enclosing call's ledger in
    ``held``, each counting the attempt toward its own cap; when ``ledger``
    then refuses it, each takes it back. Only the innermost ledger in force
    where the attempt runs -- its own call's -- reserves it in the shared budget.
    A failure, or a retry's part of an answer, is noted in ``ledger``'s hop trail after the route's own note.
    """
    route = current_route() or CallRoute()
    outer, outer_failed = route.before_attempt, route.on_failure

    own = check_own(ledger)

    def check(attempt: Attempt) -> bool:
        return ledger.admit(attempt, reserve=own())

    def admit(attempt: Attempt) -> bool:
        if outer is None:
            return check(attempt)
        if not outer(attempt):
            return False
        if check(attempt):
            return True
        for enclosing in held:
            enclosing.undo(attempt)
        return False

    def note(tool: ToolName, provider: str, exc: BaseException | str) -> None:
        if outer_failed is not None:
            outer_failed(tool, provider, exc)
        refund_unstarted(ledger, candidate_id(tool, provider), exc)
        ledger.failed(tool, provider, exc)

    on_answer, on_refund = answered(route.on_answer, ledger), refunds(route.on_refund, ledger, own)
    return replace(route, before_attempt=admit, on_failure=note, on_answer=on_answer, on_refund=on_refund)
