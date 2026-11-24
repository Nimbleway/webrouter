"""The router contract's types: what a router is shown, and what it may answer.

:class:`Budget` is stable; the router's types -- :class:`Candidate`, :class:`RouteRequest`,
:class:`RouteDecision` and :class:`.plan_type.Plan` -- are experimental and may change before 1.0.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal

from pydantic import ConfigDict

from web_router.constants_routing import MSG_BUDGET_VALUE, MSG_CANDIDATE_ID
from web_router.exceptions_typed import InvalidParameterError
from web_router.routing.entry_names import candidate_id as candidate_id  # re-exported: its callers import it here
from web_router.types_decision import Prefer as Prefer  # re-exported beside the contract types

ToolName = Literal["search", "extract", "answer", "research", "browse"]
"""The methods a router may choose between. ``live_data`` and ``open_browser`` are never routed."""


_WIRE_CONFIG = ConfigDict(use_attribute_docstrings=True)
"""How pydantic reads these types for the wire format's JSON Schema: each field's docstring is its description."""


def _finite_cap(value: object) -> bool:
    """A real number, finite as a float, and not negative."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return False
    try:
        return math.isfinite(float(value)) and value >= 0
    except OverflowError:  # an int too large for a float, such as 10**400
        return False


@dataclass(frozen=True, kw_only=True)
class Budget:
    """A cap on what one call may spend and how long it may wait.

    Every field is optional: a budget with nothing set caps nothing.
    """

    __pydantic_config__: ClassVar[ConfigDict] = _WIRE_CONFIG

    max_cost_usd: float | None = None
    """The most the call may spend, in US dollars, at estimated list prices.

    Under a cost cap, a candidate whose price is unknown is never run.
    """

    time_limit_s: float | None = None
    """The most seconds the call may wait: a cap on waiting, not a latency estimate.

    It bounds when web-router stops waiting and starting attempts: a provider call that blocks past it
    returns late. The built-in backends clamp each HTTP request to the time left, so theirs do not.
    """

    strict: bool = False
    """Under a time cap, drop the candidates with no measured p90 latency instead of trying them."""

    def __post_init__(self) -> None:
        """Raise :class:`InvalidParameterError`, a :class:`ValueError`, for a cap that is not a finite number of 0 or
        more -- negative, text, a bool, NaN, infinity, or too large for a float: it would cap nothing or everything."""
        for name in ("max_cost_usd", "time_limit_s"):
            value = getattr(self, name)
            if value is not None and not _finite_cap(value):
                raise InvalidParameterError(name, MSG_BUDGET_VALUE.format(name=name, value=value))


@dataclass(frozen=True, kw_only=True)
class Candidate:
    """One (tool, provider) pair, or one of its vendor modes, that could serve the call, with its cost and time."""

    __pydantic_config__: ClassVar[ConfigDict] = _WIRE_CONFIG

    tool: ToolName
    """The method this candidate calls."""

    provider: str
    """The backend that would serve it, by registry name."""

    mode: str | None = None
    """The backend's vendor mode, canonical (``exa-instant+lite``); ``None`` for its default. Offered only to a router
    that opts in with ``route_modes`` or names the mode in its ``extra_modes``."""

    est_cost_usd: float | None = None
    """Its estimated cost in US dollars, at list price, or a token-billed backend's measured mean; ``None`` unknown."""

    est_latency_s: float | None = None
    """Its p90 latency in seconds, as measured by the provider scans. ``None`` when unmeasured."""

    measured: bool = True
    """``False`` for a keyless tier or a member with its own ``provider_options``: a backend's measured score does
    not describe it, so a router's accuracy scores leave it unknown. Sent on the wire only when ``False``."""

    id: str = ""
    """``tool:provider``, or ``tool:provider:mode`` with a mode: the name every decision field uses. Filled in when
    left empty, refused when it differs."""

    def __post_init__(self) -> None:
        expected = candidate_id(self.tool, self.provider, self.mode)
        if self.id not in ("", expected):
            raise ValueError(MSG_CANDIDATE_ID.format(id=self.id, expected=expected))
        object.__setattr__(self, "id", expected)

    @property
    def is_backend(self) -> bool:  # a backend at its default mode, the one a call runs; not a vendor mode
        return self.mode is None


@dataclass(frozen=True, kw_only=True)
class RouteRequest:
    """What a router is shown for one call.

    Later versions add fields: a router must ignore the ones it does not know.
    """

    __pydantic_config__: ClassVar[ConfigDict] = _WIRE_CONFIG

    prompt: str
    """The query for search, answer and research; the URLs, one per line, for extract; the task for browse."""

    candidates: tuple[Candidate, ...]
    """Every (tool, provider) pair that could serve the call and that the budget allows."""

    deadline_s: float
    """Seconds the router has to answer before the static order is used instead."""

    urls: tuple[str, ...] = ()
    """The URLs the call names: extract's URLs, or browse's ``start_url``."""

    budget: Budget | None = None
    """The call's budget, when it has one."""

    pinned_tool: ToolName | None = None
    """The method the caller called. Set, the router orders backends only and does not change the tool."""

    prefer: Prefer | None = None
    """What the router is asked to prefer among the candidates: ``cheap``, ``fast`` or ``accurate``; ``None`` for no
    preference. A router built with one sets it on the request it asks or sends; it orders, and ``budget`` caps."""


@dataclass(frozen=True, kw_only=True)
class RouteDecision:
    """A router's answer. Every field is optional, and ``None`` means no opinion on it.

    A router that answers with fewer fields than a later version reads keeps
    working: what it leaves out is decided the way it would be with no router.
    """

    __pydantic_config__: ClassVar[ConfigDict] = _WIRE_CONFIG

    tools: Sequence[ToolName] | None = None
    """The methods to use, best first."""

    providers: Mapping[ToolName, Sequence[str]] | None = None
    """Per method, the backends to try, best first."""

    attempts: Sequence[str] | None = None
    """Candidate ids to try, best first, vendor modes included; non-empty, they replace ``tools`` and ``providers``."""

    exclude: Mapping[str, str] = field(default_factory=dict)
    """Candidates never to try, by id, each with the reason."""

    params: Mapping[ToolName, Mapping[str, Any]] | None = None
    """Per method, options to call it with, such as research's ``effort``."""

    confidence: float | None = None
    """How sure the router is, from 0 to 1."""

    reason: str | None = None
    """Why, in a few words, for the decision record."""

    prefer: Prefer | None = None
    """The preference the router ranked by, for the decision record; ``None`` for none. Sent only when set."""
