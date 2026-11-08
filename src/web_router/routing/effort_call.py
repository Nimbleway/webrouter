"""The one research effort a call sends, chosen once: before its candidates are priced or a router is asked.

Experimental. The request is shared by every backend the call may try, so the
effort is :func:`~web_router.routing.params.shared_effort`'s pick over them;
an effort the caller set stands. The candidate builder prices research at it,
and a call no router orders -- on ``auto`` or on a backend the caller named --
runs at it, with the choice on its decision record.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, TypeVar

from pydantic import BaseModel

from web_router.constants import PROVIDER_AUTO
from web_router.constants_candidates import AUTO_GETTERS
from web_router.routing.contract import Budget, ToolName
from web_router.routing.eligible import eligible_members
from web_router.routing.params import EffortChoice, shared_effort
from web_router.types import NormalizedRequest, ResearchRequest, provider_name
from web_router.types_decision import DecisionRecord

_RequestT = TypeVar("_RequestT", bound=NormalizedRequest)
_ResponseT = TypeVar("_ResponseT", bound=BaseModel)


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


class Only:
    """A router whose ``auto`` for ``tool`` is ``chain``: the candidates of the chain the call runs on."""

    def __init__(self, tool: ToolName, chain: Any) -> None:
        setattr(self, AUTO_GETTERS[tool], lambda _name: chain)


def unrouted(chain: Any, request: _RequestT, budget: Budget | None) -> tuple[_RequestT, DecisionRecord | None]:
    """A call no router orders: with a budget, research still runs at the effort the budget maps.

    Over the backends of the chain the call runs on that can serve it -- or the one backend the caller named
    -- by :func:`choose_effort`; the record carries the choice.
    """
    if not isinstance(request, ResearchRequest) or budget is None:
        return request, None
    if getattr(chain, "name", None) == PROVIDER_AUTO:
        names = [name for name, _ in eligible_members(Only("research", chain), "research", request)[0]]
    else:
        names = [provider_name(chain.name)]
    request, effort = choose_effort(names, request, budget)
    return request, None if effort is None else DecisionRecord(**effort.fields())


def named(chain: Any, request: _RequestT, budget: Budget | None) -> tuple[_RequestT, DecisionRecord | None]:
    """A call to a backend the caller named: its research effort, as :func:`unrouted` maps it; else unchanged."""
    if getattr(chain, "name", PROVIDER_AUTO) == PROVIDER_AUTO:
        return request, None
    return unrouted(chain, request, budget)


def with_record(response: _ResponseT, record: DecisionRecord | None) -> _ResponseT:
    """``response`` carrying ``record`` as its ``decision``; unchanged when there is none."""
    return response if record is None else response.model_copy(update={"decision": record})
