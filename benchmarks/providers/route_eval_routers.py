"""route-eval's routers: the static order, the oracle, one backend always, and a router of your own.

A yardstick router takes a :class:`~route_eval_sets.Question` and returns the
candidate ids best first; your own takes a ``RouteRequest`` and returns a
``RouteDecision``, through :func:`adapt`. Imported as a
sibling, by name, as ``_common`` is.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from _tools_effort_plan import EXTRACT
from route_eval_merge import request_for
from route_eval_paths import MIXED, RESEARCH, path_router
from route_eval_rank import cost_key, cost_of, latency_key
from route_eval_sets import Question

from web_router.constants import (
    AUTO_APPENDED,
    AUTO_EXTRACT_APPENDED,
    AUTO_EXTRACT_PREFERRED,
    AUTO_PREFERRED,
    AUTO_RESEARCH_PREFERRED,
)
from web_router.constants_answer import AUTO_ANSWER_PREFERRED
from web_router.providers.auto import AutoExtractProvider, AutoProvider
from web_router.providers.auto_answer import AutoAnswerProvider
from web_router.routing.contract import RouteRequest

STATIC = "static"
ORACLE = "oracle"
ALWAYS = "always:"
FEATURES = "features"

STATIC_ORDER: dict[str, tuple[str, ...]] = {
    "search": tuple(name for name, _ in AutoProvider.default_chain(frozenset((*AUTO_PREFERRED, *AUTO_APPENDED)))),
    "answer": tuple(name for name, _ in AutoAnswerProvider.default_chain(frozenset(AUTO_ANSWER_PREFERRED))),
    EXTRACT: tuple(
        name
        for name, _ in AutoExtractProvider.default_chain(frozenset((*AUTO_EXTRACT_PREFERRED, *AUTO_EXTRACT_APPENDED)))
    ),
    RESEARCH: AUTO_RESEARCH_PREFERRED,
}
"""Today's ``auto`` order per method, built as ``auto`` builds it with a key for every backend: what a router must beat.

For search that is the preferred backends, then the keyless tier, then the
backends appended since the last reordering. Extract and research are only
read on a mixed set, to order its read and research paths.
"""

MSG_BAD_ROUTER = (
    "{spec!r}: a router is static, oracle, always:<backend> (always:<path> on a mixed set), module:attr or "
    "path/file.py:attr"
)

Router = Callable[[Question], Any]
"""What the replay calls: a question in; a ``RouteDecision`` or a ranking, either with a stated cost, out."""
UserRouter = Callable[[RouteRequest], Any]
"""A router of your own: a ``RouteRequest`` in, a ``RouteDecision`` (or ``{"decision", "cost_usd"}``) out."""


def static_router(method: str) -> Router:
    """Today's ``auto`` order; on a mixed set, search then read, as a caller with no router does."""
    if method == MIXED:
        return path_router(STATIC_ORDER)
    order = STATIC_ORDER.get(method, ())

    def route(q: Question) -> list[str]:
        return [p for p in order if p in q.rows] + [p for p in q.candidates if p not in order]

    return route


def oracle_router(method: str) -> Router:
    """The backend that got each question right, cheapest then fastest; the static order when none did."""
    static = static_router(method)

    def route(q: Question) -> list[str]:
        right = [p for p, r in q.rows.items() if r["error"] is None and r["grade"] == "correct"]
        right.sort(key=lambda p: (cost_key(cost_of(q.rows[p])), latency_key(q.rows[p]["latency_s"]), p))
        return right + [p for p in static(q) if p not in right]

    return route


def always_router(backend: str) -> Router:
    return lambda q: [backend]


def adapt(user: UserRouter) -> Router:
    """A router of the library's contract as a replay router: each question shown to it as a ``RouteRequest``."""

    def route(q: Question) -> Any:
        return user(request_for(q))

    if hasattr(user, "fit"):
        route.fit = user.fit  # type: ignore[attr-defined]
    return route


def load_router(spec: str, method: str) -> Router:
    """A router named on the command line."""
    if spec == STATIC:
        return static_router(method)
    if spec == ORACLE:
        return oracle_router(method)
    if spec.startswith(ALWAYS):
        return always_router(spec.removeprefix(ALWAYS))
    if spec == FEATURES:
        from web_router.routing.features import FeaturesRouter

        return adapt(FeaturesRouter().route)
    target, sep, attr = spec.rpartition(":")
    if not sep or not target or not attr:
        raise SystemExit(MSG_BAD_ROUTER.format(spec=spec))
    if target.endswith(".py"):
        module_spec = importlib.util.spec_from_file_location(Path(target).stem, target)
        if module_spec is None or module_spec.loader is None:
            raise SystemExit(MSG_BAD_ROUTER.format(spec=spec))
        module = importlib.util.module_from_spec(module_spec)
        sys.modules[module_spec.name] = module  # registered while it runs: a dataclass looks its module up by name
        module_spec.loader.exec_module(module)
    else:
        module = importlib.import_module(target)
    return adapt(getattr(module, attr))
