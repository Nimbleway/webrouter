"""The one research effort a call sends, chosen once: before its candidates are priced or a router is asked.

Experimental. The request is shared by every backend the call may try, so the
effort is :func:`~web_router.routing.params.shared_effort`'s pick over them;
an effort the caller set stands. The candidate builder prices research at it.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TypeVar

from web_router.routing.contract import Budget
from web_router.routing.params import EffortChoice, shared_effort
from web_router.types import NormalizedRequest, ResearchRequest

_RequestT = TypeVar("_RequestT", bound=NormalizedRequest)


def choose_effort(
    names: Sequence[str], request: _RequestT, budget: Budget | None
) -> tuple[_RequestT, EffortChoice | None]:
    """``request`` with the effort the budget maps over the backends ``names``, and that choice.

    Unchanged, with no choice, for a request that is not research, one whose caller set an effort, and a
    budget that maps none.
    """
    if not isinstance(request, ResearchRequest) or request.effort is not None:
        return request, None
    effort = shared_effort(names, budget)
    if effort is None:
        return request, None
    return request.model_copy(update={"effort": effort.effort}), effort
