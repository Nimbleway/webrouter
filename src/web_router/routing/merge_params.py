"""The merge rule's check on a router's options: an allowlist, valid values, the caller's own, and the budget.

Experimental. Apart from :mod:`.merge` so each stays small. A router may set
only the keys :data:`~web_router.constants_merge.ROUTER_PARAMS` lists for the
method. A key the caller set stands. A research ``effort`` must fit the budget
on every backend in the order, by :func:`~web_router.routing.params.effort_misfit`,
since any of them may run it; a ``search_depth`` must have a known price within
the cost cap on every one. A URL needs a URL policy that allows it.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
from typing import Any, get_args

from web_router import prices
from web_router.constants_merge import (
    MSG_PARAM_CALLER,
    MSG_PARAM_DEPTH_COST,
    MSG_PARAM_DEPTH_UNKNOWN_PRICE,
    MSG_PARAM_INVALID,
    MSG_PARAM_NO_URL_POLICY,
    MSG_PARAM_NOT_ALLOWED,
    MSG_PARAM_URL_REFUSED,
    MSG_UNKNOWN_TOOL,
    ROUTER_PARAMS,
)
from web_router.routing.contract import Budget, RouteDecision
from web_router.routing.params import EFFORTS, effort_misfit
from web_router.types import SearchDepth

UrlCheck = Callable[[str], str | None]
"""A URL policy: why a URL is refused, or ``None`` when it is allowed."""

DEPTHS: tuple[str, ...] = get_args(SearchDepth)
URL_KEYS = frozenset({"urls", "start_url"})


def _valid(key: str, value: Any) -> bool:
    """Whether ``value`` has the type and range the method takes for ``key``."""
    if key == "max_results":
        return isinstance(value, int) and not isinstance(value, bool) and value >= 1
    if key == "search_depth":
        return value in DEPTHS
    if key == "effort":
        return value in EFFORTS
    if key == "urls":
        return isinstance(value, list | tuple) and bool(value) and all(isinstance(u, str) and u for u in value)
    return key == "start_url" and isinstance(value, str) and bool(value)


def _url_refusal(key: str, value: Any, url_check: UrlCheck | None) -> str | None:
    if url_check is None:
        return MSG_PARAM_NO_URL_POLICY.format(key=key)
    for url in (value,) if isinstance(value, str) else value:
        why = url_check(url)
        if why is not None:
            return MSG_PARAM_URL_REFUSED.format(url=url, why=why)
    return None


def _depth_misfit(provider: str, depth: str, budget: Budget) -> str | None:
    if budget.max_cost_usd is None:
        return None
    cost = prices.estimate(provider, "search", depth)
    if cost is None:
        return MSG_PARAM_DEPTH_UNKNOWN_PRICE.format(provider=provider, depth=depth)
    if cost > budget.max_cost_usd:
        return MSG_PARAM_DEPTH_COST.format(provider=provider, depth=depth, cost=cost, cap=budget.max_cost_usd)
    return None


def _misfit(key: str, value: str, providers: Sequence[str], budget: Budget | None) -> str | None:
    """Why ``value`` for ``key`` would not fit ``budget`` on one of ``providers``; ``None`` when it fits on all."""
    if budget is None or key not in ("effort", "search_depth"):
        return None
    for provider in providers:
        why = effort_misfit(provider, value, budget) if key == "effort" else _depth_misfit(provider, value, budget)
        if why is not None:
            return why
    return None


def _refusal(tool: str, key: str, value: Any, caller_set: Collection[str], url_check: UrlCheck | None) -> str | None:
    """Why the router may not set ``key`` to ``value`` for ``tool``, the budget aside."""
    if key not in ROUTER_PARAMS[tool]:
        return MSG_PARAM_NOT_ALLOWED.format(tool=tool, key=key)
    if key in caller_set:
        return MSG_PARAM_CALLER.format(key=key)
    if not _valid(key, value):
        return MSG_PARAM_INVALID.format(value=value, key=key, tool=tool)
    return _url_refusal(key, value, url_check) if key in URL_KEYS else None


def router_urls(decision: RouteDecision, url_check: UrlCheck | None) -> bool:
    """Whether the router gave extract URLs that pass every check: rule 7 reads it before the method is final."""
    urls = (decision.params or {}).get("extract", {}).get("urls")
    return urls is not None and _refusal("extract", "urls", urls, (), url_check) is None


def checked_params(
    tool: str,
    decision: RouteDecision,
    providers: Sequence[str],
    budget: Budget | None,
    caller_set: Collection[str],
    url_check: UrlCheck | None,
    ignored: dict[str, str],
) -> dict[str, Any]:
    """The router's options for ``tool`` that pass every check; each one dropped is noted in ``ignored``."""
    asked: Mapping[Any, Mapping[str, Any]] = decision.params or {}
    for other in asked:
        if other not in ROUTER_PARAMS:
            ignored[f"params.{other}"] = MSG_UNKNOWN_TOOL.format(tool=other)
    kept: dict[str, Any] = {}
    for key, value in asked.get(tool, {}).items():
        why = _refusal(tool, key, value, caller_set, url_check) or _misfit(key, value, providers, budget)
        if why is None:
            kept[key] = list(value) if key == "urls" else value
        else:
            ignored[f"params.{tool}.{key}"] = why
    return kept
