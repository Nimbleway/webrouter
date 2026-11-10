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


def composed(decision: RouteDecision | None, members: list[DecisionMember]) -> CompositeDecision:
    """``decision``'s fields with ``members`` beside them; with no decision, an empty one."""
    kept = {} if decision is None else {f.name: getattr(decision, f.name) for f in fields(RouteDecision)}
    return CompositeDecision(**kept, members=tuple(members))


def members_of(decision: object) -> list[DecisionMember] | None:
    """The members a composite's decision lists, for the record; ``None`` for any other decision."""
    if not isinstance(decision, CompositeDecision):
        return None
    return list(decision.members)


def none_answered(decision: object) -> bool:
    """Whether ``decision`` is a composite's whose members all failed or answered empty."""
    return isinstance(decision, CompositeDecision) and not any(member.counted for member in decision.members)
