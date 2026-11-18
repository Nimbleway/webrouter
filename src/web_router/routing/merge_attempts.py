"""A decision's ``attempts``: the candidates to try, best first, which replace its ``tools`` and ``providers``.

Experimental. Apart from :mod:`.merge` so each stays small. An attempt names a
candidate by id, a vendor mode's included (``search:exa:exa-instant``). The
merged order names each as an *entry*: its backend, or with a mode
``exa:exa-instant``, which ``auto`` runs as that backend in that mode
(:func:`~web_router.routing.attempts.in_call_order`). One backend may so run in
several modes. Excluding a backend's id excludes its modes too; excluding a
mode's id excludes that mode only.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence

from web_router.constants_merge import MSG_UNKNOWN_ID
from web_router.constants_routing import CANDIDATE_ID_SEPARATOR
from web_router.routing.contract import Candidate, RouteDecision, RouteRequest, candidate_id


def entry(candidate: Candidate) -> str:
    """How the merged order names ``candidate``: its backend, or ``backend:mode`` for a vendor mode."""
    if candidate.is_backend:
        return candidate.provider
    return f"{candidate.provider}{CANDIDATE_ID_SEPARATOR}{candidate.mode}"


def backends(request: RouteRequest, tool: str, names: Sequence[str]) -> tuple[str, ...]:
    """The backends ``names``, entries in the merged order, run on, each once: ``exa`` for ``exa:exa-instant``."""
    by_entry = {entry(c): c.provider for c in request.candidates if c.tool == tool}
    return tuple(dict.fromkeys(by_entry.get(name, name) for name in names))


def is_excluded(candidate: Candidate, excluded: Collection[str]) -> bool:
    """Whether ``excluded`` names ``candidate``, or for a vendor mode, its backend."""
    return candidate.id in excluded or candidate_id(candidate.tool, candidate.provider) in excluded


def entries(request: RouteRequest, tool: str, excluded: Collection[str]) -> dict[str, Candidate]:
    """``tool``'s candidates that are not excluded, by entry, in the request's order."""
    return {entry(c): c for c in request.candidates if c.tool == tool and not is_excluded(c, excluded)}


def first_tool(request: RouteRequest, decision: RouteDecision, excluded: Collection[str]) -> str | None:
    """The method of the first attempt that names a candidate not excluded; ``None`` when none does."""
    known = {c.id: c for c in request.candidates}
    for cid in decision.attempts or ():
        found = known.get(cid)
        if found is not None and not is_excluded(found, excluded):
            return found.tool
    return None


def ranked_attempts(
    tool: str, request: RouteRequest, decision: RouteDecision, ignored: dict[str, str]
) -> tuple[str, ...]:
    """The decision's attempts for ``tool``, as entries, best first; an unknown id noted and left out."""
    known = {c.id: c for c in request.candidates}
    ranked = []
    for cid in dict.fromkeys(decision.attempts or ()):
        found = known.get(cid)
        if found is None:
            ignored[cid] = MSG_UNKNOWN_ID.format(id=cid)
        elif found.tool == tool:
            ranked.append(entry(found))
    return tuple(ranked)
