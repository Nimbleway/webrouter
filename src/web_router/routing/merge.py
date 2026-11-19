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
   then every other candidate in the static order. A decision's ``attempts``,
   when set, replace ``tools`` and ``providers``: the method is the first
   attempt's, and its attempts, vendor modes included, rank it; a backend they
   name only in its modes is not appended at its default mode (:mod:`.merge_attempts`).
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
7. **Extract with no URL** searches instead, and says so -- also when every
   URL the router gave is dropped by a check -- unless the caller pinned it:
   :mod:`.merge_extract`.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from typing import Any, cast

from web_router.constants_merge import (
    LAST_RESORT_TOOL,
    MSG_EXCLUDE_NO_REASON,
    MSG_EXCLUSIONS_EMPTY,
    MSG_MODE_NOT_RUN,
    MSG_TOOL_NO_CANDIDATE,
    MSG_UNKNOWN_ID,
    MSG_UNKNOWN_TOOL,
    NOTE_EXCLUDE,
    ROUTER_INVALID,
)
from web_router.routing.composite import decides_nothing
from web_router.routing.contract import RouteDecision, RouteRequest, ToolName, candidate_id
from web_router.routing.merge_attempts import (
    backends,
    entries,
    excludable,
    first_tool,
    in_modes,
    kept_exclusions,
    only_in_modes,
    ranked_attempts,
    reasoned,
)
from web_router.routing.merge_extract import extract_or_search, lost_its_urls, prompt_request
from web_router.routing.merge_params import checked_params
from web_router.routing.merge_shape import TOOLS, well_formed
from web_router.routing.merge_types import FallbackReason, Merged, MergeNotes, settled
from web_router.routing.merge_urls import UrlCheck, hide_urls
from web_router.types import NormalizedRequest

__all__ = ["FallbackReason", "MergeNotes", "Merged", "UrlCheck", "merge"]


def _order(
    tool: str, request: RouteRequest, ranked: Sequence[str], excluded: Collection[str], static: Mapping[Any, Any]
) -> tuple[str, ...]:
    """``ranked`` first, then the static order, then any backend the static order leaves out; none excluded.

    A vendor mode runs only where ``ranked`` names it: the static order names backends alone, and leaves out a
    backend ``ranked`` names only in its modes.
    """
    pool, moded = entries(request, tool, excluded), in_modes(request.candidates, tool, ranked)
    rest = [p for p in (*static.get(tool, ()), *pool) if p in pool and pool[p].is_backend and p not in moded]
    return tuple(dict.fromkeys(p for p in (*ranked, *rest) if p in pool))


def _static(request: RouteRequest, static: Mapping[Any, Any], why: FallbackReason | None, reason: str | None) -> Merged:
    """The static order: for a failed router (``why``), or for an empty decision (``why`` of ``None``)."""
    tool = request.pinned_tool or LAST_RESORT_TOOL
    providers = _order(tool, request, (), (), static)
    merged = tuple(candidate_id(tool, p) for p in providers)
    notes = MergeNotes(merged=merged, fallback=why, empty=why is None, reason=hide_urls(reason))
    return Merged(tool=tool, providers=providers, params={}, notes=notes)


def _exclusions(request: RouteRequest, decision: RouteDecision, ignored: dict[str, str]) -> dict[str, str]:
    """The exclusions that count (:func:`.merge_attempts.reasoned`); each other one noted."""
    kept, known = reasoned(request.candidates, decision.exclude), excludable(request.candidates)
    for cid in (cid for cid in decision.exclude if cid not in kept):
        ignored[cid] = (MSG_EXCLUDE_NO_REASON if cid in known else MSG_UNKNOWN_ID).format(id=cid)
    return {cid: hide_urls(why) for cid, why in kept.items()}


def _tool(request: RouteRequest, decision: RouteDecision, excluded: Collection[str], ignored: dict[str, str]) -> str:
    """Rule 1: the pinned method; else the router's first with a candidate left; else ``search``."""
    if request.pinned_tool is not None:
        return request.pinned_tool
    if decision.attempts:
        return first_tool(request, decision, excluded) or LAST_RESORT_TOOL
    left = {c.tool for c in request.candidates if c.is_backend and c.id not in excluded}  # a backend to run
    for tool in decision.tools or ():
        if tool not in TOOLS:
            ignored[tool] = MSG_UNKNOWN_TOOL.format(tool=tool)
        elif tool in left:
            return tool
        else:
            ignored[tool] = MSG_TOOL_NO_CANDIDATE.format(tool=tool)
    return LAST_RESORT_TOOL


def _ranked(
    tool: str, request: RouteRequest, decision: RouteDecision, known: Mapping[str, bool], ignored: dict[str, str]
) -> tuple[str, ...]:
    """The router's backends for ``tool``, best first, or its attempts when set; an unknown id, or a vendor mode's
    id ranked as a backend, noted and left out."""
    if decision.attempts:
        return ranked_attempts(tool, request, decision, ignored)
    asked: Mapping[Any, Sequence[str]] = decision.providers or {}
    ranked = []
    for provider in dict.fromkeys(asked.get(tool, ())):
        cid = candidate_id(tool, provider)
        if known.get(cid):  # a backend: a vendor mode is not run by name
            ranked.append(provider)
        else:
            ignored[cid] = (MSG_MODE_NOT_RUN if cid in known else MSG_UNKNOWN_ID).format(id=cid)
    return tuple(ranked)


def _never_empty(
    tool: str,
    request: RouteRequest,
    ranked: Sequence[str],
    excluded: dict[str, str],
    static: Mapping[Any, Any],
    ignored: dict[str, str],
) -> tuple[tuple[str, ...], dict[str, str]]:
    """Rule 3: the order and the exclusions kept (:func:`.merge_attempts.kept_exclusions`), the ignored noted."""
    kept = kept_exclusions(request.candidates, tool, ranked, excluded)
    if dropped := [cid for cid in excluded if cid not in kept]:
        ignored[NOTE_EXCLUDE] = MSG_EXCLUSIONS_EMPTY.format(ids=", ".join(dropped), tool=tool)
    return _order(tool, request, ranked, kept, static), kept


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
    known = {c.id: c.is_backend for c in request.candidates}
    ignored: dict[str, str] = {}
    excluded = _exclusions(request, decision, ignored)
    asked = _tool(request, decision, excluded, ignored)
    tool = cast(ToolName, extract_or_search(asked, request, decision, caller_set, url_check, ignored))
    while True:  # at most twice: an extract that lost its URLs to the checks runs once more as search
        ranked = _ranked(tool, request, decision, known, ignored)
        providers, kept = _never_empty(tool, request, ranked, excluded, static_order, ignored)
        base = (requests or {}).get(tool) or prompt_request(tool, request)
        on = backends(request, tool, providers)
        moded = only_in_modes(request, tool, providers)
        params = checked_params(tool, decision, on, base, request.budget, caller_set, url_check, ignored, moded)
        if not lost_its_urls(tool, request, params, ignored):
            return settled(tool, ranked, providers, kept, params, ignored, decision.reason)
        tool = LAST_RESORT_TOOL
