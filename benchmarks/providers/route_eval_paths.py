"""route-eval on a mixed set: each candidate is a path -- method, backend and mode -- rather than a backend.

A mixed set (``tools-effort-60``, made by ``tools_effort.py``) asks every
question through several methods: search then read, extract, ``answer()`` and
``research()`` at each effort. A router there picks the tool and the effort,
so a candidate is a path id, ``method:backend[:mode]``, as
``research:parallel:low``. Imported as a sibling, by name, as ``_common`` is.

Experimental until 1.0, like the rest of route-eval.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, get_args

from _frozen_checks import DEFAULT_MODE
from _frozen_mixed import MIXED as MIXED
from _tools_effort_plan import PATHS as PATH_METHODS
from _tools_effort_plan import RESEARCH, SEARCH_EXTRACT

from web_router.types import ResearchEffort

if TYPE_CHECKING:
    from route_eval_sets import Question

DEFAULT_PATH_ROUTERS = ("always:research:parallel:low", "always:answer:perplexity")
"""The example routers on a mixed set; ``static`` and ``oracle`` are always scored, as the yardsticks."""
EFFORTS: tuple[str, ...] = get_args(ResearchEffort)
"""Research efforts, lowest first: the static order tries the cheaper effort before the dearer."""
SEP = ":"

MIXED_INTRO = (
    "A candidate here is a path: method, backend and mode (a research row's mode is its effort), as "
    "`research:parallel:low`. A question's candidates are the paths it has rows for, so `extract` is one only on "
    "the questions given their pages. The static order is what a caller with no router does today: `search` "
    "through `auto`'s order, then read the top pages -- the first `search+extract` path; the other paths follow "
    "only as its fallback (extract, answer, then research, each by its `auto` order, research lowest effort first). "
    "The oracle is the path that got each question right, cheapest then fastest. With {held_out} questions held "
    "out, one question moves accuracy by {step:.1f} points: read the 95% interval before any margin.\n"
)
EFFORT_INTRO = (
    "### Research by effort, over every question\n\n"
    "Each research backend at each effort the set covers: correct out of the questions counted, a failed row "
    "counting as a miss, the mean cost of the rows it served, and the median latency. A row that failed during the "
    "run with a rate-limit or quota error is left out of the count, as in the order above, and shown apart: a small "
    "denominator is shown as such. Over all questions, not a router's score: the evidence for mapping a budget to "
    "an effort.\n"
)


def is_mixed(methods: Iterable[str]) -> bool:
    """Whether a set's row methods make it a mixed set: more than one, every one a path's."""
    found = set(methods)
    return len(found) > 1 and found <= set(PATH_METHODS)


def path_id(row: Mapping[str, Any]) -> str:
    """A mixed set's candidate: ``method:backend``, with ``:mode`` when the mode is not the default."""
    mode = () if row["mode"] == DEFAULT_MODE else (row["mode"],)
    return SEP.join((row["method"], row["provider"], *mode))


def path_key(path: str, orders: Mapping[str, Sequence[str]]) -> tuple[int, int, int, str]:
    """Where ``path`` sorts in the static order: by method, then the method's ``auto`` order, then effort."""
    method, provider, *mode = path.split(SEP)
    order = orders.get("search" if method == SEARCH_EXTRACT else method, ())
    effort = mode[0] if mode else ""
    return (
        PATH_METHODS.index(method) if method in PATH_METHODS else len(PATH_METHODS),
        order.index(provider) if provider in order else len(order),
        EFFORTS.index(effort) if effort in EFFORTS else -1,
        path,
    )


def path_router(orders: Mapping[str, Sequence[str]]) -> Callable[[Question], list[str]]:
    """The static order on a mixed set: search then read first, the other paths only as its fallback."""
    return lambda q: sorted(q.rows, key=lambda p: path_key(p, orders))


def research_by_effort(
    backends: Mapping[str, Mapping[str, Any]], orders: Mapping[str, Sequence[str]]
) -> list[tuple[str, str, Mapping[str, Any]]]:
    """Each research path's ``(backend, effort, stats)``, in the static order: the effort table's rows."""
    research = sorted((p for p in backends if p.split(SEP)[0] == RESEARCH), key=lambda p: path_key(p, orders))
    rows = []
    for p in research:
        _, provider, *mode = p.split(SEP)
        rows.append((provider, mode[0] if mode else DEFAULT_MODE, backends[p]))
    return rows
