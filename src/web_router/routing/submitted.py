"""A job's start held to a shared budget: ``submit_research`` and ``submit_browse`` as the routers run them.

A job's start is what bills, so inside ``wr.budget(...)`` it is
reserved like any other attempt, and research with no effort set runs at the
one the budget maps, as :mod:`web_router.routing.routed` maps it for
``research()``.
"""

from __future__ import annotations

from functools import partial
from time import monotonic
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

from web_router.providers.base import Job
from web_router.routing.budget import BudgetedCall, budget_scope
from web_router.routing.contract import ToolName
from web_router.routing.effort_call import unrouted
from web_router.routing.metered import metered
from web_router.routing.pool import limited
from web_router.spend_record import log_spent
from web_router.types import NormalizedRequest
from web_router.types_decision import DecisionRecord

_ResponseT = TypeVar("_ResponseT", bound=BaseModel)
_JobT = TypeVar("_JobT", bound="Job[Any, Any, Any]", covariant=True)


class _Submits(Protocol[_JobT]):
    def submit(self, request: Any) -> _JobT: ...


class _ASubmits(Protocol[_JobT]):
    async def asubmit(self, request: Any) -> _JobT: ...


def submitted(tool: ToolName, backend: _Submits[_JobT], request: NormalizedRequest, router: object) -> _JobT:
    """``backend``'s job for ``request``, its start held to ``router``'s shared budget in force, if any (:mod:`.pool`).

    A job's start is what bills, so inside ``wr.budget(...)`` each attempt to
    start one is reserved like any other, and research with no effort set runs
    at the one the budget maps, as ``research()`` does; with no shared budget
    it starts as it would without one. The job's ``decision`` carries the
    effort and, under a budget, what it charged the start, as ``research()``'s does. Its answer is
    metered and written to the router's spend log once, when it is first seen, as a call's is.
    """
    started = monotonic()
    request, record = unrouted(backend, request, limited(None, router))  # before the scope, which admits a named one
    with budget_scope(None, tool, backend, request, router) as scope:
        job = backend.submit(request)
        job.decision = _charged(scope, record, job.provider)
    job.meter(partial(_answered, job=job, tool=tool, request=request, started=started, router=router))
    return job


async def asubmitted(tool: ToolName, backend: _ASubmits[_JobT], request: NormalizedRequest, router: object) -> _JobT:
    """As :func:`submitted`, awaited."""
    started = monotonic()
    request, record = unrouted(backend, request, limited(None, router))
    with budget_scope(None, tool, backend, request, router) as scope:
        job = await backend.asubmit(request)
        job.decision = _charged(scope, record, job.provider)
    job.meter(partial(_answered, job=job, tool=tool, request=request, started=started, router=router))
    return job


def _answered(
    response: _ResponseT,
    *,
    job: Job[Any, Any, Any],
    tool: ToolName,
    request: NormalizedRequest,
    started: float,
    router: object,
) -> _ResponseT:
    """``response`` with what the run took and cost, written to ``router``'s spend log when it keeps one.

    The entry carries the job's ``decision``, which the job puts on the answer only after this runs.
    """
    answered = metered(response, tool, request, started)
    logged = answered if job.decision is None else answered.model_copy(update={"decision": job.decision})
    log_spent(getattr(router, "_spend_sink", None), logged, tool)
    return answered


def _charged(scope: BudgetedCall[Any], record: DecisionRecord | None, provider: str) -> DecisionRecord | None:
    """``record`` with what ``scope``'s budget charged the start ``provider`` made; as it is with no budget."""
    return record if scope.ledger is None else scope.ledger.record(record, answered=provider)
