"""The merge rule's shape checks: whether a router's answer is a well-formed decision, and whether it decides anything.

Experimental. Apart from :mod:`.merge` so each stays small.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from typing import TypeGuard, get_args

from web_router.routing.composite import CompositeDecision
from web_router.routing.contract import RouteDecision, ToolName
from web_router.types_decision import DecisionMember, MemberOutcome

TOOLS: tuple[str, ...] = get_args(ToolName)
OUTCOMES: tuple[str, ...] = get_args(MemberOutcome)


def _names(value: object) -> bool:
    """A list or tuple of strings: a bare string is a sequence too, and never a ranking."""
    return isinstance(value, list | tuple) and all(isinstance(v, str) for v in value)


def _keyed_by_tool(value: object, check: Callable[[object], bool]) -> bool:
    """``None``, or a mapping keyed by method names whose every value passes ``check``."""
    if value is None:
        return True
    return isinstance(value, Mapping) and all(k in TOOLS and check(v) for k, v in value.items())


def _keyed_by_name(value: object) -> bool:
    """A method's options: a mapping keyed by option names, which are strings."""
    return isinstance(value, Mapping) and all(isinstance(k, str) for k in value)


def _probability(value: object) -> bool:
    """A finite number from 0 to 1: what ``confidence`` must be when set."""
    return isinstance(value, int | float) and not isinstance(value, bool) and 0 <= value <= 1


def well_formed(decision: object) -> TypeGuard[RouteDecision]:
    """Whether ``decision`` is a :class:`RouteDecision` whose fields have the shapes the contract gives them.

    A mapping keyed by anything but one of the five method names is malformed, not an unknown id: the
    contract types those keys, so a router that breaks them is broken, and its decision is set aside whole.
    """
    if not isinstance(decision, RouteDecision):
        return False
    exclude, confidence = decision.exclude, decision.confidence
    return (
        (decision.tools is None or _names(decision.tools))
        and _keyed_by_tool(decision.providers, _names)
        and _keyed_by_tool(decision.params, _keyed_by_name)
        and isinstance(exclude, Mapping)
        and all(isinstance(k, str) and isinstance(v, str) for k, v in exclude.items())
        and (decision.reason is None or isinstance(decision.reason, str))
        and (confidence is None or _probability(confidence))
        and _members_well_formed(decision)
    )


def _member(member: object) -> bool:
    """A :class:`DecisionMember` whose fields still have their types: the model checks them when built, not when set."""
    if not isinstance(member, DecisionMember):
        return False
    ms = member.ms
    return (
        isinstance(member.name, str)
        and member.outcome in OUTCOMES
        and isinstance(ms, int | float)
        and not isinstance(ms, bool)
        and _finite(ms)
        and ms >= 0
        and isinstance(member.counted, bool)
    )


def _finite(value: float) -> bool:
    """``value`` is finite; an int too large for a float is not."""
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _members_well_formed(decision: RouteDecision) -> bool:
    """A composite's ``members``: a tuple or list of :class:`DecisionMember`, at most one of them counted."""
    if not isinstance(decision, CompositeDecision):
        return True
    members = decision.members
    if not isinstance(members, tuple | list) or not all(_member(m) for m in members):
        return False
    if not isinstance(decision.timed_out, bool):
        return False
    return sum(m.counted for m in members) <= 1
