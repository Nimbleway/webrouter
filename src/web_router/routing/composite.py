"""What a composite router answers with: a decision, and the routers it asked to reach it.

Experimental. A router returns only a :class:`RouteDecision`, so a router that
asks other routers -- the built-in ``fallback`` -- answers with
:class:`CompositeDecision`, the same decision with ``members`` beside it, and
the decision record copies them to ``members``. Nothing else sets it: the wire
format reads a plain :class:`RouteDecision`, so a router service cannot write
the record's members.
"""

from __future__ import annotations

from dataclasses import dataclass, fields

from web_router.constants_merge import ROUTER_EMPTY, ROUTER_TIMEOUT
from web_router.routing.contract import RouteDecision
from web_router.types_decision import DecisionMember


@dataclass(frozen=True, kw_only=True)
class CompositeDecision(RouteDecision):
    """A :class:`RouteDecision` from a router that asked others, with what each of them came to.

    Experimental. With no member's decision taken, every decision field is left unset: the call runs on the
    static order and the record says ``router_empty``.
    """

    members: tuple[DecisionMember, ...] = ()
    """Every router asked, in order: its name, its outcome, its milliseconds, and whether its decision counted."""

    timed_out: bool = False
    """Whether no member answered because the time ran out: the last one asked was cut short by it, or no time
    was left to ask the next. The record then says ``router_timeout``, not ``router_empty``."""


def composed(
    decision: RouteDecision | None, members: list[DecisionMember], *, timed_out: bool = False
) -> CompositeDecision:
    """``decision``'s fields with ``members`` beside them; with no decision, an empty one, timed out or not."""
    kept = {} if decision is None else {f.name: getattr(decision, f.name) for f in fields(RouteDecision)}
    return CompositeDecision(**kept, members=tuple(members), timed_out=decision is None and timed_out)


def members_of(decision: object) -> list[DecisionMember] | None:
    """The members a composite's decision lists, for the record; ``None`` for any other decision."""
    if not isinstance(decision, CompositeDecision):
        return None
    return list(decision.members)


def decides_nothing(decision: RouteDecision) -> bool:
    """A decision that decides nothing: no method, no backend, no exclusion and no option."""
    providers, params = decision.providers or {}, decision.params or {}
    return not (decision.tools or any(providers.values()) or decision.exclude or any(params.values()))


def none_answered(decision: object) -> str | None:
    """Why a composite gave no answer: ``router_timeout`` or ``router_empty``; ``None`` when it decides something.

    Judged by the decision itself, as the ``fallback`` router judges a member's, whatever ``members`` or
    ``timed_out`` say. ``None`` too for any decision that is not a composite's.
    """
    if not isinstance(decision, CompositeDecision) or not decides_nothing(decision):
        return None
    return ROUTER_TIMEOUT if decision.timed_out else ROUTER_EMPTY
