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
   the static order, with the reason. So does a decision that decides
   nothing, recorded as ``router_empty`` and not as a failure.
7. **Extract with no URL** searches instead, and says so.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, TypeGuard, cast, get_args

from web_router._urls import shown
from web_router.constants_merge import (
    EXTRACT_TOOL,
    LAST_RESORT_TOOL,
    MSG_EXCLUSIONS_EMPTY,
    MSG_EXTRACT_NO_URL,
    MSG_TOOL_NO_CANDIDATE,
    MSG_UNKNOWN_ID,
    MSG_UNKNOWN_TOOL,
    NOTE_EXCLUDE,
    NOTE_EXTRACT,
    NOTE_EXTRACT_URLS,
    ROUTER_EMPTY,
    ROUTER_INVALID,
    ROUTER_TIMEOUT,
)
from web_router.constants_params import PARAMS_SOURCE_ROUTER
from web_router.routing.contract import RouteDecision, RouteRequest, ToolName, candidate_id
from web_router.routing.merge_params import (
    UrlCheck,
    checked_params,
    hide_urls,
    router_url_refusal,
    router_urls,
)

FallbackReason = Literal["router_timeout", "router_error", "router_invalid"]
"""Why the static order was used instead of a decision."""

TOOLS: tuple[str, ...] = get_args(ToolName)

__all__ = ["FallbackReason", "MergeNotes", "Merged", "UrlCheck", "merge", "well_formed"]


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

    empty: bool = False
    """The router answered with a well-formed decision that decides nothing: the static order, and no failure."""

    reason: str | None = None
    """The router's own reason. Here, as in ``excluded``, any URL in a router's words shows as its scheme and host."""


@dataclass(frozen=True, kw_only=True)
class Merged:
    """A decision merged: the method to call, the backends to try in order, and the options a router set."""

    tool: ToolName
    providers: tuple[str, ...]
    """Backend names, in the order to try them."""

    params: Mapping[str, Any]
    """The router's options that passed every check. Empty, the caller's options and the budget's mapping apply.

    URLs are kept whole here, to be called with; the record and the ``repr`` show only their scheme and host."""

    notes: MergeNotes

    def __repr__(self) -> str:
        params = self.record()["params"] or {}
        return f"Merged(tool={self.tool!r}, providers={self.providers!r}, params={params!r}, notes={self.notes!r})"

    def record(self) -> dict[str, Any]:
        """The :class:`~web_router.DecisionRecord` fields the merge fills."""
        notes = self.notes
        return {
            "ranked": list(notes.ranked) or None,
            "merged": list(notes.merged),
            "excluded": dict(notes.excluded) or None,
            "reason": notes.fallback or (ROUTER_EMPTY if notes.empty else notes.reason),
            "router_timeout": True if notes.fallback == ROUTER_TIMEOUT else None,
            "params": shown(self.params) or None,
            "params_source": PARAMS_SOURCE_ROUTER if self.params else None,
        }


def _names(value: object) -> bool:
    """A list or tuple of strings: a bare string is a sequence too, and never a ranking."""
    return isinstance(value, list | tuple) and all(isinstance(v, str) for v in value)


def _by_tool(value: object, check: Callable[[object], bool]) -> bool:
    """``None``, or a mapping keyed by method names whose every value passes ``check``."""
    if value is None:
        return True
    return isinstance(value, Mapping) and all(k in TOOLS and check(v) for k, v in value.items())


def _keyed_by_name(value: object) -> bool:
    """A method's options: a mapping keyed by option names, which are strings."""
    return isinstance(value, Mapping) and all(isinstance(k, str) for k in value)


def well_formed(decision: object) -> TypeGuard[RouteDecision]:
    """Whether ``decision`` is a :class:`RouteDecision` whose fields have the shapes the contract gives them.

    A mapping keyed by anything but one of the five method names is malformed, not an unknown id: the
    contract types those keys, so a router that breaks them is broken, and its decision is set aside whole.
    """
    if not isinstance(decision, RouteDecision):
        return False
    exclude, confidence = decision.exclude, decision.confidence
    return (
        (decision.tools is None or _names(decision.tools))
        and _by_tool(decision.providers, _names)
        and _by_tool(decision.params, _keyed_by_name)
        and isinstance(exclude, Mapping)
        and all(isinstance(k, str) and isinstance(v, str) for k, v in exclude.items())
        and (decision.reason is None or isinstance(decision.reason, str))
        and (confidence is None or _probability(confidence))
    )


def _probability(value: object) -> bool:
    """A finite number from 0 to 1: what ``confidence`` must be when set."""
    return isinstance(value, int | float) and not isinstance(value, bool) and 0 <= value <= 1


def _empty(decision: RouteDecision) -> bool:
    """A decision that decides nothing: no method, no backend, no exclusion and no option."""
    providers, params = decision.providers or {}, decision.params or {}
    return not (decision.tools or any(providers.values()) or decision.exclude or any(params.values()))


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
        if cid in known:
            excluded[cid] = hide_urls(why) or ""
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
    if failure is None and not well_formed(decision):
        failure = ROUTER_INVALID
    if failure is not None:
        return _static(request, static_order, failure, None)
    decision = cast(RouteDecision, decision)
    if _empty(decision):
        return _static(request, static_order, None, decision.reason)
    known = {c.id for c in request.candidates}
    ignored: dict[str, str] = {}
    excluded = _exclusions(decision, known, ignored)
    tool = _tool(request, decision, excluded, ignored)
    no_url = request.pinned_tool is None and not request.urls and not router_urls(decision, url_check, caller_set)
    if tool == EXTRACT_TOOL and no_url:
        ignored[NOTE_EXTRACT] = MSG_EXTRACT_NO_URL
        refused = router_url_refusal(decision, url_check, caller_set)
        if refused is not None:
            ignored[NOTE_EXTRACT_URLS] = refused
        tool = LAST_RESORT_TOOL
    ranked = _ranked(tool, decision, known, ignored)
    providers = _order(tool, request, ranked, excluded, static_order)
    mine = [cid for cid in excluded if cid.startswith(candidate_id(tool, ""))]
    if not providers and mine:
        ignored[NOTE_EXCLUDE] = MSG_EXCLUSIONS_EMPTY.format(ids=", ".join(mine), tool=tool)
        excluded = {cid: why for cid, why in excluded.items() if cid not in mine}
        providers = _order(tool, request, ranked, (), static_order)
    params = checked_params(tool, decision, providers, request.budget, caller_set, url_check, ignored)
    notes = MergeNotes(
        ranked=tuple(candidate_id(tool, p) for p in ranked),
        merged=tuple(candidate_id(tool, p) for p in providers),
        excluded=excluded,
        ignored={hide_urls(key): hide_urls(why) for key, why in ignored.items()},
        reason=hide_urls(decision.reason),
    )
    return Merged(tool=cast(ToolName, tool), providers=providers, params=params, notes=notes)
