"""The merge rule's check on a router's options: an allowlist, valid values, the caller's own, and the budget.

Experimental. Apart from :mod:`.merge` so each stays small. A router may set
only the keys :data:`~web_router.constants_merge.ROUTER_PARAMS` lists for the
method. A key the caller set stands. Every option must fit the budget on every
backend in the order, since any of them may run it: the request is priced with
the option applied, by the attempt rule
(:func:`~web_router.routing.pricing.fit_reason`). Every backend in the order
must take the option -- the request always sends it, or the backend's
``supported_parameters`` declares it -- since a backend that does not would
refuse the call. A URL needs a URL
policy that allows it, and is never written into a note or a record whole:
:func:`web_router._urls.host_of` keeps its scheme and host.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
from typing import Any, cast, get_args

from web_router import registry
from web_router._urls import host_of
from web_router.constants_merge import (
    MSG_PARAM_CALLER,
    MSG_PARAM_INVALID,
    MSG_PARAM_NO_URL_POLICY,
    MSG_PARAM_NOT_ALLOWED,
    MSG_PARAM_PRICED,
    MSG_PARAM_UNCHECKED,
    MSG_PARAM_UNDECLARED,
    MSG_PARAM_URL_REFUSED,
    ROUTER_PARAMS,
)
from web_router.exceptions import ProviderNotFoundError, ProviderNotInstalledError
from web_router.routing.attempts import Attempt
from web_router.routing.candidates import REQUEST_OF
from web_router.routing.contract import Budget, RouteDecision, ToolName, candidate_id
from web_router.routing.merge_urls import URL_KEYS, UrlCheck
from web_router.routing.params import EFFORTS
from web_router.routing.pricing import fit_reason
from web_router.types import (
    NormalizedRequest,
    SearchDepth,
)

DEPTHS: tuple[str, ...] = get_args(SearchDepth)

_CLASS_OF: dict[ToolName, Callable[[str], type[Any]]] = {
    "search": registry.get_provider_class,
    "extract": registry.get_extract_provider_class,
    "research": registry.get_research_provider_class,
    "browse": registry.get_browse_provider_class,
}
DECLARED: dict[str, tuple[type[NormalizedRequest], Callable[[str], type[Any]]]] = {
    tool: (REQUEST_OF[tool], class_of) for tool, class_of in _CLASS_OF.items()
}
"""Per method, its request (whose ``always_sent`` every backend takes) and its registry's class lookup."""


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


def _shown_value(key: str, value: Any) -> Any:
    """``value`` as a note names it: a URL as its scheme and host, a list of URLs by how many."""
    if key == "urls":
        return f"{len(value)} URLs"
    return host_of(value) if key == "start_url" else value


def _misfit(
    tool: str, key: str, value: Any, request: NormalizedRequest, providers: Sequence[str], budget: Budget
) -> str | None:
    """Why ``request`` with ``key`` set to ``value`` would not fit ``budget`` on one of ``providers``.

    Priced as the call would run it, by :func:`~web_router.routing.pricing.fit_reason`: an ``effort``, a
    ``search_depth`` and a ``max_results`` (past a row's ``results_assumed``) change the price, and ``urls``
    are charged per page, so each is held to the budget on every backend that may run it.
    """
    priced = request.model_copy(update={key: list(value) if key == "urls" else value})
    for provider in providers:
        what = MSG_PARAM_PRICED.format(id=candidate_id(tool, provider), key=key, value=_shown_value(key, value))
        why = fit_reason(Attempt(tool=cast(ToolName, tool), provider=provider, request=priced), budget, what)
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


def router_url_refusal(decision: RouteDecision, url_check: UrlCheck | None, caller_set: Collection[str]) -> str | None:
    """Why the router's extract URLs fail a check; ``None`` when they pass, or when it gave none."""
    urls = (decision.params or {}).get("extract", {}).get("urls")
    return None if urls is None else _refusal("extract", "urls", urls, caller_set, url_check)


def checked_params(
    tool: str,
    decision: RouteDecision,
    providers: Sequence[str],
    request: NormalizedRequest,
    budget: Budget | None,
    caller_set: Collection[str],
    url_check: UrlCheck | None,
    ignored: dict[str, str],
) -> dict[str, Any]:
    """The router's options for ``tool`` that pass every check; each one dropped is noted in ``ignored``.

    ``request`` is the one the call runs with no option of the router's: each option is priced on it, with the
    options kept before it applied. The merge redacts every note once, at its end.
    """
    asked: Mapping[Any, Mapping[str, Any]] = decision.params or {}
    kept: dict[str, Any] = {}
    for key, value in asked.get(tool, {}).items():
        why = _refusal(tool, key, value, caller_set, url_check) or _undeclared(tool, key, providers)
        if why is None and budget is not None:
            why = _misfit(tool, key, value, request.model_copy(update=kept), providers, budget)
        if why is None:
            kept[key] = list(value) if key == "urls" else value
        else:
            ignored[f"params.{tool}.{key}"] = why
    return kept
