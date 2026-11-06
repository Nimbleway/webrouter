"""The merge rule's check on a router's options: an allowlist, valid values, the caller's own, and the budget.

Experimental. Apart from :mod:`.merge` so each stays small. A router may set
only the keys :data:`~web_router.constants_merge.ROUTER_PARAMS` lists for the
method. A key the caller set stands. A research ``effort`` must fit the budget
on every backend in the order, by :func:`~web_router.routing.params.effort_misfit`,
since any of them may run it; a ``search_depth`` must have a known price within
the cost cap on every one. Every backend in the order must take the option --
the request always sends it, or the backend's ``supported_parameters`` declares
it -- since a backend that does not would refuse the call. A URL needs a URL
policy that allows it, and is never written into a note or a record whole:
:func:`shown` keeps its scheme and host.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Collection, Mapping, Sequence
from typing import Any, get_args, overload
from urllib.parse import urlsplit

from web_router import registry
from web_router.constants_merge import (
    MSG_PARAM_CALLER,
    MSG_PARAM_DEPTH_COST,
    MSG_PARAM_DEPTH_UNKNOWN_PRICE,
    MSG_PARAM_INVALID,
    MSG_PARAM_NO_URL_POLICY,
    MSG_PARAM_NOT_ALLOWED,
    MSG_PARAM_UNCHECKED,
    MSG_PARAM_UNDECLARED,
    MSG_PARAM_URL_REFUSED,
    ROUTER_PARAMS,
    URL_BRACKETS,
    URL_HIDDEN,
    URL_IN_TEXT,
)
from web_router.exceptions import ProviderNotFoundError, ProviderNotInstalledError
from web_router.routing.contract import Budget, RouteDecision
from web_router.routing.params import EFFORTS, effort_misfit
from web_router.routing.pricing import mode_cost, over_cost
from web_router.types import (
    BrowseRequest,
    ExtractRequest,
    NormalizedRequest,
    ResearchRequest,
    SearchDepth,
    SearchRequest,
)

UrlCheck = Callable[[str], str | None]
"""A URL policy: why a URL is refused, or ``None`` when it is allowed."""

DEPTHS: tuple[str, ...] = get_args(SearchDepth)
URL_KEYS = frozenset({"urls", "start_url"})

DECLARED: dict[str, tuple[type[NormalizedRequest], Callable[[str], type[Any]]]] = {
    "search": (SearchRequest, registry.get_provider_class),
    "extract": (ExtractRequest, registry.get_extract_provider_class),
    "research": (ResearchRequest, registry.get_research_provider_class),
    "browse": (BrowseRequest, registry.get_browse_provider_class),
}
"""Per method, its request (whose ``always_sent`` every backend takes) and its registry's class lookup."""


def host_of(url: str) -> str:
    """What a note or a record shows of ``url``: its scheme and host, never its path, query or credentials."""
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:
        return URL_HIDDEN
    return f"{parts.scheme}://{host}" if parts.scheme and host else URL_HIDDEN


def _url_end(match: str) -> int:
    """Where the URL in ``match`` ends: before the first closing bracket it did not open."""
    opened: list[str] = []
    for i, char in enumerate(match):
        if char in URL_BRACKETS.values():
            opened.append(char)
        elif char in URL_BRACKETS and (not opened or opened.pop() != URL_BRACKETS[char]):
            return i
    return len(match)


def _hide(match: re.Match[str]) -> str:
    text = match.group(0)
    end = _url_end(text)
    return host_of(text[:end]) + text[end:]


@overload
def hide_urls(text: str) -> str: ...
@overload
def hide_urls(text: None) -> None: ...
def hide_urls(text: str | None) -> str | None:
    """``text`` with every URL in it cut to its scheme and host: for anything a router wrote, ids included."""
    return None if text is None else re.sub(URL_IN_TEXT, _hide, text)


def shown(params: Mapping[str, Any]) -> dict[str, Any]:
    """``params`` as a record or a ``repr`` shows them: each URL cut to its scheme and host."""
    out = dict(params)
    if "urls" in out:
        out["urls"] = [host_of(u) for u in out["urls"]]
    if "start_url" in out:
        out["start_url"] = host_of(out["start_url"])
    return out


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
            return MSG_PARAM_URL_REFUSED.format(host=host_of(url), why=why)
    return None


def _depth_misfit(provider: str, depth: str, budget: Budget) -> str | None:
    if budget.max_cost_usd is None:
        return None
    cost = mode_cost(provider, "search", depth)
    if cost is None:
        return MSG_PARAM_DEPTH_UNKNOWN_PRICE.format(provider=provider, depth=depth)
    if over_cost(cost, budget.max_cost_usd):
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
        return MSG_PARAM_INVALID.format(key=key, tool=tool)
    return _url_refusal(key, value, url_check) if key in URL_KEYS else None


def _undeclared(tool: str, key: str, providers: Sequence[str]) -> str | None:
    """Why one of ``providers`` would refuse ``key``: it neither always sends it nor declares it."""
    request_class, class_of = DECLARED[tool]
    if key in request_class.always_sent:
        return None
    for provider in providers:
        try:
            declared = class_of(provider).supported_parameters
        except (ProviderNotFoundError, ProviderNotInstalledError) as exc:  # not registered, or its SDK is missing
            return MSG_PARAM_UNCHECKED.format(tool=tool, provider=provider, key=key, error=type(exc).__name__)
        if key not in declared:
            return MSG_PARAM_UNDECLARED.format(tool=tool, provider=provider, key=key)
    return None


def router_urls(decision: RouteDecision, url_check: UrlCheck | None, caller_set: Collection[str]) -> bool:
    """Whether the router gave extract URLs that pass every check: rule 7 reads it before the method is final."""
    urls = (decision.params or {}).get("extract", {}).get("urls")
    return urls is not None and _refusal("extract", "urls", urls, caller_set, url_check) is None


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
    kept: dict[str, Any] = {}
    for key, value in asked.get(tool, {}).items():
        why = (
            _refusal(tool, key, value, caller_set, url_check)
            or _undeclared(tool, key, providers)
            or _misfit(key, value, providers, budget)
        )
        if why is None:
            kept[key] = list(value) if key == "urls" else value
        else:
            ignored[hide_urls(f"params.{tool}.{key}")] = hide_urls(why)
    return kept
