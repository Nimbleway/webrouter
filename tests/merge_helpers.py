"""What the merge rule's tests share: the candidates offered, the static order, and a merge over them."""

from __future__ import annotations

from typing import Any

from web_router import Budget, Candidate, RouteRequest
from web_router.routing.merge import Merged, merge

PAIRS = (
    ("search", "exa"),
    ("search", "brave"),
    ("search", "tavily"),
    ("search", "nimble"),
    ("research", "nimble"),
    ("research", "parallel"),
    ("answer", "perplexity"),
    ("extract", "jina"),
    ("browse", "skyvern"),
)
STATIC = {
    "search": ("tavily", "brave", "exa", "nimble"),
    "research": ("parallel", "nimble"),
    "answer": ("perplexity",),
    "extract": ("jina",),
    "browse": ("skyvern",),
}


def request_for(pinned: Any = None, budget: Budget | None = None, urls: tuple[str, ...] = (), **_: Any) -> RouteRequest:
    """A request offering every pair in :data:`PAIRS`."""
    candidates = tuple(Candidate(tool=t, provider=p) for t, p in PAIRS)  # type: ignore[arg-type]
    return RouteRequest(prompt="q", candidates=candidates, deadline_s=1.0, urls=urls, budget=budget, pinned_tool=pinned)


def merge_with(decision: object, **kwargs: Any) -> Merged:
    """``decision`` merged with :data:`STATIC` over :func:`request_for`; the merge's own options pass through."""
    options = {k: kwargs.pop(k) for k in ("failure", "caller_set", "url_check") if k in kwargs}
    return merge(request_for(**kwargs), decision, STATIC, **options)
