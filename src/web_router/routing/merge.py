"""The merge rule: a router's decision and the static order, made into one method, one order and checked options.

Experimental. :func:`merge` has no clock and no network: it reads the price
list and the effort table, and looks backend classes up in the registry to see
which options each takes, which may import a backend's module on first use.
Its answer depends on its arguments alone, so route-eval replays a decision
exactly as a routed call would run it. The rules:

1. **Method.** The pinned one, when the caller called a method; else the
   first of ``decision.tools`` that still has a candidate; else ``search``.
   It is chosen once: a fallback never moves to another method.
2. **Order.** The backends the router ranked for that method, in its order,
   then every other candidate in the static order.
3. **Exclusions.** Only ``decision.exclude`` removes a candidate, each with
   its reason; one with an empty reason is set aside and noted. Exclusions
   that would leave the method no backend are ignored.
4. **Unknown ids** -- a method, a backend or an exclusion that is not a
   candidate -- are set aside and noted, never an error.
5. **Options.** ``decision.params`` for the method, by
   :func:`~web_router.routing.merge_params.checked_params`: only the keys on
   :data:`~web_router.constants_merge.ROUTER_PARAMS`, valid values, none the
   caller set, and nothing that does not fit the budget. What is dropped is
   noted, and the caller's options and the budget's mapping apply instead.
6. **No decision.** A router that timed out, raised, or answered with
   anything but a well-formed :class:`~web_router.routing.RouteDecision` gets
   the static order, with the reason. So does a decision that decides
   nothing, recorded as ``router_empty`` and not as a failure.
7. **Extract with no URL** searches instead, and says so.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from typing import Any, cast

from web_router.constants_candidates import ONE_PAGE_URL
from web_router.constants_merge import (
    EXTRACT_TOOL,
    LAST_RESORT_TOOL,
    MSG_EXCLUDE_NO_REASON,
    MSG_EXCLUSIONS_EMPTY,
    MSG_EXTRACT_NO_URL,
    MSG_TOOL_NO_CANDIDATE,
    MSG_UNKNOWN_ID,
    MSG_UNKNOWN_TOOL,
    NOTE_EXCLUDE,
    NOTE_EXTRACT,
    NOTE_EXTRACT_URLS,
    ROUTER_INVALID,
)
from web_router.constants_params import PRICED_QUERY
from web_router.routing.candidates import REQUEST_OF, base_params
from web_router.routing.contract import RouteDecision, RouteRequest, ToolName, candidate_id
from web_router.routing.merge_params import checked_params, router_url_refusal
from web_router.routing.merge_shape import TOOLS, decides_nothing, well_formed
from web_router.routing.merge_types import FallbackReason, Merged, MergeNotes
from web_router.routing.merge_urls import UrlCheck, hide_urls
from web_router.types import NormalizedRequest

__all__ = ["FallbackReason", "MergeNotes", "Merged", "UrlCheck", "merge"]


def _order(
    tool: str, request: RouteRequest, ranked: Sequence[str], excluded: Collection[str], static: Mapping[Any, Any]
) -> tuple[str, ...]:
    """``ranked`` first, then the static order, then any candidate the static order leaves out; none excluded."""
    names = [c.provider for c in request.candidates if c.tool == tool and c.id not in excluded]
    pool = set(names)
    return tuple(dict.fromkeys(p for p in (*ranked, *static.get(tool, ()), *names) if p in pool))


def _static(request: RouteRequest, static: Mapping[Any, Any], why: FallbackReason | None, reason: str | None) -> Merged:
    """The static order: for a failed router (``why``), or for an empty decision (``why`` of ``None``)."""
    tool = request.pinned_tool or LAST_RESORT_TOOL
    providers = _order(tool, request, (), (), static)
    merged = tuple(candidate_id(tool, p) for p in providers)
    notes = MergeNotes(merged=merged, fallback=why, empty=why is None, reason=hide_urls(reason))
    return Merged(tool=tool, providers=providers, params={}, notes=notes)


def _exclusions(decision: RouteDecision, known: Collection[str], ignored: dict[str, str]) -> dict[str, str]:
    excluded = {}
    for cid, why in decision.exclude.items():
        if cid not in known:
            ignored[cid] = MSG_UNKNOWN_ID.format(id=cid)
        elif not why.strip():
            ignored[cid] = MSG_EXCLUDE_NO_REASON.format(id=cid)
        else:
            excluded[cid] = hide_urls(why)
    return excluded


def _tool(request: RouteRequest, decision: RouteDecision, excluded: Collection[str], ignored: dict[str, str]) -> str:
    """Rule 1: the pinned method; else the router's first with a candidate left; else ``search``."""
    if request.pinned_tool is not None:
        return request.pinned_tool
    left = {c.tool for c in request.candidates if c.id not in excluded}
    for tool in decision.tools or ():
        if tool not in TOOLS:
            ignored[tool] = MSG_UNKNOWN_TOOL.format(tool=tool)
        elif tool in left:
            return tool
        else:
            ignored[tool] = MSG_TOOL_NO_CANDIDATE.format(tool=tool)
    return LAST_RESORT_TOOL


