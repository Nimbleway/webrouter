"""The router contract's types: what a router is shown, and what it may answer.

Experimental: every name here may change before 1.0.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from web_router._urls import shown
from web_router.constants_routing import CANDIDATE_ID_SEPARATOR, MSG_BUDGET_VALUE, MSG_CANDIDATE_ID, PROMPT_PARAMS
from web_router.types import DecisionRecord

ToolName = Literal["search", "extract", "answer", "research", "browse"]
"""The methods a router may choose between. ``run_template`` and ``open_browser`` are never routed."""


def candidate_id(tool: str, provider: str) -> str:
    """The id every decision field names a candidate by: ``tool:provider``, as ``search:exa``."""
    return f"{tool}{CANDIDATE_ID_SEPARATOR}{provider}"


@dataclass(frozen=True, kw_only=True)
class Budget:
    """A cap on what one call may spend and how long it may wait.

    Every field is optional: a budget with nothing set caps nothing.
    """

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
        """
        Raises:
            ValueError: for a cap that is negative or not a number: it would cap nothing, or everything, silently.
        """
        for name in ("max_cost_usd", "time_limit_s"):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or not value >= 0):  # NaN fails ``>= 0``
                raise ValueError(MSG_BUDGET_VALUE.format(name=name, value=value))


@dataclass(frozen=True, kw_only=True)
class Candidate:
    """One (tool, provider) pair that could serve the call, with what it is estimated to cost and take."""

    tool: ToolName
    """The method this candidate calls."""

    provider: str
    """The backend that would serve it, by registry name."""

    est_cost_usd: float | None = None
    """Its estimated cost in US dollars, at list price. ``None`` when unknown or billed by tokens."""

    est_latency_s: float | None = None
    """Its p90 latency in seconds, as measured by the provider scans. ``None`` when unmeasured."""

    id: str = ""
    """``tool:provider``, the name every decision field uses. Filled in when left empty."""

    def __post_init__(self) -> None:
        expected = candidate_id(self.tool, self.provider)
        if not self.id:
            object.__setattr__(self, "id", expected)
        elif self.id != expected:
            raise ValueError(MSG_CANDIDATE_ID.format(id=self.id, expected=expected))


@dataclass(frozen=True, kw_only=True)
class RouteRequest:
    """What a router is shown for one call.

    Later versions add fields: a router must ignore the ones it does not know.
    """

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


@dataclass(frozen=True, kw_only=True)
class RouteDecision:
    """A router's answer. Every field is optional, and ``None`` means no opinion on it.

    A router that answers with fewer fields than a later version reads keeps
    working: what it leaves out is decided the way it would be with no router.
    """

    tools: Sequence[ToolName] | None = None
    """The methods to use, best first."""

    providers: Mapping[ToolName, Sequence[str]] | None = None
    """Per method, the backends to try, best first."""

    exclude: Mapping[str, str] = field(default_factory=dict)
    """Candidates never to try, by id, each with the reason."""

    params: Mapping[ToolName, Mapping[str, Any]] | None = None
    """Per method, options to call it with, such as research's ``effort``."""

    confidence: float | None = None
    """How sure the router is, from 0 to 1."""

    reason: str | None = None
    """Why, in a few words, for the decision record."""


@dataclass(frozen=True, kw_only=True)
class Plan:
    """A routed call, worked out and not yet run: the method, its options and the backends to try."""

    prompt: str
    """The prompt the plan was made for."""

    tool: ToolName
    """The method the call runs."""

    params: Mapping[str, Any] = field(default_factory=dict)
    """The options it runs with."""

    providers: Mapping[ToolName, Sequence[str]] = field(default_factory=dict)
    """Per method, the merged order of backends to try."""

    decision: DecisionRecord = field(default_factory=DecisionRecord)
    """How the plan was made: the same record a routed call's response carries."""

    def __repr__(self) -> str:
        """The plan without its prompt -- nor the query or task that repeat it -- and each URL as scheme and host."""
        params = {k: v for k, v in shown(self.params).items() if k not in PROMPT_PARAMS}
        return f"Plan(tool={self.tool!r}, params={params!r}, providers={dict(self.providers)!r})"
