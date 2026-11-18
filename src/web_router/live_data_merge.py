"""Merging the pages several live data backends answered into one order.

A listing takes each backend's sources in turn; a query's matches are merged
on :func:`~web_router.live_data_rank.score`, keeping each backend's own order.
Kept out of :mod:`web_router.live_data_any`, which asks the backends.
"""

from __future__ import annotations

from collections.abc import Iterable

from web_router.live_data_rank import Query, score
from web_router.types_live_data import LiveDataSource


def in_turn(lists: Iterable[list[LiveDataSource]]) -> Iterable[LiveDataSource]:
    """The first of each list, then the second of each, and so on: no backend crowds out the others."""
    rows = [list(sources) for sources in lists]
    for index in range(max((len(sources) for sources in rows), default=0)):
        yield from (sources[index] for sources in rows if index < len(sources))


def by_score(lists: list[list[LiveDataSource]], asked: Query) -> Iterable[LiveDataSource]:
    """Each backend's sources in its own order, merged by taking the best-scored next source of any of them.

    A backend's own order is kept -- a local search's is already by score, a
    vendor's own search is in the vendor's order -- so the first ``n`` merged
    depend only on each backend's first ``n``: the same sources come back
    whatever page is asked, with no repeat or gap between pages. Ties go to the
    earlier position, then the earlier backend, as taking turns would.
    """
    scores = [[score(source, asked) for source in sources] for sources in lists]
    at = [0] * len(lists)
    while left := [index for index, sources in enumerate(lists) if at[index] < len(sources)]:
        best = max(left, key=lambda index: (scores[index][at[index]], -at[index], -index))
        yield lists[best][at[best]]
        at[best] += 1


__all__: list[str] = ["by_score", "in_turn"]