def _ranked(tool: str, decision: RouteDecision, known: Collection[str], ignored: dict[str, str]) -> tuple[str, ...]:
    """The router's backends for ``tool``, best first; an unknown one noted and left out."""
    asked: Mapping[Any, Sequence[str]] = decision.providers or {}
    ranked = []
    for provider in dict.fromkeys(asked.get(tool, ())):
        if candidate_id(tool, provider) in known:
            ranked.append(provider)
        else:
            ignored[candidate_id(tool, provider)] = MSG_UNKNOWN_ID.format(id=candidate_id(tool, provider))
    return tuple(ranked)


def _method(
    tool: str,
    request: RouteRequest,
    decision: RouteDecision,
    caller_set: Collection[str],
    url_check: UrlCheck | None,
    ignored: dict[str, str],
) -> str:
    """Rule 7: ``tool``, unless it is extract with no URL to read, which searches instead and says so."""
    refused = router_url_refusal(decision, url_check, caller_set)
    gave_urls = (decision.params or {}).get(EXTRACT_TOOL, {}).get("urls") is not None
    if tool != EXTRACT_TOOL or request.pinned_tool is not None or request.urls or (gave_urls and not refused):
        return tool
    ignored[NOTE_EXTRACT] = MSG_EXTRACT_NO_URL
    if refused is not None:
        ignored[NOTE_EXTRACT_URLS] = refused
    return LAST_RESORT_TOOL


def _never_empty(
    tool: str,
    request: RouteRequest,
    ranked: Sequence[str],
    excluded: dict[str, str],
    static: Mapping[Any, Any],
    ignored: dict[str, str],
) -> tuple[tuple[str, ...], dict[str, str]]:
    """Rule 3: the order and the exclusions kept, with the method's exclusions ignored if they would empty it."""
    providers = _order(tool, request, ranked, excluded, static)
    mine = [cid for cid in excluded if cid.startswith(candidate_id(tool, ""))]
    if providers or not mine:
        return providers, excluded
    ignored[NOTE_EXCLUDE] = MSG_EXCLUSIONS_EMPTY.format(ids=", ".join(mine), tool=tool)
    kept = {cid: why for cid, why in excluded.items() if cid not in mine}
    return _order(tool, request, ranked, (), static), kept


def _prompt_request(tool: ToolName, request: RouteRequest) -> NormalizedRequest:
    """The request ``tool`` runs ``request``'s prompt with, extract on one page when the prompt names no URL."""
    prompt = request.prompt or PRICED_QUERY  # priced by backend and options, never by the prompt
    return REQUEST_OF[tool](**base_params(tool, prompt, request.urls or (ONE_PAGE_URL,)))


def merge(
    request: RouteRequest,
    decision: object,
    static_order: Mapping[ToolName, Sequence[str]],
    *,
    failure: FallbackReason | None = None,
    caller_set: Collection[str] = (),
    url_check: UrlCheck | None = None,
    requests: Mapping[ToolName, NormalizedRequest] | None = None,
) -> Merged:
    """``decision`` for ``request``, merged with ``static_order`` by the rules above.

    ``static_order`` is each method's backends as ``auto`` would try them.
    ``failure`` says the router timed out or raised; ``decision`` is then not
    read. ``caller_set`` names the options the caller set, which a router never
    changes. ``url_check`` is the URL policy: it returns why a URL is refused,
    ``None`` when it is allowed. With none, a router sets no URL. ``requests``
    are what each method runs with no router option -- the ones the candidates
    were priced at -- and the router's options are priced on them; with none,
    the prompt's own requests.
    """
    if failure is None and not well_formed(decision):
        failure = ROUTER_INVALID
    if failure is not None:
        return _static(request, static_order, failure, None)
    decision = cast(RouteDecision, decision)
    if decides_nothing(decision):
        return _static(request, static_order, None, decision.reason)
    known = {c.id for c in request.candidates}
    ignored: dict[str, str] = {}
    excluded = _exclusions(decision, known, ignored)
    tool = _tool(request, decision, excluded, ignored)
    tool = _method(tool, request, decision, caller_set, url_check, ignored)
    ranked = _ranked(tool, decision, known, ignored)
    providers, excluded = _never_empty(tool, request, ranked, excluded, static_order, ignored)
    base = (requests or {}).get(cast(ToolName, tool)) or _prompt_request(cast(ToolName, tool), request)
    params = checked_params(tool, decision, providers, base, request.budget, caller_set, url_check, ignored)
    notes = MergeNotes(
        ranked=tuple(candidate_id(tool, p) for p in ranked),
        merged=tuple(candidate_id(tool, p) for p in providers),
        excluded=excluded,
        ignored={hide_urls(key): hide_urls(why) for key, why in ignored.items()},
        reason=hide_urls(decision.reason),
    )
    return Merged(tool=cast(ToolName, tool), providers=providers, params=params, notes=notes)
