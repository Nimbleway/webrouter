"""The decision record: how a routed or budgeted call chose its backend. Experimental: it may change before 1.0.

Apart from :mod:`web_router.types` so that file stops growing; it re-exports
these, and imports nothing of ours. Every routable response and
:class:`~web_router.Plan` carries one in ``decision``.

**Privacy.** The record holds no key values and no prompt text. The prompt is
there only as ``prompt_sha256``: an unsalted SHA-256 of what the router reads --
the query or task, or for extract the whole URLs, one per line, query string
included; a plan's is its whole prompt, any URL in it included. It is a
pseudonymous id, not anonymisation: anyone holding a prompt or URL they can
guess can confirm it from the hash, and equal prompts share one. Elsewhere in
the record a URL shows only its scheme and host. Candidate ids
(``tool:provider``) do show which backends are configured. ``reason``,
``excluded`` and ``ignored`` hold a router's own words, URLs cut the same way:
a router that writes the prompt there puts it in the record.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class DecisionHop(BaseModel):
    """A candidate a routed call moved past, and why."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="The candidate, as ``tool:provider``.")
    error: str | None = Field(
        default=None,
        description=(
            "Why the call moved on: the budget's skip reason, or the failure's HTTP status or error class; "
            "None when the candidate answered empty."
        ),
    )


class DecisionAttempt(BaseModel):
    """A candidate a routed call tried: what it was estimated to cost, and how long it took."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="The candidate, as ``tool:provider``.")
    est_cost_usd: float | None = Field(default=None, description="Its estimated cost; None when unknown.")
    seconds: float | None = Field(default=None, description="Wall-clock seconds the attempt took.")


MemberOutcome = Literal["ok", "router_timeout", "router_error", "router_invalid", "router_empty"]
"""What a composite router's member came to: ``ok`` when it decided something, else why it was passed over."""


class DecisionMember(BaseModel):
    """A router a composite router, such as ``fallback``, asked: how it answered, and how long it took."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="The member, as the composite names it: never its import path or a URL's path.")
    outcome: MemberOutcome = Field(
        description="ok when it decided something; else why the composite asked the next member."
    )
    ms: float = Field(description="Milliseconds from asking it to its answer, or to giving up on it.")
    counted: bool = Field(description="Whether its decision is the one the composite answered with.")


REASON_DESCRIPTION = (
    "The router's own reason. When the static order ran instead of the router's decision, why: "
    "router_timeout, router_error, router_invalid, or router_empty for a decision that decided nothing."
)


class DecisionRecord(BaseModel):
    """How a routed or budgeted call chose its backend. Experimental: it may change before 1.0.

    Every field is optional, and ``None`` means not recorded. Candidates are named
    ``tool:provider``, which shows the backends configured; no key values, and no
    prompt text, only ``prompt_sha256``, an unsalted hash of it that confirms a guessed
    prompt or URL (see the module docstring).
    """

    model_config = ConfigDict(extra="forbid")

    decision_id: str | None = Field(default=None, description="A unique id for this decision.")
    router: str | None = Field(default=None, description="The router that ranked the candidates.")
    offered: list[str] | None = Field(default=None, description="Every candidate the router saw, in that order.")
    ranked: list[str] | None = Field(default=None, description="The router's own ranking.")
    merged: list[str] | None = Field(default=None, description="The order tried, after the merge rule.")
    ran: int | None = Field(default=None, description="The position in `merged` that answered, from 0.")
    hops: list[DecisionHop] | None = Field(default=None, description="The candidates the call moved past.")
    attempts: list[DecisionAttempt] | None = Field(default=None, description="Every candidate tried, in order.")
    reason: str | None = Field(default=None, description=REASON_DESCRIPTION)
    members: list[DecisionMember] | None = Field(
        default=None, description="The routers a composite router asked, in order; None for any other router."
    )
    router_timeout: bool | None = Field(default=None, description="Whether the router ran out of time.")
    router_ms: float | None = Field(default=None, description="Milliseconds the router took.")
    dropped: dict[str, str] | None = Field(default=None, description="Candidates left out before routing -> why.")
    excluded: dict[str, str] | None = Field(default=None, description="Candidates the router excluded -> why.")
    ignored: dict[str, str] | None = Field(
        default=None, description="What of the router's decision the merge rule set aside -> why."
    )
    params: dict[str, Any] | None = Field(default=None, description="The options the call ran with, prompt left out.")
    params_source: Literal["budget", "router", "caller"] | None = Field(default=None, description="Who set `params`.")
    params_reason: str | None = Field(default=None, description="Why the budget's mapping chose `params`, in words.")
    estimated_cost_usd: float | None = Field(default=None, description="The call's estimated spend in US dollars.")
    cost_is_estimate: bool | None = Field(default=None, description="Whether that spend is a list-price estimate.")
    unmeasured: list[str] | None = Field(
        default=None,
        description=(
            "Candidates whose estimate is not measured: a candidate offered with no known price or p90 latency, "
            "that is keyless, whose request carries `provider_options`, or that route-eval did not rank in the "
            "method's default order (every extract, research and browse candidate); and an attempt a budget "
            "charged with no known price or p90."
        ),
    )
    prompt_sha256: str | None = Field(
        default=None,
        description=(
            "Unsalted SHA-256 of what the router reads: the query or task, or for extract the whole URLs, "
            "one per line; for a plan, its whole prompt. A pseudonymous id, not anonymisation: "
            "a guessed prompt or URL can be confirmed from it, and equal prompts share a hash."
        ),
    )


DECISION_DESCRIPTION = "How a routed or budgeted call chose its backend; None on any other call."
