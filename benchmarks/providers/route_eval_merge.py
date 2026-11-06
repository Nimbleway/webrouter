"""route-eval's bridge to the library's merge rule: a question as a ``RouteRequest``, an answer as the order walked.

A router's ``RouteDecision`` goes through :func:`web_router.routing.merge.merge`,
the same function a routed call runs, so the replay walks what production would
try: the router's ranking first, unknown ids dropped, then the rest of the
candidates in the static order. A ranking the yardstick routers return (a list
of ids) goes through it too, as the decision ``providers={method: ranking}``;
on a mixed set, whose candidates are paths across methods, a ranking is walked
as it is, then the static order, since a decision never crosses methods.
Imported as a sibling, by name, as ``_common`` is.

On a mixed set, a question given its pages offers ``extract``, but its frozen
rows do not keep the page URLs, so the request names the placeholder
:data:`FROZEN_PAGES` in their place. A merged research backend runs at the
router's effort, else at its own default effort, as a real call would; with no
frozen row at that effort it cannot be scored, and counts as an unscorable miss
in its place in the order. A decision never leaves its method, not even when it
failed or decided nothing: the replay walks that method's static order, as a
routed call would.

Experimental until 1.0, like the rest of route-eval.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any

from _tools_effort_plan import ANSWER, EXTRACT, RESEARCH, SEARCH_EXTRACT
from route_eval_paths import MIXED, SEP

from web_router import prices
from web_router.routing.contract import Candidate, RouteDecision, RouteRequest
from web_router.routing.merge import FallbackReason, Merged, merge

if TYPE_CHECKING:
    from route_eval_sets import Question

TOOL_OF_PATH = {SEARCH_EXTRACT: "search", EXTRACT: "extract", ANSWER: "answer", RESEARCH: "research"}
"""A path's method as the tool a decision names: search then read is a ``search``."""
FROZEN_PAGES = "frozen:pages"
"""The URL a mixed set's request names when the question was given its pages: the frozen rows keep no URLs."""


def method_of(q: Question) -> str:
    """The question's method, or ``mixed`` when its candidates are paths."""
    if any(SEP in candidate for candidate in q.rows):
        return MIXED
    return str(next(iter(q.rows.values()))["method"])


def _pairs(q: Question, method: str) -> dict[tuple[str, str], None]:
    """Each (tool, provider) the question has a row for, in candidate order."""
    if method != MIXED:
        return dict.fromkeys((method, p) for p in q.candidates)
    return dict.fromkeys((TOOL_OF_PATH[path.split(SEP)[0]], path.split(SEP)[1]) for path in q.candidates)


def _candidate(tool: str, provider: str) -> Candidate:
    latency = prices.estimate_latency(provider, tool)
    return Candidate(
        tool=tool,  # type: ignore[arg-type]  # a frozen set's method is a tool by construction
        provider=provider,
        est_cost_usd=prices.estimate(provider, tool),
        est_latency_s=None if latency is None else latency[1],
    )


def request_for(q: Question) -> RouteRequest:
    """What a router is shown for ``q``: one candidate per (tool, provider), the method pinned on a one-method set.

    The prompt is empty unless ``--joined`` gave the question's text; route-eval never times a router out.
    """
    method = method_of(q)
    pairs = _pairs(q, method)
    given_pages = method == MIXED and any(tool == EXTRACT for tool, _ in pairs)
    return RouteRequest(
        prompt=q.prompt or "",
        candidates=tuple(_candidate(tool, provider) for tool, provider in pairs),
        deadline_s=math.inf,
        urls=(FROZEN_PAGES,) if given_pages else (),
        pinned_tool=None if method == MIXED else method,  # type: ignore[arg-type]
    )


def _by_tool(static: Sequence[str], method: str) -> dict[str, list[str]]:
    """The static order as the merge reads it: each tool's providers, in the order the static walk meets them."""
    if method != MIXED:
        return {method: list(static)}
    order: dict[str, dict[str, None]] = {}
    for path in static:
        kind, provider, *_ = path.split(SEP)
        order.setdefault(TOOL_OF_PATH[kind], {})[provider] = None
    return {tool: list(providers) for tool, providers in order.items()}


def _mode_wanted(merged: Merged, provider: str) -> tuple[str, ...] | None:
    """The research mode a call runs: the router's effort, else the backend's default, as a real call would.

    ``()`` is the call that sends no effort; ``None`` means any mode, for a method other than research.
    """
    if merged.tool != RESEARCH:
        return None
    effort = merged.params.get("effort")
    if effort is None:
        row = prices.price(provider, RESEARCH, None)
        effort = None if row is None else row.mode
    return () if effort is None else (effort,)


def _path_of(merged: Merged, provider: str, static: Sequence[str]) -> str | None:
    """``provider``'s path at the mode a real call would run; ``None`` when the frozen set has no row for it."""
    wanted = _mode_wanted(merged, provider)
    for path in static:
        kind, name, *mode = path.split(SEP)
        if TOOL_OF_PATH[kind] == merged.tool and name == provider and wanted in (None, tuple(mode)):
            return path
    return None


def walk(
    answer: Any, q: Question, fallback_order: Callable[[Question], list[str]], failure: FallbackReason | None
) -> tuple[list[str | None], bool]:
    """The ids the replay tries for ``answer``, in order, and whether the router failed.

    A ranking (a list) fails when it names no candidate; a decision fails when the merge set it aside. On a
    mixed set a decision -- failed or empty too -- walks only the merged method's backends, as a routed call
    would; one with no frozen row to score is ``None`` in its place, a miss the replay counts as unscorable.
    """
    static = fallback_order(q)
    method = method_of(q)
    if isinstance(answer, list) and method == MIXED:
        known = [p for p in dict.fromkeys(answer) if p in q.rows]
        return list(dict.fromkeys((*known, *static))), not known
    ranking = isinstance(answer, list)
    decision = RouteDecision(providers={method: answer}) if ranking else answer  # type: ignore[dict-item]
    merged = merge(request_for(q), decision, _by_tool(static, method), failure=failure)  # type: ignore[arg-type]
    failed = merged.notes.fallback is not None or (ranking and not merged.notes.ranked)
    if method != MIXED:
        return list(merged.providers), failed
    return [_path_of(merged, provider, static) for provider in merged.providers], failed
