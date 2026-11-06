"""The merge rule: a router's decision and the static order, made into one method, one order and checked options.

Experimental. :func:`merge` is pure -- no I/O, no clock, nothing read but the
price list and the effort table -- so route-eval replays a decision exactly as
a routed call would run it. The rules:

1. **Method.** The pinned one, when the caller called a method; else the
   first of ``decision.tools`` that still has a candidate; else ``search``.
   It is chosen once: a fallback never moves to another method.
2. **Order.** The backends the router ranked for that method, in its order,
   then every other candidate in the static order.
3. **Exclusions.** Only ``decision.exclude`` removes a candidate, each with
   its reason. Exclusions that would leave the method no backend are ignored.
4. **Unknown ids** -- a method, a backend or an exclusion that is not a
   candidate -- are set aside and noted, never an error.
5. **Options.** ``decision.params`` for the method, by
   :func:`~web_router.routing.merge_params.checked_params`: only the keys on
   :data:`~web_router.constants_merge.ROUTER_PARAMS`, valid values, none the
   caller set, and nothing that does not fit the budget. What is dropped is
   noted, and the caller's options and the budget's mapping apply instead.
6. **No decision.** A router that timed out, raised, or answered with
   anything but a well-formed :class:`~web_router.routing.RouteDecision` gets
   the static order, with the reason.
7. **Extract with no URL** searches instead, and says so.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, cast, get_args

from web_router.constants_merge import (
    LAST_RESORT_TOOL,
    MSG_EXCLUSIONS_EMPTY,
    MSG_EXTRACT_NO_URL,
    MSG_UNKNOWN_ID,
    MSG_UNKNOWN_TOOL,
    NOTE_EXCLUDE,
    NOTE_EXTRACT,
    ROUTER_INVALID,
    ROUTER_TIMEOUT,
)
from web_router.constants_routing import CANDIDATE_ID_SEPARATOR
from web_router.routing.contract import RouteDecision, RouteRequest, ToolName
from web_router.routing.merge_params import UrlCheck, checked_params, router_urls

FallbackReason = Literal["router_timeout", "router_error", "router_invalid"]
"""Why the static order was used instead of a decision."""

TOOLS: tuple[str, ...] = get_args(ToolName)

__all__ = ["FallbackReason", "MergeNotes", "Merged", "UrlCheck", "merge"]


@dataclass(frozen=True, kw_only=True)
class MergeNotes:
    """How a decision was merged: what the decision record carries, and what of the decision was set aside."""

    ranked: tuple[str, ...] = ()
    """The router's ranking for the method, as candidate ids, unknown ones left out."""

    merged: tuple[str, ...] = ()
    """The order to try, as candidate ids."""

    excluded: Mapping[str, str] = field(default_factory=dict)
    """The candidates the router excluded, each with its reason."""

    ignored: Mapping[str, str] = field(default_factory=dict)
    """What of the decision was set aside, and why: an unknown id, an option as ``tool.key``, the exclusions."""

    fallback: FallbackReason | None = None
    """Why the static order was used instead of the decision; ``None`` when the decision was merged."""

    reason: str | None = None
    """The router's own reason."""


@dataclass(frozen=True, kw_only=True)
class Merged:
    """A decision merged: the method to call, the backends to try in order, and the options a router set."""

    tool: ToolName
    providers: tuple[str, ...]
    """Backend names, in the order to try them."""

    params: Mapping[str, Any]
    """The router's options that passed every check. Empty, the caller's options and the budget's mapping apply."""

    notes: MergeNotes

    def record(self) -> dict[str, Any]:
        """The :class:`~web_router.DecisionRecord` fields the merge fills."""
        notes = self.notes
        return {
            "ranked": list(notes.ranked) or None,
            "merged": list(notes.merged),
            "excluded": dict(notes.excluded) or None,
            "reason": notes.fallback or notes.reason,
            "router_timeout": True if notes.fallback == ROUTER_TIMEOUT else None,
            "params": dict(self.params) or None,
            "params_source": "router" if self.params else None,
        }


def _id(tool: str, provider: str) -> str:
    return f"{tool}{CANDIDATE_ID_SEPARATOR}{provider}"


def _names(value: object) -> bool:
    """A list or tuple of strings: a bare string is a sequence too, and never a ranking."""
    return isinstance(value, list | tuple) and all(isinstance(v, str) for v in value)


