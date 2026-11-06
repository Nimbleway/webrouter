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
:data:`FROZEN_PAGES` in their place. A merged research backend with no row at
the effort the router chose cannot be scored: it is passed over and counted
as unscorable, and the walk goes on.

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


def _path_of(merged: Merged, provider: str, static: Sequence[str]) -> str | None:
    """``provider``'s path: the first in the static order, at the router's research effort when it set one."""
    effort = merged.params.get("effort")
    for path in static:
        kind, name, *mode = path.split(SEP)
        if TOOL_OF_PATH[kind] == merged.tool and name == provider and effort in (None, *mode):
            return path
    return None


def _paths(merged: Merged, static: Sequence[str]) -> tuple[list[str], int]:
    """Each merged provider's path, and how many had none to score (a research backend with no row at the effort)."""
    found = [_path_of(merged, provider, static) for provider in merged.providers]
    return [p for p in found if p is not None], found.count(None)


def walk(
    answer: Any, q: Question, fallback_order: Callable[[Question], list[str]], failure: FallbackReason | None
) -> tuple[list[str], bool, int]:
    """The ids the replay tries for ``answer``, in order, whether the router failed, and how many were unscorable.

    A ranking (a list) fails when it names no candidate; a decision fails when the merge set it aside. A failed
    or empty decision on a mixed set walks the whole static path order, as the static yardstick does.
    """
    static = fallback_order(q)
    method = method_of(q)
    if isinstance(answer, list) and method == MIXED:
        known = [p for p in dict.fromkeys(answer) if p in q.rows]
        return list(dict.fromkeys((*known, *static))), not known, 0
    ranking = isinstance(answer, list)
    decision = RouteDecision(providers={method: answer}) if ranking else answer  # type: ignore[dict-item]
    merged = merge(request_for(q), decision, _by_tool(static, method), failure=failure)  # type: ignore[arg-type]
    failed = merged.notes.fallback is not None or (ranking and not merged.notes.ranked)
    if method != MIXED:
        return list(merged.providers), failed, 0
    if merged.notes.fallback or merged.notes.empty:
        return list(static), failed, 0
    paths, unscorable = _paths(merged, static)
    return paths, failed, unscorable
