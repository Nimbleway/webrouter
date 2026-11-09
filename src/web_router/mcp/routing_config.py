"""What the operator set for the server's routing, read once at startup.

Experimental. These variables, read from the process environment when the
server starts and never again -- no ``.env`` file and no ``load_env`` call can
set or change them, since :mod:`web_router.mcp.env` loads credentials only:

- ``WEB_ROUTER_MCP_ROUTER``: the router ``web_run`` plans with, as
  ``<module>:<attribute>`` naming a router in a module the operator installed;
  ``remote`` for the built-in remote router, at ``WEB_ROUTER_REMOTE_ROUTER_URL``
  with the bearer key ``WEB_ROUTER_REMOTE_ROUTER_API_KEY``, both read from the
  same environment; ``off`` for none. Unset, the built-in
  :func:`~web_router.routing.rules.rules`.
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
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from web_router._urls import host_of
from web_router.client import AsyncWebRouter
from web_router.constants_mcp import (
    ENV_MCP_MAX_COST_USD,
    ENV_MCP_ROUTER,
    ENV_MCP_TIME_LIMIT_S,
    MCP_MSG_CEILING_VALUE,
    MCP_MSG_REMOTE_URL,
    MCP_MSG_ROUTER_IMPORT,
    MCP_MSG_ROUTER_SPEC,
    MCP_PROMPT_NOTE,
    MCP_ROUTER_IN_PROCESS,
    MCP_ROUTER_OFF,
    MCP_ROUTER_OPERATOR,
    MCP_ROUTER_OPERATOR_NOTE,
    MCP_ROUTER_REMOTE,
    MCP_ROUTER_REMOTE_NOTE,
    MCP_URL_POLICY,
    MCP_URL_POLICY_NOTE,
)
from web_router.constants_remote import (
    ENV_REMOTE_ROUTER_API_KEY,
    ENV_REMOTE_ROUTER_URL,
    REMOTE_DISPLAY_NAME,
    REMOTE_ROUTER_NAME,
)
from web_router.constants_routing import RULES_ROUTER_NAME
from web_router.exceptions import ConfigurationError
from web_router.routing.contract import Budget
from web_router.routing.invoke import RouterLike
from web_router.routing.rules import rules


@dataclass(frozen=True)
class McpRouting:
    """The server's routing as the operator set it: the router ``web_run`` plans with, and the ceiling."""

    router: RouterLike | str | None = rules
    router_name: str = RULES_ROUTER_NAME
    """What the capabilities resource names it: ``rules``, ``off``, ``operator`` -- never the import path -- or
    ``remote(<scheme>://<host>)``."""
    ceiling: Budget = field(default_factory=Budget)
    router_config: Mapping[str, Any] | None = field(default=None, repr=False)
    """The ``remote`` router's URL and key; left out of the ``repr``, so the key is never printed."""

    @classmethod
    def from_environ(cls, environ: Mapping[str, str] | None = None) -> McpRouting:
        """What the process environment sets: read once, at startup.

        Raises:
            ConfigurationError: for a router set empty or that does not import, or a ceiling that is not a positive
                number.
        """
        env = os.environ if environ is None else environ
        ceiling = Budget(
            max_cost_usd=_positive(env, ENV_MCP_MAX_COST_USD), time_limit_s=_positive(env, ENV_MCP_TIME_LIMIT_S)
        )
        raw = env.get(ENV_MCP_ROUTER)
        if raw is None:
            return cls(ceiling=ceiling)
        spec = raw.strip()
        if spec == MCP_ROUTER_OFF:
            return cls(router=None, router_name=MCP_ROUTER_OFF, ceiling=ceiling)
        if spec == REMOTE_ROUTER_NAME:
            return cls._remote(env, ceiling)
        return cls(router=_imported(spec), router_name=MCP_ROUTER_OPERATOR, ceiling=ceiling)

    @classmethod
    def _remote(cls, env: Mapping[str, str], ceiling: Budget) -> McpRouting:
        """The ``remote`` router at the URL the environment sets, with its key; the URL is checked when it is built."""
        url = (env.get(ENV_REMOTE_ROUTER_URL) or "").strip()
        if not url:
            raise ConfigurationError(MCP_MSG_REMOTE_URL.format(env_var=ENV_MCP_ROUTER, url_var=ENV_REMOTE_ROUTER_URL))
        config: dict[str, Any] = {"url": url}
        if key := env.get(ENV_REMOTE_ROUTER_API_KEY):
            config["api_key"] = key
        name = REMOTE_DISPLAY_NAME.format(host=host_of(url))
        return cls(router=REMOTE_ROUTER_NAME, router_name=name, ceiling=ceiling, router_config=config)

    def build(self, *args: Any, **kwargs: Any) -> AsyncWebRouter:
        """An :class:`~web_router.AsyncWebRouter` with this router attached for ``plan`` and ``run`` alone."""
        return AsyncWebRouter(
            *args,
            router=self.router,
            router_config=self.router_config,
            route_tools=(),
            url_policy=MCP_URL_POLICY,
            **kwargs,
        )

    def capabilities(self, budget: dict[str, Any] | None) -> dict[str, Any]:
        """The capabilities resource's ``routing`` section, with ``budget``, where the connection's budget stands."""
        runs = self._runs()
        return {
            "router": self.router_name,
            **runs,
            "prompt_note": MCP_PROMPT_NOTE,
            "url_policy": MCP_URL_POLICY,
            "url_policy_note": MCP_URL_POLICY_NOTE,
            "ceiling": {"max_cost_usd": self.ceiling.max_cost_usd, "time_limit_s": self.ceiling.time_limit_s},
            "budget": budget,
        }

    def _runs(self) -> dict[str, str]:
        """Where the router runs, and for one that is not the library's own, a note on what it may send."""
        if self.router_name in (RULES_ROUTER_NAME, MCP_ROUTER_OFF):
            return {"router_runs": MCP_ROUTER_IN_PROCESS}
        if self.router == REMOTE_ROUTER_NAME:
            host = host_of(str((self.router_config or {}).get("url", "")))
            return {"router_runs": MCP_ROUTER_REMOTE, "router_note": MCP_ROUTER_REMOTE_NOTE.format(host=host)}
        return {"router_runs": MCP_ROUTER_OPERATOR, "router_note": MCP_ROUTER_OPERATOR_NOTE}


def _positive(env: Mapping[str, str], name: str) -> float | None:
    raw = env.get(name)
    if raw is None:
        return None
    try:
        value = float(raw.strip() or "nan")
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