def _well_formed(decision: object) -> bool:
    """Whether ``decision`` is a :class:`RouteDecision` whose fields have the shapes the contract gives them."""
    if not isinstance(decision, RouteDecision):
        return False
    providers, params, exclude = decision.providers, decision.params, decision.exclude
    return (
        (decision.tools is None or _names(decision.tools))
        and (providers is None or (isinstance(providers, Mapping) and all(map(_names, providers.values()))))
        and isinstance(exclude, Mapping)
        and all(isinstance(k, str) and isinstance(v, str) for k, v in exclude.items())
        and (params is None or (isinstance(params, Mapping) and all(isinstance(p, Mapping) for p in params.values())))
    )


def _order(
    tool: str, request: RouteRequest, ranked: Sequence[str], excluded: Collection[str], static: Mapping[Any, Any]
) -> tuple[str, ...]:
    """``ranked`` first, then the static order, then any candidate the static order leaves out; none excluded."""
    names = [c.provider for c in request.candidates if c.tool == tool and c.id not in excluded]
    pool = set(names)
    return tuple(dict.fromkeys(p for p in (*ranked, *static.get(tool, ()), *names) if p in pool))


def _static(request: RouteRequest, static: Mapping[Any, Any], why: FallbackReason) -> Merged:
    tool = request.pinned_tool or LAST_RESORT_TOOL
    providers = _order(tool, request, (), (), static)
    notes = MergeNotes(merged=tuple(_id(tool, p) for p in providers), fallback=why)
    return Merged(tool=tool, providers=providers, params={}, notes=notes)


def _exclusions(decision: RouteDecision, known: Collection[str], ignored: dict[str, str]) -> dict[str, str]:
    excluded = {}
    for cid, why in decision.exclude.items():
        if cid in known:
            excluded[cid] = why
        else:
            ignored[cid] = MSG_UNKNOWN_ID.format(id=cid)
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
    return LAST_RESORT_TOOL


def _ranked(tool: str, decision: RouteDecision, known: Collection[str], ignored: dict[str, str]) -> tuple[str, ...]:
    """The router's backends for ``tool``, best first; an unknown one noted and left out."""
    asked: Mapping[Any, Sequence[str]] = decision.providers or {}
    ranked = []
    for provider in dict.fromkeys(asked.get(tool, ())):
        if _id(tool, provider) in known:
            ranked.append(provider)
        else:
            ignored[_id(tool, provider)] = MSG_UNKNOWN_ID.format(id=_id(tool, provider))
    for other in asked:
        if other not in TOOLS:
            ignored[other] = MSG_UNKNOWN_TOOL.format(tool=other)
    return tuple(ranked)


def merge(
    request: RouteRequest,
    decision: object,
    static_order: Mapping[ToolName, Sequence[str]],
    *,
    failure: FallbackReason | None = None,
    caller_set: Collection[str] = (),
    url_check: UrlCheck | None = None,
) -> Merged:
    """``decision`` for ``request``, merged with ``static_order`` by the rules above.

    ``static_order`` is each method's backends as ``auto`` would try them.
    ``failure`` says the router timed out or raised; ``decision`` is then not
    read. ``caller_set`` names the options the caller set, which a router never
    changes. ``url_check`` is the URL policy: it returns why a URL is refused,
    ``None`` when it is allowed. With none, a router sets no URL.
    """
    if failure is None and not _well_formed(decision):
        failure = ROUTER_INVALID
    if failure is not None:
        return _static(request, static_order, failure)
    decision = cast(RouteDecision, decision)
    known = {c.id for c in request.candidates}
    ignored: dict[str, str] = {}
    excluded = _exclusions(decision, known, ignored)
    tool = _tool(request, decision, excluded, ignored)
    no_url = request.pinned_tool is None and not request.urls and not router_urls(decision, url_check)
    if tool == "extract" and no_url:
        ignored[NOTE_EXTRACT] = MSG_EXTRACT_NO_URL
        tool = LAST_RESORT_TOOL
    ranked = _ranked(tool, decision, known, ignored)
    providers = _order(tool, request, ranked, excluded, static_order)
    mine = [cid for cid in excluded if cid.startswith(_id(tool, ""))]
    if not providers and mine:
        ignored[NOTE_EXCLUDE] = MSG_EXCLUSIONS_EMPTY.format(ids=", ".join(mine), tool=tool)
        excluded = {cid: why for cid, why in excluded.items() if cid not in mine}
        providers = _order(tool, request, ranked, (), static_order)
    params = checked_params(tool, decision, providers, request.budget, caller_set, url_check, ignored)
    notes = MergeNotes(
        ranked=tuple(_id(tool, p) for p in ranked),
        merged=tuple(_id(tool, p) for p in providers),
        excluded=excluded,
        ignored=ignored,
        reason=decision.reason,
    )
    return Merged(tool=cast(ToolName, tool), providers=providers, params=params, notes=notes)
