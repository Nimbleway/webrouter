"""What the operator set for the server's routing, read once at startup, and the ``web_run`` tool.

Experimental. Three variables, read from the process environment when the
server starts and never again -- no ``.env`` file and no ``load_env`` call can
set or change them, since :mod:`web_router.mcp.env` loads credentials only:

- ``WEB_ROUTER_MCP_ROUTER``: the router ``web_run`` plans with, as
  ``<module>:<attribute>`` naming a router in a module the operator installed;
  ``off`` for none. Unset, the built-in :func:`~web_router.routing.rules.rules`.
- ``WEB_ROUTER_MCP_MAX_COST_USD`` and ``WEB_ROUTER_MCP_TIME_LIMIT_S``: the
  ceiling on one connection, held by :mod:`web_router.mcp.budget`.

The router is attached for ``plan`` and ``run`` only (``route_tools=()``), so
``search``, ``extract`` and the other tools run exactly as they would with no
router. A value the server cannot read stops it at startup, rather than
leaving a connection without the ceiling the operator meant to set.
"""

from __future__ import annotations

import functools
import importlib
import math
import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from web_router.client import AsyncWebRouter
from web_router.constants_mcp import (
    ENV_MCP_MAX_COST_USD,
    ENV_MCP_ROUTER,
    ENV_MCP_TIME_LIMIT_S,
    MCP_MSG_CEILING_VALUE,
    MCP_MSG_ROUTER_IMPORT,
    MCP_MSG_ROUTER_SPEC,
    MCP_PROMPTS_IN_PROCESS,
    MCP_ROUTER_OFF,
    MCP_TOOL_BUDGET_START,
    MCP_TOOL_BUDGET_STATUS,
    MCP_TOOL_WEB_RUN,
    MCP_URL_POLICY,
)
from web_router.constants_routing import RULES_ROUTER_NAME
from web_router.exceptions import ConfigurationError
from web_router.mcp.budget import budget_start, budget_status
from web_router.mcp.shaping import (
    max_response_chars,
    redact,
    shape_answer,
    shape_browse,
    shape_extract,
    shape_research,
    shape_search,
)
from web_router.routing.contract import Budget, Plan
from web_router.routing.invoke import RouterLike
from web_router.routing.rules import rules
from web_router.types import BrowseResponse, ExtractResponse, ResearchResponse, SearchResponse
from web_router.types_answer import AnswerResponse

if TYPE_CHECKING:
    from web_router.mcp.tools import WebRouterTools

_SHAPERS: dict[type, tuple[str, Callable[..., dict[str, Any]]]] = {
    SearchResponse: ("search", shape_search),
    ExtractResponse: ("extract", shape_extract),
    AnswerResponse: ("answer", shape_answer),
    ResearchResponse: ("research", shape_research),
    BrowseResponse: ("browse", shape_browse),
}
"""Each response ``run`` can return: the method it is from, and how that method's own tool shapes it."""


@dataclass(frozen=True)
class McpRouting:
    """The server's routing as the operator set it: the router ``web_run`` plans with, and the ceiling."""

    router: RouterLike | None = rules
    router_name: str = RULES_ROUTER_NAME
    ceiling: Budget = field(default_factory=Budget)

    @classmethod
    def from_environ(cls, environ: Mapping[str, str] | None = None) -> McpRouting:
        """What the process environment sets: read once, at startup.

        Raises:
            ConfigurationError: for a router that does not import, or a ceiling that is not a positive number.
        """
        env = os.environ if environ is None else environ
        ceiling = Budget(
            max_cost_usd=_positive(env, ENV_MCP_MAX_COST_USD), time_limit_s=_positive(env, ENV_MCP_TIME_LIMIT_S)
        )
        spec = env.get(ENV_MCP_ROUTER, "").strip()
        if not spec:
            return cls(ceiling=ceiling)
        if spec == MCP_ROUTER_OFF:
            return cls(router=None, router_name=MCP_ROUTER_OFF, ceiling=ceiling)
        return cls(router=_imported(spec), router_name=spec, ceiling=ceiling)

    def build(self, *args: Any, **kwargs: Any) -> AsyncWebRouter:
        """An :class:`~web_router.AsyncWebRouter` with this router attached for ``plan`` and ``run`` alone."""
        return AsyncWebRouter(*args, router=self.router, route_tools=(), url_policy=MCP_URL_POLICY, **kwargs)

    def capabilities(self, budget: dict[str, Any] | None) -> dict[str, Any]:
        """The capabilities resource's ``routing`` section, with ``budget``, where the connection's budget stands."""
        return {
            "router": self.router_name,
            "prompts": MCP_PROMPTS_IN_PROCESS,
            "url_policy": MCP_URL_POLICY,
            "ceiling": {"max_cost_usd": self.ceiling.max_cost_usd, "time_limit_s": self.ceiling.time_limit_s},
            "budget": budget,
        }


def _positive(env: Mapping[str, str], name: str) -> float | None:
    raw = env.get(name, "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        value = math.nan
    if not (math.isfinite(value) and value > 0):
        raise ConfigurationError(MCP_MSG_CEILING_VALUE.format(env_var=name, value=raw))
    return value


def _imported(spec: str) -> RouterLike:
    """The object ``<module>:<attribute>`` names; the attribute may be dotted."""
    module_name, _, attribute = spec.partition(":")
    if not module_name or not attribute:
        raise ConfigurationError(MCP_MSG_ROUTER_SPEC.format(env_var=ENV_MCP_ROUTER, value=spec))
    try:
        module = importlib.import_module(module_name)
        found: RouterLike = functools.reduce(getattr, attribute.split("."), module)
    except (ImportError, AttributeError) as exc:
        raise ConfigurationError(MCP_MSG_ROUTER_IMPORT.format(env_var=ENV_MCP_ROUTER, value=spec, error=exc)) from exc
    return found


def _plan_payload(plan: Plan) -> dict[str, Any]:
    return {
        "tool": plan.tool,
        "params": dict(plan.params),
        "providers": {tool: list(order) for tool, order in plan.providers.items()},
        "decision": plan.decision.model_dump(exclude_none=True),
    }


async def web_run(tools: WebRouterTools, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Plan ``prompt`` with the attached router and run it, or with ``dry_run`` only plan it."""
    kwargs = tools.arguments(MCP_TOOL_WEB_RUN, arguments)
    caps = {name: kwargs[name] for name in ("max_cost_usd", "time_limit_s") if name in kwargs}
    budget = Budget(**caps) if caps else None
    prompt = str(kwargs["prompt"])
    if kwargs.get("dry_run"):
        return {"dry_run": True, **_plan_payload(await tools.router.plan(prompt, budget=budget))}
    response = await tools.router.run(prompt, budget=budget)
    tool, shape = _SHAPERS[type(response)]
    # A browse payload can carry the session the agent ran in, so fetch_raw hands back its redacted copy.
    raw = redact(response.raw)[0] if tool == "browse" else response.raw
    handle = tools.store.add(MCP_TOOL_WEB_RUN, raw=raw).handle
    decision = response.decision.model_dump(exclude_none=True) if response.decision else None
    return {"tool": tool, **shape(response, handle, budget=max_response_chars()), "decision": decision}


ROUTING_TOOLS: dict[str, Callable[[WebRouterTools, Mapping[str, Any]], Awaitable[dict[str, Any]]]] = {
    MCP_TOOL_WEB_RUN: web_run,
    MCP_TOOL_BUDGET_START: budget_start,
    MCP_TOOL_BUDGET_STATUS: budget_status,
}
"""The three routing tools, each a handler taking the session's tools, as :class:`WebRouterTools` registers them."""
