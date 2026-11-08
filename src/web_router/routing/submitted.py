"""A job's start held to a shared budget: ``submit_research`` and ``submit_browse`` as the routers run them.

Experimental. A job's start is what bills, so inside ``wr.budget(...)`` it is
reserved like any other attempt, and research with no effort set runs at the
one the budget maps, as :mod:`web_router.routing.routed` maps it for
``research()``.
"""

from __future__ import annotations

from typing import Any, Protocol, TypeVar

from web_router.providers.base import Job
from web_router.routing.budget import budget_scope
from web_router.routing.contract import ToolName
from web_router.routing.pool import limited
from web_router.routing.routed import unrouted
from web_router.types import NormalizedRequest

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
    it starts as it would without one.
    """
    request, record = unrouted(backend, request, limited(None, router))  # before the scope, which admits a named one
    with budget_scope(None, tool, backend, request, router):
        job = backend.submit(request)
    job.decision = record
    return job


async def asubmitted(tool: ToolName, backend: _ASubmits[_JobT], request: NormalizedRequest, router: object) -> _JobT:
    """As :func:`submitted`, awaited."""
    request, record = unrouted(backend, request, limited(None, router))
    with budget_scope(None, tool, backend, request, router):
        job = await backend.asubmit(request)
    job.decision = record
    return job
