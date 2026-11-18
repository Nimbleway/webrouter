"""What a router that did not opt in to vendor modes is shown, and how many candidates a remote request carries.

Experimental. A router is shown a candidate per vendor mode only when it opts in (``route_modes``). The
``fallback`` router shows each member the request it would see alone, so :func:`backends_only` strips the mode
candidates for a member that did not; the ``remote`` router strips them unless its ``router_config`` opts in.
:func:`within` holds a request to the wire's limit of entries in one list, across every method: the mode
candidates go first, the lowest in their method's static order first, and the rest keep their order.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace

from web_router.routing.contract import Candidate, RouteRequest


def backends(candidates: Sequence[Candidate]) -> tuple[Candidate, ...]:
    """The backends among ``candidates``, in their order: the vendor modes left out."""
    return tuple(candidate for candidate in candidates if candidate.is_backend)


def backends_only(req: RouteRequest) -> RouteRequest:
    """``req`` without its vendor mode candidates: what a router that did not opt in is shown."""
    kept = backends(req.candidates)
    return req if len(kept) == len(req.candidates) else replace(req, candidates=kept)


def shown(req: RouteRequest, modes: bool) -> RouteRequest:
    """What a router is shown: ``req`` when it opts in to vendor modes, its backends only when it does not."""
    return req if modes else backends_only(req)


def runnable(candidates: Sequence[Candidate]) -> bool:
    """Whether ``candidates`` hold anything to run: a backend, or a vendor mode a router's ``attempts`` can name."""
    return bool(candidates)


def within(req: RouteRequest, limit: int) -> RouteRequest:
    """``req`` with at most ``limit`` candidates: the mode candidates lowest in their method's order dropped first."""
    if len(req.candidates) <= limit:
        return req
    keep = set(sorted(range(len(req.candidates)), key=_rank(req.candidates))[:limit])
    return replace(req, candidates=tuple(c for at, c in enumerate(req.candidates) if at in keep))


def _rank(candidates: tuple[Candidate, ...]) -> Callable[[int], tuple[bool, int, int]]:
    """The sort key of a candidate's index: backends before modes, then its place in its method's static order."""
    places: dict[str, int] = {}
    place: list[int] = []
    for candidate in candidates:
        place.append(places.get(candidate.tool, 0))
        places[candidate.tool] = place[-1] + 1
    return lambda at: (not candidates[at].is_backend, place[at], at)
