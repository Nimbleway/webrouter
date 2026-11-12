"""Routing: the contract between web-router and a router that picks a call's method and backends.

Experimental: every name here may change before 1.0, except :class:`Budget`, which is stable.

A router is the caller's code. web-router shows it a :class:`RouteRequest` --
the prompt, the (tool, provider) candidates that could serve it with their
estimated cost and latency, and the call's :class:`Budget` -- and reads back a
:class:`RouteDecision`. Two rules keep routers working across versions: a
router ignores request fields it does not know, since later versions add them,
and every decision field is optional, so what a router leaves out is decided as
it would be with no router at all.

The contract is frozen, keyword-only dataclasses rather than pydantic models:
a router runs on every routed call, and web-router builds these values itself,
so validation would cost on each call and catch nothing, while keyword-only
construction lets a field be added without breaking a router that builds one.
:class:`~web_router.DecisionRecord` is the exception: a pydantic model, because
it rides on the pydantic responses.

Each part of routing has its own module: :mod:`.contract` the types,
:mod:`.candidates` the candidates a call offers, :mod:`.invoke` attaching and
calling a router, :mod:`.merge` turning its decision into an order,
:mod:`.budget` holding a call to its budget, :mod:`.params` the options a
budget sets, :mod:`.effort_table` the research effort table they read,
:mod:`.pricing` what an attempt costs and takes, :mod:`.decide` the merged
decision a call runs with, :mod:`.url_policy` the URLs a call may send,
:mod:`.attempts` the seam in ``auto``'s fallback loops, :mod:`.registry`
routers by name, :mod:`.wire` the JSON wire format, :mod:`.remote` the
built-in router that speaks it over HTTP, :mod:`.decider_router` a router
made of a decider, which the built-in ``llm`` and ``systemone`` routers are,
and :mod:`.fallback` the built-in router that asks others in turn, answering a
:class:`CompositeDecision`.
"""

from web_router.routing.composite import CompositeDecision
from web_router.routing.contract import Budget, Candidate, Plan, RouteDecision, RouteRequest, ToolName
from web_router.routing.decider import AsyncDecider, Decider, DeciderQuestion, DeciderReply, SyncDecider
from web_router.routing.decider_router import DeciderRouter
from web_router.routing.invoke import RouterLike
from web_router.routing.url_policy import UrlPolicy

__all__ = [
    "AsyncDecider",
    "Budget",
    "Candidate",
    "CompositeDecision",
    "Decider",
    "DeciderQuestion",
    "DeciderReply",
    "DeciderRouter",
    "Plan",
    "RouteDecision",
    "RouteRequest",
    "RouterLike",
    "SyncDecider",
    "ToolName",
    "UrlPolicy",
]
